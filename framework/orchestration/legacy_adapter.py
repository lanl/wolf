from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import asyncio
import inspect

from .task_infra import TaskInfrastructure


@dataclass(slots=True)
class ActionOutcome:
    ok: bool
    result: Any = None
    error: Optional[str] = None
    history_delta: List[Dict[str, Any]] = field(default_factory=list)


class LegacyActionExecutor:
    def __init__(self, run_in_thread: bool = True) -> None:
        self.run_in_thread = run_in_thread

    async def execute(self, action: Any, infra: TaskInfrastructure) -> ActionOutcome:
        before = len(getattr(infra, 'events', []))
        try:
            call = getattr(action, 'execute')
            if inspect.iscoroutinefunction(call):
                result = await call(infra=infra)
            elif self.run_in_thread:
                result = await asyncio.to_thread(call, infra=infra)
            else:
                result = call(infra=infra)
            after = len(getattr(infra, 'events', []))
            delta = list(getattr(infra, 'events', []))[before:after]
            return ActionOutcome(ok=True, result=result, history_delta=delta)
        except Exception as exc:
            after = len(getattr(infra, 'events', []))
            delta = list(getattr(infra, 'events', []))[before:after]
            return ActionOutcome(ok=False, error=str(exc), history_delta=delta)
