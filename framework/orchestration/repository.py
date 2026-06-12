from __future__ import annotations

from typing import Dict, Iterable, Optional
import asyncio
import time

from .models import TaskNode, TaskStatus


class TaskRepository:
    def __init__(self) -> None:
        self._tasks: Dict[str, TaskNode] = {}
        self._lock = asyncio.Lock()

    async def add(self, task: TaskNode) -> None:
        async with self._lock:
            self._tasks[task.id] = task

    async def get(self, task_id: str) -> Optional[TaskNode]:
        async with self._lock:
            return self._tasks.get(task_id)

    async def put(self, task: TaskNode) -> None:
        async with self._lock:
            task.updated_at = time.time()
            self._tasks[task.id] = task

    async def list(self) -> Iterable[TaskNode]:
        async with self._lock:
            return list(self._tasks.values())

    async def count(self) -> int:
        async with self._lock:
            return len(self._tasks)

    async def update_status(self, task_id: str, status: TaskStatus) -> None:
        async with self._lock:
            task = self._tasks[task_id]
            task.status = status
            task.updated_at = time.time()
            if status == TaskStatus.RUNNING:
                task.started_at = task.updated_at
            if status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                task.finished_at = task.updated_at
