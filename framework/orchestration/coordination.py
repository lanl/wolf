"""Coordination patterns for async workflow orchestration.

Provides high-level helpers for common workflow patterns:
- Map-Reduce: Parallel processing with result aggregation
- Pipeline: Sequential task chains with data flow
- Fan-Out/Fan-In: Parallel task execution with synchronization
- Scatter-Gather: Broadcast with result collection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
import asyncio

from .models import TaskSpec, TaskStatus, WaitPolicy, AgentRequirements
from .runtime import AsyncWorkflowRuntime


@dataclass(slots=True)
class CoordinationResult:
    """Result from a coordination pattern execution."""
    success: bool
    results: List[Any] = field(default_factory=list)
    failed_tasks: List[str] = field(default_factory=list)
    task_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class MapReduceCoordinator:
    """Implements the Map-Reduce coordination pattern.
    
    Splits work across multiple parallel tasks (map phase) and aggregates
    results into a single output (reduce phase).
    """
    
    def __init__(self, runtime: AsyncWorkflowRuntime) -> None:
        self.runtime = runtime
    
    async def execute(self,
                     name: str,
                     map_items: List[Any],
                     map_objective: str,
                     reduce_objective: str,
                     workflow_type: str = 'generic',
                     session_id: Optional[str] = None,
                     requirements: Optional[AgentRequirements] = None) -> CoordinationResult:
        """Execute map-reduce pattern.
        
        Args:
            name: Base name for tasks
            map_items: Items to process in parallel (map phase)
            map_objective: Template objective for map tasks (use {item} placeholder)
            reduce_objective: Objective for reduce task
            workflow_type: Workflow type for tasks
            session_id: Session ID for task grouping
            requirements: Agent requirements
            
        Returns:
            CoordinationResult with aggregated results
        """
        # Create map tasks
        map_task_ids = []
        for i, item in enumerate(map_items):
            spec = TaskSpec(
                name=f"{name}_map_{i}",
                objective=map_objective.format(item=item),
                workflow_type=workflow_type,
                session_id=session_id,
                requirements=requirements or AgentRequirements(),
                inputs={'map_item': item, 'map_index': i}
            )
            task_id = await self.runtime.submit_root_task(spec)
            map_task_ids.append(task_id)
        
        # Wait for all map tasks to complete
        map_results = []
        failed_tasks = []
        
        for task_id in map_task_ids:
            task = await self.runtime.get_task(task_id)
            while task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                await asyncio.sleep(0.1)
                task = await self.runtime.get_task(task_id)
            
            if task.status == TaskStatus.COMPLETED:
                map_results.append(task.result)
            else:
                failed_tasks.append(task_id)
        
        # If any map task failed, return early
        if failed_tasks:
            return CoordinationResult(
                success=False,
                results=map_results,
                failed_tasks=failed_tasks,
                task_ids=map_task_ids,
                metadata={'phase': 'map'}
            )
        
        # Create reduce task
        reduce_spec = TaskSpec(
            name=f"{name}_reduce",
            objective=reduce_objective,
            workflow_type=workflow_type,
            session_id=session_id,
            requirements=requirements or AgentRequirements(),
            inputs={'map_results': map_results}
        )
        reduce_task_id = await self.runtime.submit_root_task(reduce_spec)
        
        # Wait for reduce task
        reduce_task = await self.runtime.get_task(reduce_task_id)
        while reduce_task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            await asyncio.sleep(0.1)
            reduce_task = await self.runtime.get_task(reduce_task_id)
        
        return CoordinationResult(
            success=reduce_task.status == TaskStatus.COMPLETED,
            results=[reduce_task.result] if reduce_task.result else [],
            failed_tasks=[reduce_task_id] if reduce_task.status != TaskStatus.COMPLETED else [],
            task_ids=map_task_ids + [reduce_task_id],
            metadata={'phase': 'reduce', 'map_task_count': len(map_items)}
        )


class PipelineCoordinator:
    """Implements the Pipeline coordination pattern.
    
    Executes tasks sequentially, passing output from one stage to the next.
    """
    
    def __init__(self, runtime: AsyncWorkflowRuntime) -> None:
        self.runtime = runtime
    
    async def execute(self,
                     name: str,
                     stages: List[Dict[str, Any]],
                     initial_input: Any = None,
                     workflow_type: str = 'generic',
                     session_id: Optional[str] = None,
                     requirements: Optional[AgentRequirements] = None) -> CoordinationResult:
        """Execute pipeline pattern.
        
        Args:
            name: Base name for pipeline
            stages: List of stage definitions, each with 'name' and 'objective' keys
            initial_input: Input for first stage
            workflow_type: Workflow type for tasks
            session_id: Session ID for task grouping
            requirements: Agent requirements
            
        Returns:
            CoordinationResult with final output
        """
        task_ids = []
        stage_results = []
        current_input = initial_input
        
        for i, stage in enumerate(stages):
            spec = TaskSpec(
                name=f"{name}_stage_{i}_{stage.get('name', f'stage{i}')}",
                objective=stage['objective'],
                workflow_type=workflow_type,
                session_id=session_id,
                requirements=requirements or AgentRequirements(),
                inputs={'stage_input': current_input, 'stage_index': i}
            )
            
            task_id = await self.runtime.submit_root_task(spec)
            task_ids.append(task_id)
            
            # Wait for stage to complete
            task = await self.runtime.get_task(task_id)
            while task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                await asyncio.sleep(0.1)
                task = await self.runtime.get_task(task_id)
            
            if task.status != TaskStatus.COMPLETED:
                return CoordinationResult(
                    success=False,
                    results=stage_results,
                    failed_tasks=[task_id],
                    task_ids=task_ids,
                    metadata={'failed_stage': i, 'stage_name': stage.get('name', f'stage{i}')}
                )
            
            stage_results.append(task.result)
            current_input = task.result  # Output becomes input for next stage
        
        return CoordinationResult(
            success=True,
            results=stage_results,
            failed_tasks=[],
            task_ids=task_ids,
            metadata={'stages_completed': len(stages)}
        )


class FanOutFanInCoordinator:
    """Implements the Fan-Out/Fan-In coordination pattern.
    
    Executes multiple tasks in parallel (fan-out) and waits for all to complete (fan-in).
    """
    
    def __init__(self, runtime: AsyncWorkflowRuntime) -> None:
        self.runtime = runtime
    
    async def execute(self,
                     name: str,
                     tasks: List[Dict[str, Any]],
                     wait_policy: WaitPolicy = WaitPolicy.ALL,
                     workflow_type: str = 'generic',
                     session_id: Optional[str] = None,
                     requirements: Optional[AgentRequirements] = None) -> CoordinationResult:
        """Execute fan-out/fan-in pattern.
        
        Args:
            name: Base name for tasks
            tasks: List of task definitions, each with 'name' and 'objective' keys
            wait_policy: Wait for ALL or ANY tasks to complete
            workflow_type: Workflow type for tasks
            session_id: Session ID for task grouping
            requirements: Agent requirements
            
        Returns:
            CoordinationResult with collected results
        """
        # Fan-out: create all tasks
        task_ids = []
        for i, task_def in enumerate(tasks):
            spec = TaskSpec(
                name=f"{name}_parallel_{i}_{task_def.get('name', f'task{i}')}",
                objective=task_def['objective'],
                workflow_type=workflow_type,
                session_id=session_id,
                requirements=requirements or AgentRequirements(),
                inputs=task_def.get('inputs', {})
            )
            task_id = await self.runtime.submit_root_task(spec)
            task_ids.append(task_id)
        
        # Fan-in: wait for completion based on policy
        completed_results = []
        failed_tasks = []
        
        if wait_policy == WaitPolicy.ALL:
            # Wait for all tasks
            for task_id in task_ids:
                task = await self.runtime.get_task(task_id)
                while task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                    await asyncio.sleep(0.1)
                    task = await self.runtime.get_task(task_id)
                
                if task.status == TaskStatus.COMPLETED:
                    completed_results.append(task.result)
                else:
                    failed_tasks.append(task_id)
        
        elif wait_policy == WaitPolicy.ANY:
            # Wait for first completion
            while True:
                for task_id in task_ids:
                    task = await self.runtime.get_task(task_id)
                    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                        if task.status == TaskStatus.COMPLETED:
                            completed_results.append(task.result)
                            return CoordinationResult(
                                success=True,
                                results=completed_results,
                                failed_tasks=[],
                                task_ids=task_ids,
                                metadata={'wait_policy': 'any', 'first_completed': task_id}
                            )
                        else:
                            failed_tasks.append(task_id)
                
                # Check if all failed
                if len(failed_tasks) == len(task_ids):
                    break
                
                await asyncio.sleep(0.1)
        
        return CoordinationResult(
            success=len(failed_tasks) == 0,
            results=completed_results,
            failed_tasks=failed_tasks,
            task_ids=task_ids,
            metadata={'wait_policy': wait_policy.value, 'total_tasks': len(tasks)}
        )


class ScatterGatherCoordinator:
    """Implements the Scatter-Gather coordination pattern.
    
    Broadcasts the same task to multiple workers and gathers results.
    """
    
    def __init__(self, runtime: AsyncWorkflowRuntime) -> None:
        self.runtime = runtime
    
    async def execute(self,
                     name: str,
                     objective: str,
                     worker_count: int,
                     workflow_type: str = 'generic',
                     session_id: Optional[str] = None,
                     requirements: Optional[AgentRequirements] = None) -> CoordinationResult:
        """Execute scatter-gather pattern.
        
        Args:
            name: Base name for tasks
            objective: Same objective for all workers
            worker_count: Number of parallel workers
            workflow_type: Workflow type for tasks
            session_id: Session ID for task grouping
            requirements: Agent requirements
            
        Returns:
            CoordinationResult with gathered results
        """
        # Scatter: create identical tasks
        task_ids = []
        for i in range(worker_count):
            spec = TaskSpec(
                name=f"{name}_worker_{i}",
                objective=objective,
                workflow_type=workflow_type,
                session_id=session_id,
                requirements=requirements or AgentRequirements(),
                inputs={'worker_id': i}
            )
            task_id = await self.runtime.submit_root_task(spec)
            task_ids.append(task_id)
        
        # Gather: collect all results
        results = []
        failed_tasks = []
        
        for task_id in task_ids:
            task = await self.runtime.get_task(task_id)
            while task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                await asyncio.sleep(0.1)
                task = await self.runtime.get_task(task_id)
            
            if task.status == TaskStatus.COMPLETED:
                results.append(task.result)
            else:
                failed_tasks.append(task_id)
        
        return CoordinationResult(
            success=len(failed_tasks) == 0,
            results=results,
            failed_tasks=failed_tasks,
            task_ids=task_ids,
            metadata={'worker_count': worker_count}
        )


# Convenience class for accessing all coordinators
class CoordinationPatterns:
    """Unified access to all coordination patterns."""
    
    def __init__(self, runtime: AsyncWorkflowRuntime) -> None:
        self.runtime = runtime
        self.map_reduce = MapReduceCoordinator(runtime)
        self.pipeline = PipelineCoordinator(runtime)
        self.fan_out_fan_in = FanOutFanInCoordinator(runtime)
        self.scatter_gather = ScatterGatherCoordinator(runtime)
