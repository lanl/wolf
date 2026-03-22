from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
import asyncio
import json
import time

from .action_adapter import DynamicActionAdapter
from .actions import CompleteTaskAction, CreateSubtasksAction, FailTaskAction, OrchestrationAction, PauseTaskAction, PublishProgressAction, RequestUserInputAction, TaskThreadMessageAction, WaitForTasksAction
from .agent_pool import AgentPool, AgentLease
from .agent_runner import AgentRunner
from .artifacts import ArtifactStore
from .context import ContextBuilder
from .events import Event, EventBus
from .graph import TaskGraph
from .legacy_adapter import LegacyActionExecutor
from .memory import MemoryStore
from .models import EngineConfig, SummaryCapsule, TaskNode, TaskResult, TaskSpec, TaskStatus, WaitPolicy
from .policies import BudgetGuard, ChatWorkflowPolicy, GenericWorkflowPolicy, WorkflowPolicy
from .repository import TaskRepository
from .task_infra import TaskInfrastructure, TaskInfrastructureFactory


@dataclass
class RuntimeState:
    repository: TaskRepository
    graph: TaskGraph
    memory: MemoryStore
    context_builder: ContextBuilder
    event_bus: EventBus
    config: EngineConfig


class AsyncWorkflowRuntime:
    def __init__(self, agent_pool: AgentPool, infra_factory: TaskInfrastructureFactory, workflow_policies: Optional[Dict[str, WorkflowPolicy]] = None, config: Optional[EngineConfig] = None, action_adapter: Optional[DynamicActionAdapter] = None, artifact_store: Optional[ArtifactStore] = None) -> None:
        self.repository = TaskRepository()
        self.graph = TaskGraph()
        self.config = config or EngineConfig()
        self.memory = MemoryStore(max_compressed_history=self.config.max_compressed_history)
        self.event_bus = EventBus()
        self.agent_pool = agent_pool
        self.infra_factory = infra_factory
        self.action_adapter = action_adapter or DynamicActionAdapter()
        self.runner = AgentRunner(self.action_adapter)
        self.context_builder = ContextBuilder(self.graph, self.memory, max_messages=self.config.max_context_messages, max_facts=self.config.max_context_facts)
        self.guard = BudgetGuard(self.config, self.graph)
        self.workflow_policies = workflow_policies or {'generic': GenericWorkflowPolicy(), 'chat': ChatWorkflowPolicy()}
        self.task_infras: Dict[str, TaskInfrastructure] = {}
        self.legacy_executor = LegacyActionExecutor(run_in_thread=self.config.run_sync_actions_in_thread)
        self.artifacts = artifact_store or ArtifactStore(inline_limit=self.config.artifact_inline_limit)

    def policy_for(self, workflow_type: str) -> WorkflowPolicy:
        return self.workflow_policies.get(workflow_type, self.workflow_policies['generic'])

    async def submit_root_task(self, spec: TaskSpec) -> str:
        task = TaskNode(spec=spec)
        await self._register_task(task)
        return task.id

    async def get_task(self, task_id: str) -> Optional[TaskNode]:
        return await self.repository.get(task_id)

    async def get_task_detail(self, task_id: str) -> Dict[str, Any]:
        task = await self.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        capsule = await self.memory.get_context(task_id)
        return {
            'task': task,
            'local_messages': list(capsule.local_messages),
            'local_facts': list(capsule.local_facts),
            'child_summaries': dict(capsule.child_summaries),
            'dependency_summaries': dict(capsule.dependency_summaries),
            'compressed_history': list(capsule.compressed_history),
            'artifacts': [{'artifact_id': r.artifact_id, 'task_id': r.task_id, 'path': r.path, 'kind': r.kind, 'created_at': r.created_at, 'metadata': r.metadata} for r in self.artifacts.list_task_artifacts(task_id)],
            'events': list(getattr(self.task_infras.get(task_id), 'events', [])),
        }

    async def _append_thread_entry(self, task_id: str, role: str, content: Any) -> None:
        task = await self.repository.get(task_id)
        if task is None:
            return
        infra = self.task_infras.get(task_id)
        if infra is not None:
            infra.update_history(role, content, action={'action': 'thread_entry'}, log_console=False)
        await self.memory.add_local_message(task_id, role, content)

    async def inject_user_message(self, task_id: str, content: Any, role: str = 'user', wake: bool = True) -> None:
        task = await self.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        await self._append_thread_entry(task_id, role, content)
        await self.event_bus.publish(Event(type='user_message', task_id=task_id, actor=role, payload={'content': str(content)[:200], 'wake': wake}))
        if wake and task.status not in {TaskStatus.RUNNING, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            task.status = TaskStatus.READY
            task.error = None
            task.touch()
            await self.repository.put(task)
            await self.event_bus.publish(Event(type='task_ready', task_id=task_id, actor='runtime', payload={'reason': 'user_message'}))

    async def pause_task(self, task_id: str, reason: str = 'paused by user') -> None:
        task = await self.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        await self._append_thread_entry(task_id, 'system', f'user paused task: {reason}')
        task.status = TaskStatus.PAUSED
        task.touch()
        await self.repository.put(task)
        await self.event_bus.publish(Event(type='task_paused', task_id=task_id, actor='user', payload={'reason': reason}))

    async def resume_task(self, task_id: str, reason: str = 'resumed by user') -> None:
        task = await self.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        if task.status in {TaskStatus.PAUSED, TaskStatus.BLOCKED, TaskStatus.WAITING}:
            await self._append_thread_entry(task_id, 'system', f'user resumed task: {reason}')
            task.status = TaskStatus.READY
            task.error = None
            task.touch()
            await self.repository.put(task)
            await self.event_bus.publish(Event(type='task_resumed', task_id=task_id, actor='user', payload={'reason': reason}))
            await self.event_bus.publish(Event(type='task_ready', task_id=task_id, actor='runtime', payload={'reason': 'manual_resume'}))

    async def cancel_task(self, task_id: str, reason: str = 'cancelled by user') -> None:
        task = await self.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        await self._append_thread_entry(task_id, 'system', f'user cancelled task: {reason}')
        task.status = TaskStatus.CANCELLED
        task.error = reason
        task.touch()
        await self.repository.put(task)
        await self.event_bus.publish(Event(type='task_cancelled', task_id=task_id, actor='user', payload={'reason': reason}))

    async def retry_task(self, task_id: str, reason: str = 'retried by user') -> None:
        task = await self.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        task.status = TaskStatus.READY
        task.error = None
        await self._append_thread_entry(task_id, 'system', f'user retried task: {reason}')
        task.retries += 1
        task.touch()
        await self.repository.put(task)
        await self.event_bus.publish(Event(type='task_retried', task_id=task_id, actor='user', payload={'reason': reason, 'retries': task.retries}))
        await self.event_bus.publish(Event(type='task_ready', task_id=task_id, actor='runtime', payload={'reason': 'manual_retry'}))

    async def _register_task(self, task: TaskNode) -> None:
        if await self.repository.count() >= self.config.budgets.max_total_tasks:
            raise RuntimeError('Maximum total task count exceeded')
        await self.repository.add(task)
        self.graph.add_task(task)
        await self.memory.ensure_context(task.id, task.spec.objective, task.spec.workflow_type)
        if task.spec.parent_id:
            parent = await self.repository.get(task.spec.parent_id)
            if parent and parent.result and parent.result.summary_capsule:
                await self.memory.set_parent_summary(task.id, parent.result.summary_capsule.outcome)
        self.task_infras[task.id] = self.infra_factory.create(task.id, task.spec.workflow_type, metadata={'task_name': task.spec.name, 'session_id': task.spec.session_id})
        task.status = TaskStatus.READY if not task.spec.dependencies else TaskStatus.PENDING
        await self.repository.put(task)
        await self.event_bus.publish(Event(type='task_registered', task_id=task.id, actor='runtime', payload={'name': task.spec.name, 'workflow_type': task.spec.workflow_type}))
        await self.event_bus.publish(Event(type='task_ready', task_id=task.id, actor='runtime', payload={'reason': 'registered'}))

    async def run_until_complete(self, root_task_id: str) -> TaskNode:
        while True:
            await self.step()
            root = await self.repository.get(root_task_id)
            if root and root.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                return root
            await asyncio.sleep(self.config.scheduler_poll_interval)

    async def step(self) -> None:
        await self._schedule_once()
        await self.event_bus.drain()

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        stop_event = stop_event or asyncio.Event()
        while not stop_event.is_set():
            await self.step()
            await asyncio.sleep(self.config.scheduler_poll_interval)

    async def _schedule_once(self) -> None:
        runnable = self.graph.runnable_tasks()
        running_count = sum(1 for t in await self.repository.list() if t.status == TaskStatus.RUNNING)
        for task in runnable:
            if running_count >= self.config.budgets.max_active_tasks:
                break
            if task.leased_agent_name:
                continue
            try:
                lease = await self.agent_pool.acquire(task.id, task.spec.requirements)
            except RuntimeError:
                break
            task.leased_agent_name = lease.agent_name
            if task.owner_agent_name is None:
                task.owner_agent_name = lease.agent_name
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            task.touch()
            await self.repository.put(task)
            await self.event_bus.publish(Event(type='agent_leased', task_id=task.id, actor=lease.agent_name, payload={'task_name': task.spec.name}))
            running_count += 1
            asyncio.create_task(self._run_task(task.id, lease))

    async def _run_task(self, task_id: str, lease: AgentLease) -> None:
        try:
            await self._execute_task(task_id, lease)
        finally:
            task = await self.repository.get(task_id)
            if task:
                task.leased_agent_name = None
                task.touch()
                await self.repository.put(task)
            await self.agent_pool.release(lease)
            await self.event_bus.publish(Event(type='agent_released', task_id=task_id, actor=lease.agent_name, payload={}))

    async def _execute_task(self, task_id: str, lease: AgentLease) -> None:
        task = await self.repository.get(task_id)
        assert task is not None
        agent = await self.agent_pool.get(lease.agent_name)
        policy = self.policy_for(task.spec.workflow_type)
        bundle = self.action_adapter.build(policy.allowed_action_names(task))
        context = await self.context_builder.build_task_context(task)
        infra = self.task_infras[task.id]
        await self.memory.add_local_message(task.id, 'system', f'leased_agent={lease.agent_name}')
        prompt = policy.build_agent_prompt(task, context, bundle.schema_text)
        await self._append_thread_entry(task.id, 'system', f'agent {lease.agent_name} entered task workspace')
        await self.event_bus.publish(Event(type='task_started', task_id=task.id, actor=lease.agent_name, payload={'workflow_type': task.spec.workflow_type}))
        try:
            payload = await self.runner.get_action(agent=agent, prompt=prompt, validator=bundle.validator)
            action = self._coerce_action(payload)
            await self._apply_action(task, action, infra, policy)
        except Exception as exc:
            task.error = str(exc)
            task.status = TaskStatus.FAILED
            task.touch()
            await self.repository.put(task)
            await self.event_bus.publish(Event(type='task_failed', task_id=task.id, actor=lease.agent_name, payload={'error': str(exc)}))
            if self.config.fail_fast:
                raise

    def _coerce_action(self, payload: Any) -> Any:
        if isinstance(payload, OrchestrationAction) or hasattr(payload, 'execute'):
            return payload
        if isinstance(payload, str):
            return TaskThreadMessageAction(message=payload)
        if hasattr(payload, 'model_dump'):
            payload = payload.model_dump()
        if not isinstance(payload, dict):
            raise TypeError(f'Unsupported action payload type: {type(payload)}')
        action_name = payload.get('action')
        if action_name == 'create_subtasks':
            subtasks = [TaskSpec(**spec) if isinstance(spec, dict) else spec for spec in payload.get('subtasks', [])]
            return CreateSubtasksAction(subtasks=subtasks, wait_policy=WaitPolicy(payload.get('wait_policy', 'all')), summary=payload.get('summary', 'Delegating work to subtasks'), rationale=payload.get('rationale', ''))
        if action_name == 'complete_task':
            return CompleteTaskAction(summary=payload.get('summary', ''), facts=payload.get('facts', []), artifacts=payload.get('artifacts', {}), trace=payload.get('trace', []), confidence=payload.get('confidence', 0.8), important_findings=payload.get('important_findings', []), blockers=payload.get('blockers', []))
        if action_name == 'publish_progress':
            return PublishProgressAction(message=payload.get('message', ''), payload=payload.get('payload', {}))
        if action_name in {'task_message', 'send_message', 'assistant_message', 'message'}:
            message = payload.get('message') or payload.get('content') or payload.get('text') or ''
            return TaskThreadMessageAction(message=message, role=payload.get('role', 'assistant'), kind=payload.get('kind', 'message'))
        if action_name == 'request_user_input':
            return RequestUserInputAction(question=payload.get('question', ''), reason=payload.get('reason', ''))
        if action_name == 'wait_for_tasks':
            return WaitForTasksAction(wait_policy=WaitPolicy(payload.get('wait_policy', 'all')))
        if action_name == 'pause_task':
            return PauseTaskAction(reason=payload.get('reason', ''))
        if action_name == 'fail_task':
            return FailTaskAction(error=payload.get('error', 'unknown error'))
        raise ValueError(f'Unknown action payload: {payload}')

    async def _apply_action(self, task: TaskNode, action: Any, infra: TaskInfrastructure, policy: WorkflowPolicy) -> None:
        await self.memory.add_local_message(task.id, 'agent', getattr(action, 'action', str(action)))
        if hasattr(action, 'execute') and not isinstance(action, OrchestrationAction):
            outcome = await self.legacy_executor.execute(action, infra)
            for entry in outcome.history_delta:
                await self.memory.add_local_message(task.id, entry.get('actor', 'tool'), entry.get('content'))
            if not outcome.ok:
                task.error = outcome.error
                task.status = TaskStatus.FAILED
                task.touch()
                await self.repository.put(task)
                await self.event_bus.publish(Event(type='task_failed', task_id=task.id, actor=task.owner_agent_name or 'agent', payload={'error': outcome.error}))
                return
            if outcome.result is not None:
                result_payload = self.artifacts.inline_or_ref(task.id, outcome.result, name_hint=action.__class__.__name__)
                await self._append_thread_entry(task.id, 'tool_result', result_payload)
            task.status = TaskStatus.READY
            task.touch()
            await self.repository.put(task)
            await self.event_bus.publish(Event(type='action_executed', task_id=task.id, actor=task.owner_agent_name or 'agent', payload={'action': action.__class__.__name__, 'history_delta': len(outcome.history_delta)}))
            await self.event_bus.publish(Event(type='task_ready', task_id=task.id, actor='runtime', payload={'reason': 'legacy_action_complete'}))
            return
        if isinstance(action, PublishProgressAction):
            payload = dict(action.payload)
            if action.message:
                payload['message'] = action.message
            await self._append_thread_entry(task.id, 'assistant', action.message or 'progress update')
            await self.event_bus.publish(Event(type='task_progress', task_id=task.id, actor=task.owner_agent_name or 'agent', payload=payload))
            task.status = TaskStatus.READY
            task.touch()
            await self.repository.put(task)
            await self.event_bus.publish(Event(type='task_ready', task_id=task.id, actor='runtime', payload={'reason': 'progress_published'}))
            return
        if isinstance(action, TaskThreadMessageAction):
            await self._append_thread_entry(task.id, action.role, action.message)
            await self.event_bus.publish(Event(type='assistant_message', task_id=task.id, actor=task.owner_agent_name or 'agent', payload={'message': action.message, 'kind': action.kind}))
            task.status = TaskStatus.READY
            task.touch()
            await self.repository.put(task)
            return
        if isinstance(action, RequestUserInputAction):
            task.status = TaskStatus.BLOCKED
            task.error = action.question
            task.touch()
            await self.repository.put(task)
            await self._append_thread_entry(task.id, 'assistant', action.question)
            await self.event_bus.publish(Event(type='user_input_requested', task_id=task.id, actor=task.owner_agent_name or 'agent', payload={'question': action.question, 'reason': action.reason}))
            return
        if isinstance(action, PauseTaskAction):
            task.status = TaskStatus.PAUSED
            task.touch()
            await self.repository.put(task)
            await self._append_thread_entry(task.id, 'system', f'task paused: {action.reason}')
            await self.event_bus.publish(Event(type='task_paused', task_id=task.id, actor=task.owner_agent_name or 'agent', payload={'reason': action.reason}))
            return
        if isinstance(action, FailTaskAction):
            task.status = TaskStatus.FAILED
            task.error = action.error
            task.touch()
            await self.repository.put(task)
            await self._append_thread_entry(task.id, 'assistant', f'task failed: {action.error}')
            await self.event_bus.publish(Event(type='task_failed', task_id=task.id, actor=task.owner_agent_name or 'agent', payload={'error': action.error}))
            await self._notify_parent(task, policy)
            return
        if isinstance(action, CreateSubtasksAction):
            self.guard.validate_new_child(task, len(action.subtasks))
            child_ids = []
            for child_spec in action.subtasks:
                child_spec.parent_id = task.id
                child_spec.session_id = child_spec.session_id or task.spec.session_id
                if not child_spec.workflow_type:
                    child_spec.workflow_type = policy.choose_workflow_type(task, child_spec)
                child = TaskNode(spec=child_spec)
                child.depth = task.depth + 1
                child.lineage = [*task.lineage, task.id]
                await self._register_task(child)
                child_ids.append(child.id)
            task.waiting_policy = action.wait_policy
            task.waiting_on = child_ids
            task.status = TaskStatus.WAITING if action.wait_policy != WaitPolicy.NONE else TaskStatus.READY
            task.touch()
            await self.repository.put(task)
            await self._append_thread_entry(task.id, 'assistant', action.summary)
            await self._append_thread_entry(task.id, 'system', f'subtasks created: ' + ', '.join(cid[:8] for cid in child_ids))
            await self.event_bus.publish(Event(type='subtasks_created', task_id=task.id, actor=task.owner_agent_name or 'agent', payload={'child_ids': child_ids, 'wait_policy': action.wait_policy.value, 'summary': action.summary}))
            if action.wait_policy == WaitPolicy.NONE:
                await self.event_bus.publish(Event(type='task_ready', task_id=task.id, actor='runtime', payload={'reason': 'non_blocking_subtasks'}))
            return
        if isinstance(action, WaitForTasksAction):
            task.status = TaskStatus.WAITING
            task.waiting_policy = action.wait_policy
            task.touch()
            await self.repository.put(task)
            await self._append_thread_entry(task.id, 'system', f'task waiting on children ({action.wait_policy.value})')
            await self.event_bus.publish(Event(type='task_waiting', task_id=task.id, actor=task.owner_agent_name or 'agent', payload={'wait_policy': action.wait_policy.value}))
            return
        if isinstance(action, CompleteTaskAction):
            artifacts = await self._materialize_artifacts(task.id, action.artifacts, action.summary, action.trace)
            local_digest = await self._compress_local_context(task.id)
            child_digest = await self._compress_child_summaries(task.id)
            capsule = SummaryCapsule(
                task_id=task.id,
                objective=task.spec.objective,
                outcome=action.summary,
                important_findings=action.important_findings,
                blockers=action.blockers,
                confidence=action.confidence,
                artifact_refs=list(artifacts.keys()),
                local_context_digest=local_digest,
                child_summary_digest=child_digest,
                next_steps=[],
            )
            task.result = TaskResult(summary=action.summary, artifacts=artifacts, facts=action.facts, trace=action.trace, summary_capsule=capsule)
            task.status = TaskStatus.COMPLETED
            task.finished_at = time.time()
            task.touch()
            await self.repository.put(task)
            await self.memory.put_summary(task.id, task.result.summary_capsule)
            await self._append_thread_entry(task.id, 'assistant', action.summary)
            for fact in action.facts:
                await self.memory.add_local_fact(task.id, fact)
            await self.event_bus.publish(Event(type='summary_generated', task_id=task.id, actor=task.owner_agent_name or 'agent', payload={'digest': local_digest, 'child_digest': child_digest}))
            await self.event_bus.publish(Event(type='task_completed', task_id=task.id, actor=task.owner_agent_name or 'agent', payload={'summary': action.summary}))
            await self._notify_parent(task, policy)
            return
        raise TypeError(f'Unsupported action instance: {action}')

    async def _materialize_artifacts(self, task_id: str, artifacts: Dict[str, Any], summary: str, trace: list[dict[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if summary:
            rec = self.artifacts.put_text(task_id, 'summary.txt', summary, metadata={'kind': 'summary'})
            out['summary'] = f'artifact://{task_id}/{Path(rec.path).name}'
        if trace:
            rec = self.artifacts.put_json(task_id, 'trace.json', trace, metadata={'kind': 'trace'})
            out['trace'] = f'artifact://{task_id}/{Path(rec.path).name}'
        for key, value in (artifacts or {}).items():
            if isinstance(value, str) and value.startswith('artifact://'):
                out[key] = value
            elif isinstance(value, (dict, list, tuple)):
                rec = self.artifacts.put_json(task_id, f'{key}.json', value, metadata={'source_key': key})
                out[key] = f'artifact://{task_id}/{Path(rec.path).name}'
            else:
                rec = self.artifacts.put_text(task_id, f'{key}.txt', str(value), metadata={'source_key': key})
                out[key] = f'artifact://{task_id}/{Path(rec.path).name}'
        return out

    async def _compress_local_context(self, task_id: str) -> str:
        capsule = await self.memory.get_context(task_id)
        parts = [str(m['content']) for m in capsule.local_messages[-3:]]
        text = ' | '.join(parts).strip() or 'no local context'
        return text[:280] + ('...' if len(text) > 280 else '')

    async def _compress_child_summaries(self, task_id: str) -> str:
        capsule = await self.memory.get_context(task_id)
        if not capsule.child_summaries:
            return 'no child summaries'
        pairs = [f'{cid[:8]}={summary}' for cid, summary in list(capsule.child_summaries.items())[-4:]]
        text = ' ; '.join(pairs)
        return text[:280] + ('...' if len(text) > 280 else '')

    async def _notify_parent(self, task: TaskNode, policy: WorkflowPolicy) -> None:
        parent_id = task.spec.parent_id
        if not parent_id or not self.config.auto_resume_parents:
            return
        parent = await self.repository.get(parent_id)
        if parent is None:
            return
        child_summary = await self.memory.get_summary(task.id)
        if child_summary is not None:
            await self.memory.attach_child_summary(parent.id, task.id, child_summary)
            await self._append_thread_entry(parent.id, 'system', f'child {task.spec.name} [{task.id[:8]}] completed: {child_summary.outcome}')
            await self.event_bus.publish(Event(type='summary_propagated', task_id=parent.id, actor='runtime', payload={'child_id': task.id, 'outcome': child_summary.outcome}))
        if parent.status != TaskStatus.WAITING:
            return
        pending = [cid for cid in parent.waiting_on if (await self.repository.get(cid)).status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}]
        ready_to_resume = False
        if parent.waiting_policy == WaitPolicy.ALL:
            ready_to_resume = len(pending) == 0
        elif parent.waiting_policy == WaitPolicy.ANY:
            ready_to_resume = len(pending) < len(parent.waiting_on)
        if ready_to_resume:
            parent.status = TaskStatus.READY
            parent.touch()
            await self.repository.put(parent)
            await self._append_thread_entry(parent.id, 'system', f'task resumed after child {task.id[:8]} update')
            await self.event_bus.publish(Event(type='parent_resumed', task_id=parent.id, actor='runtime', payload={'child_id': task.id}))
            await self.event_bus.publish(Event(type='task_ready', task_id=parent.id, actor='runtime', payload={'reason': 'parent_resumed'}))

    async def snapshot(self) -> Dict[str, Any]:
        tasks = list(await self.repository.list())
        return {
            'tasks': tasks,
            'agent_pool': await self.agent_pool.stats(),
            'artifacts': {task.id: [{'artifact_id': r.artifact_id, 'task_id': r.task_id, 'path': r.path, 'kind': r.kind, 'created_at': r.created_at, 'metadata': r.metadata} for r in self.artifacts.list_task_artifacts(task.id)] for task in tasks},
        }
