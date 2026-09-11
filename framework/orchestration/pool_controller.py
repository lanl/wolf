from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import asyncio
from datetime import datetime

from .agent_pool import AgentPool
from .profiles import AgentFactory, AgentProfile, normalize_mix, normalize_profiles


@dataclass(slots=True)
class AgentPoolController:
    """Controls a session-local dynamic orchestration worker pool."""

    pool: AgentPool
    profiles: List[AgentProfile]
    mix: Dict[str, int]
    factory: AgentFactory
    session_id: str
    _counters: Dict[str, int] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    async def create(cls, *, session_id: str, base_config: Dict[str, Any]) -> "AgentPoolController":
        profiles = normalize_profiles(base_config.get("agent_profiles"), base_config)
        mix = normalize_mix(base_config.get("agent_pool_mix"), profiles, int(base_config.get("orchestration_worker_count") or 1))
        pool = AgentPool([])
        controller = cls(pool=pool, profiles=profiles, mix=mix, factory=AgentFactory(base_config), session_id=session_id)
        await controller.reconcile()
        return controller

    async def reconcile(self) -> None:
        async with self._lock:
            stats = await self.pool.stats()
            by_profile: Dict[str, List[Dict[str, Any]]] = {}
            for item in stats:
                by_profile.setdefault(str(item.get("profile_id") or "default"), []).append(item)
            profile_map = {p.profile_id: p for p in self.profiles}
            for profile_id, target in self.mix.items():
                profile = profile_map.get(profile_id)
                if profile is None:
                    continue
                current = by_profile.get(profile_id, [])
                max_instances = profile.max_instances if profile.max_instances is not None else target
                target = min(max(0, int(target)), max(0, int(max_instances)))
                while len(current) < target:
                    idx = self._counters.get(profile_id, 0) + 1
                    self._counters[profile_id] = idx
                    agent = self.factory.build(profile, instance_index=idx, session_id=self.session_id)
                    await self.pool.add(agent, profile_id=profile.profile_id, agent_kind=profile.agent_kind, metadata={"profile": profile.to_dict(redact=True)})
                    current.append({"name": getattr(agent, "name", f"agent-{idx}"), "profile_id": profile_id})
                while len(current) > target:
                    victim = current.pop()
                    removed = await self.pool.remove(victim["name"], drain=True)
                    if not removed:
                        # Busy agents are marked draining and removed after release.
                        break

    async def update_mix(self, mix: Dict[str, Any], profiles: Optional[List[Dict[str, Any]]] = None, *, base_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with self._lock:
            if base_config:
                self.factory.base_config.update(dict(base_config or {}))
            if profiles is not None:
                self.profiles = normalize_profiles(profiles, self.factory.base_config)
            self.mix = normalize_mix(mix, self.profiles, 1)
        await self.reconcile()
        return await self.snapshot()

    async def snapshot(self) -> Dict[str, Any]:
        agents = await self.pool.stats()
        profile_rows = []
        for profile in self.profiles:
            related = [a for a in agents if a.get("profile_id") == profile.profile_id]
            profile_rows.append({
                "profile": profile.to_dict(redact=True),
                "target": int(self.mix.get(profile.profile_id, 0) or 0),
                "total": len(related),
                "busy": sum(1 for a in related if a.get("busy")),
                "idle": sum(1 for a in related if not a.get("busy") and not a.get("draining")),
                "draining": sum(1 for a in related if a.get("draining")),
            })
        return {
            "type": "agent_pool_snapshot",
            "session_id": self.session_id,
            "agent_profiles": [p.to_dict(redact=True) for p in self.profiles],
            "agent_pool_mix": dict(self.mix),
            "profiles": profile_rows,
            "agents": agents,
            "total_agents": len(agents),
            "busy_agents": sum(1 for a in agents if a.get("busy")),
            "idle_agents": sum(1 for a in agents if not a.get("busy") and not a.get("draining")),
            "timestamp": datetime.now().isoformat(),
        }
