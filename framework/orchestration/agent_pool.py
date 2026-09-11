from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
import asyncio
import time

from .models import AgentRequirements


_SECRET_KEYS = {"api_key", "apikey", "authorization", "access_token", "refresh_token", "token", "password", "secret"}


def _redact_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            out[key_text] = "***redacted***" if key_text.lower() in _SECRET_KEYS else _redact_metadata(item)
        return out
    if isinstance(value, list):
        return [_redact_metadata(item) for item in value]
    return value


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
    profile_id: Optional[str] = None
    agent_kind: Optional[str] = None
    draining: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_agent(cls, agent: Any) -> 'AgentDescriptor':
        return cls(
            agent=agent,
            name=getattr(agent, 'name', f'agent-{id(agent)}'),
            capabilities=list(getattr(agent, 'capabilities', []) or []),
            model_family=getattr(agent, 'model', None),
            context_window=getattr(agent, 'max_ctx_tokens', getattr(agent, 'ctx_window_length', None)),
            profile_id=getattr(agent, 'profile_id', None),
            agent_kind=getattr(agent, 'agent_kind', None),
            metadata=dict(getattr(agent, 'metadata', {}) or {}),
        )


class AgentPool:
    def __init__(self, agents: Iterable[Any]) -> None:
        self._agents: Dict[str, AgentDescriptor] = {d.name: d for d in (AgentDescriptor.from_agent(a) for a in agents)}
        self._lock = asyncio.Lock()

    async def acquire(self, task_id: str, requirements: Optional[AgentRequirements] = None) -> AgentLease:
        requirements = requirements or AgentRequirements()
        async with self._lock:
            eligible = [d for d in self._eligible(requirements) if not d.busy and not d.draining]
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
            desc = self._agents.get(lease.agent_name)
            if desc is None:
                return
            desc.busy = False
            desc.current_task_id = None
            if desc.draining:
                self._agents.pop(lease.agent_name, None)

    async def add(self, agent: Any, *, profile_id: Optional[str] = None, agent_kind: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        desc = AgentDescriptor.from_agent(agent)
        desc.profile_id = profile_id or desc.profile_id
        desc.agent_kind = agent_kind or desc.agent_kind
        desc.metadata = dict(metadata or desc.metadata or {})
        async with self._lock:
            self._agents[desc.name] = desc

    async def remove(self, agent_name: str, *, drain: bool = True) -> bool:
        async with self._lock:
            desc = self._agents.get(agent_name)
            if desc is None:
                return True
            if desc.busy and drain:
                desc.draining = True
                return False
            self._agents.pop(agent_name, None)
            return True

    async def get(self, agent_name: str) -> Any:
        async with self._lock:
            return self._agents[agent_name].agent

    async def stats(self) -> List[Dict[str, Any]]:
        async with self._lock:
            return [
                {'name': d.name, 'busy': d.busy, 'current_task_id': d.current_task_id, 'capabilities': list(d.capabilities), 'model_family': d.model_family, 'context_window': d.context_window, 'last_assigned_at': d.last_assigned_at, 'profile_id': d.profile_id, 'agent_kind': d.agent_kind, 'draining': d.draining, 'metadata': _redact_metadata(d.metadata or {})}
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
