from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol
import asyncio
import time
import uuid


@dataclass(slots=True)
class Event:
    type: str
    task_id: str
    actor: str
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class EventSubscriber(Protocol):
    async def __call__(self, event: Event) -> None: ...


class EventBus:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subs: List[EventSubscriber] = []

    def subscribe(self, callback: EventSubscriber) -> None:
        self._subs.append(callback)

    async def publish(self, event: Event) -> None:
        await self._queue.put(event)

    async def consume_once(self) -> None:
        event = await self._queue.get()
        for sub in list(self._subs):
            await sub(event)

    async def drain(self) -> None:
        while not self._queue.empty():
            await self.consume_once()
