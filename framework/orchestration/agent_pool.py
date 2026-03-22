from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
import asyncio
import time

from .models import AgentRequirements


@dataclass(slots=True)
class AgentLease:
    agent_name: str
    task_id: str
    acquired_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class AgentDescriptor:
    agent: Any
    name: str
    capabilities: List[str] = field(default_factory=list)
    model_family: Optional[str] = None
    context_window: Optional[int] = None
    busy: bool = False
    current_task_id: Optional[str] = None
    last_assigned_at: float = 0.0

    @classmethod
    def from_agent(cls, agent: Any) -> 'AgentDescriptor':
        return cls(
            agent=agent,
            name=getattr(agent, 'name', f'agent-{id(agent)}'),
            capabilities=list(getattr(agent, 'capabilities', []) or []),
            model_family=getattr(agent, 'model', None),
            context_window=getattr(agent, 'max_ctx_tokens', None),
        )


class AgentPool:
    def __init__(self, agents: Iterable[Any]) -> None:
        self._agents: Dict[str, AgentDescriptor] = {d.name: d for d in (AgentDescriptor.from_agent(a) for a in agents)}
        self._lock = asyncio.Lock()

    async def acquire(self, task_id: str, requirements: Optional[AgentRequirements] = None) -> AgentLease:
        requirements = requirements or AgentRequirements()
        async with self._lock:
            eligible = [d for d in self._eligible(requirements) if not d.busy]
            if not eligible:
                raise RuntimeError('No compatible free agents are currently available')
            eligible.sort(key=lambda d: d.last_assigned_at)
            desc = eligible[0]
            desc.busy = True
            desc.current_task_id = task_id
            desc.last_assigned_at = time.time()
            return AgentLease(agent_name=desc.name, task_id=task_id)

    async def release(self, lease: AgentLease) -> None:
        async with self._lock:
            desc = self._agents[lease.agent_name]
            desc.busy = False
            desc.current_task_id = None

    async def get(self, agent_name: str) -> Any:
        async with self._lock:
            return self._agents[agent_name].agent

    async def stats(self) -> List[Dict[str, Any]]:
        async with self._lock:
            return [
                {'name': d.name, 'busy': d.busy, 'current_task_id': d.current_task_id, 'capabilities': list(d.capabilities), 'model_family': d.model_family, 'context_window': d.context_window, 'last_assigned_at': d.last_assigned_at}
                for d in self._agents.values()
            ]

    def _eligible(self, requirements: AgentRequirements) -> List[AgentDescriptor]:
        preferred = set(requirements.preferred_agent_names or [])
        def ok(desc: AgentDescriptor) -> bool:
            if preferred and desc.name not in preferred:
                return False
            if requirements.model_family and desc.model_family != requirements.model_family:
                return False
            if requirements.min_context_window and (desc.context_window or 0) < requirements.min_context_window:
                return False
            need = set(requirements.capabilities or [])
            have = set(desc.capabilities or [])
            return need.issubset(have)
        return [d for d in self._agents.values() if ok(d)]
