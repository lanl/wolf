from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any, AsyncIterator, Deque, Dict, List

from framework.orchestration.events import Event


class EventHub:
    def __init__(self, max_history: int = 1000) -> None:
        self.max_history = max_history
        self._history: Dict[str, Deque[Event]] = defaultdict(lambda: deque(maxlen=self.max_history))
        self._subs: Dict[str, List[asyncio.Queue[Event]]] = defaultdict(list)

    async def publish(self, session_id: str, event: Event) -> None:
        self._history[session_id].append(event)
        for q in list(self._subs[session_id]):
            await q.put(event)

    def history(self, session_id: str, limit: int = 200) -> List[Event]:
        h = list(self._history[session_id])
        return h[-limit:]

    async def subscribe(self, session_id: str) -> AsyncIterator[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subs[session_id].append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs[session_id].remove(q)
