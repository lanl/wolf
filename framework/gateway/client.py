from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional

from framework.orchestration.events import Event
from .server import GatewayServer


@dataclass(slots=True)
class GatewayClient:
    gateway: GatewayServer
    session_id: str

    async def submit(self, objective: str, *, name: str = 'root-task', workflow_type: str = 'generic', allowed_actions: Optional[list[str]] = None, inputs: Optional[Dict[str, Any]] = None) -> str:
        return await self.gateway.submit_task(self.session_id, objective, name=name, workflow_type=workflow_type, allowed_actions=allowed_actions, inputs=inputs)

    async def message(self, content: str, *, task_id: Optional[str] = None, role: str = 'user', wake: bool = True) -> None:
        await self.gateway.send_message(self.session_id, content, target_task_id=task_id, role=role, wake=wake)

    async def pause(self, task_id: str) -> None:
        await self.gateway.pause_task(task_id)

    async def resume(self, task_id: str) -> None:
        await self.gateway.resume_task(task_id)

    async def cancel(self, task_id: str) -> None:
        await self.gateway.cancel_task(task_id)

    async def retry(self, task_id: str) -> None:
        await self.gateway.retry_task(task_id)

    async def snapshot(self) -> Dict[str, Any]:
        return await self.gateway.get_snapshot(self.session_id)

    async def detail(self, task_id: str) -> Dict[str, Any]:
        return await self.gateway.get_task_detail(task_id)

    async def events(self) -> AsyncIterator[Event]:
        async for event in self.gateway.subscribe(self.session_id):
            yield event
