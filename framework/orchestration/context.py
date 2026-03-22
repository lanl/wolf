from __future__ import annotations

from typing import List

from .graph import TaskGraph
from .memory import MemoryStore
from .models import TaskNode


class ContextBuilder:
    def __init__(self, graph: TaskGraph, memory: MemoryStore, max_messages: int = 8, max_facts: int = 10) -> None:
        self.graph = graph
        self.memory = memory
        self.max_messages = max_messages
        self.max_facts = max_facts

    async def build_task_context(self, task: TaskNode) -> str:
        capsule = await self.memory.get_context(task.id)
        dep_lines: List[str] = []
        for dep_id in task.spec.dependencies:
            summary = await self.memory.get_summary(dep_id)
            if summary:
                dep_lines.append(f'- {dep_id[:8]}: {summary.outcome}')
        child_lines: List[str] = []
        for child in self.graph.get_children(task.id):
            summary = await self.memory.get_summary(child.id)
            if summary:
                child_lines.append(f'- {child.spec.name}: {summary.outcome}')
        local_lines = [f"- {m['role']}: {m['content']}" for m in capsule.local_messages[-self.max_messages:]]
        fact_lines = [f'- {fact}' for fact in capsule.local_facts[-self.max_facts:]]
        compressed_lines = [f'- {line}' for line in capsule.compressed_history[-6:]]
        return '\n'.join([
            f'Task ID: {task.id}',
            f'Task Name: {task.spec.name}',
            f'Workflow Type: {task.spec.workflow_type}',
            f'Objective: {task.spec.objective}',
            f'Depth: {task.depth}',
            '',
            'Parent Summary:', capsule.parent_summary or '<none>', '',
            'Dependency Summaries:', '\n'.join(dep_lines) if dep_lines else '<none>', '',
            'Child Summaries:', '\n'.join(child_lines) if child_lines else '<none>', '',
            'Compressed History:', '\n'.join(compressed_lines) if compressed_lines else '<none>', '',
            'Recent Local Messages:', '\n'.join(local_lines) if local_lines else '<none>', '',
            'Local Facts:', '\n'.join(fact_lines) if fact_lines else '<none>',
        ])
