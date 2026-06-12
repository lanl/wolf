from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import time
import uuid
from typing import Any, Dict, Optional

from framework.orchestration import AgentPool, AsyncWorkflowRuntime, DynamicActionAdapter, EngineConfig, Event, SharedResources, TaskInfrastructureFactory, TaskSpec, WorkflowPolicy
from .events import EventHub
from .store import SqliteStore


@dataclass(slots=True)
class Session:
    id: str
    created_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    root_task_ids: list[str] = field(default_factory=list)


class GatewayServer:
    def __init__(self, agents: list[Any], workflow_policies: Optional[Dict[str, WorkflowPolicy]] = None, shared_resources: Optional[SharedResources] = None, db_path: str = '.gateway/gateway.db', session_root: str = '.gateway/sessions', config: Optional[EngineConfig] = None) -> None:
        self.agent_pool = AgentPool(agents)
        self.store = SqliteStore(db_path)
        self.hub = EventHub()
        self.infra_factory = TaskInfrastructureFactory(shared_resources=shared_resources or SharedResources(), session_root=session_root)
        self.runtime = AsyncWorkflowRuntime(agent_pool=self.agent_pool, infra_factory=self.infra_factory, workflow_policies=workflow_policies, config=config or EngineConfig(), action_adapter=DynamicActionAdapter())
        self.sessions: Dict[str, Session] = {}
        self.runtime.event_bus.subscribe(self._on_runtime_event)
        self._task_to_session: Dict[str, str] = {}
        self._bg_task: asyncio.Task | None = None
        self._bg_stop = asyncio.Event()

    def create_session(self, metadata: Optional[Dict[str, Any]] = None) -> str:
        sid = str(uuid.uuid4())
        session = Session(id=sid, created_at=time.time(), metadata=metadata or {})
        self.sessions[sid] = session
        self.store.put_session(sid, session.created_at, session.metadata)
        return sid

    def connect(self, session_id: str):
        from .client import GatewayClient
        self.ensure_background()
        return GatewayClient(self, session_id)


    def ensure_background(self) -> None:
        if self._bg_task is None or self._bg_task.done():
            self._bg_stop = asyncio.Event()
            self._bg_task = asyncio.create_task(self.runtime.run_forever(self._bg_stop))

    async def shutdown(self) -> None:
        self._bg_stop.set()
        if self._bg_task is not None:
            self._bg_task.cancel()
            await asyncio.gather(self._bg_task, return_exceptions=True)
            self._bg_task = None


    async def submit_task(self, session_id: str, objective: str, *, name: str = 'root-task', workflow_type: str = 'chat', allowed_actions: Optional[list[str]] = None, inputs: Optional[Dict[str, Any]] = None) -> str:
        self.ensure_background()
        spec = TaskSpec(name=name, objective=objective, workflow_type=workflow_type, allowed_actions=allowed_actions, inputs=inputs or {}, session_id=session_id)
        task_id = await self.runtime.submit_root_task(spec)
        self._task_to_session[task_id] = session_id
        self.sessions[session_id].root_task_ids.append(task_id)
        return task_id

    async def send_message(self, session_id: str, content: str, *, target_task_id: str | None = None, role: str = 'user', wake: bool = True) -> str:
        self.ensure_background()
        if target_task_id is None:
            target_task_id = await self._default_target_task(session_id)
            if target_task_id is None:
                return await self.submit_task(session_id, content, name='chat-root')
        await self.runtime.inject_user_message(target_task_id, content, role=role, wake=wake)
        return target_task_id

    async def pause_task(self, task_id: str, reason: str = 'paused by user') -> None:
        await self.runtime.pause_task(task_id, reason=reason)

    async def resume_task(self, task_id: str, reason: str = 'resumed by user') -> None:
        await self.runtime.resume_task(task_id, reason=reason)

    async def cancel_task(self, task_id: str, reason: str = 'cancelled by user') -> None:
        await self.runtime.cancel_task(task_id, reason=reason)

    async def retry_task(self, task_id: str, reason: str = 'retried by user') -> None:
        await self.runtime.retry_task(task_id, reason=reason)

    async def get_task_detail(self, task_id: str) -> Dict[str, Any]:
        detail = await self.runtime.get_task_detail(task_id)
        tasks = await self.runtime.repository.list()
        children = [
            {
                'id': t.id,
                'name': t.spec.name,
                'status': t.status.value,
                'objective': t.spec.objective,
                'workflow_type': t.spec.workflow_type,
                'lease': t.leased_agent_name,
                'owner': t.owner_agent_name,
            }
            for t in tasks
            if t.spec.parent_id == task_id
        ]
        children.sort(key=lambda row: row['name'])
        detail['children'] = children
        return detail

    async def run_until_complete(self, task_id: str):
        return await self.runtime.run_until_complete(task_id)

    async def get_snapshot(self, session_id: str) -> Dict[str, Any]:
        snap = await self.runtime.snapshot()
        tasks = [t for t in snap['tasks'] if (t.spec.session_id or self._task_to_session.get(t.id)) == session_id]
        tasks_by_status = {
            'running': [t for t in tasks if t.status.value == 'running'],
            'waiting': [t for t in tasks if t.status.value == 'waiting'],
            'ready': [t for t in tasks if t.status.value == 'ready'],
            'paused': [t for t in tasks if t.status.value == 'paused'],
            'blocked': [t for t in tasks if t.status.value == 'blocked'],
            'completed': [t for t in tasks if t.status.value == 'completed'],
            'failed': [t for t in tasks if t.status.value == 'failed'],
            'cancelled': [t for t in tasks if t.status.value == 'cancelled'],
        }
        return {
            'session_id': session_id,
            'tasks': tasks,
            'agent_pool': snap['agent_pool'],
            'events': self.hub.history(session_id),
            'artifacts': {tid: rows for tid, rows in snap.get('artifacts', {}).items() if any(t.id == tid for t in tasks)},
            'task_counts': {k: len(v) for k, v in tasks_by_status.items()},
            'queues': tasks_by_status,
        }

    async def _default_target_task(self, session_id: str) -> Optional[str]:
        tasks = [t for t in await self.runtime.repository.list() if (t.spec.session_id or self._task_to_session.get(t.id)) == session_id]
        active = [t for t in tasks if t.status.value not in {'completed', 'failed', 'cancelled'}]
        if active:
            active.sort(key=lambda t: t.updated_at, reverse=True)
            return active[0].id
        session = self.sessions.get(session_id)
        return session.root_task_ids[-1] if session and session.root_task_ids else None

    async def _on_runtime_event(self, event: Event) -> None:
        session_id = self._task_to_session.get(event.task_id)
        if session_id is None:
            task = await self.runtime.repository.get(event.task_id)
            if task:
                session_id = task.spec.session_id or ''
                if session_id:
                    self._task_to_session[event.task_id] = session_id
        if session_id is None:
            session_id = ''
        await self.hub.publish(session_id, event)
        self.store.put_event(session_id, event)
        task = await self.runtime.repository.get(event.task_id)
        if task:
            self.store.put_task(task)
            self._task_to_session[task.id] = task.spec.session_id or session_id
            for record in self.runtime.artifacts.list_task_artifacts(task.id):
                self.store.put_artifact(task.spec.session_id or session_id, record)

    async def subscribe(self, session_id: str):
        async for event in self.hub.subscribe(session_id):
            yield event
