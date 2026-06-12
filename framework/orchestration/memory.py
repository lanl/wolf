from __future__ import annotations

from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Optional
import asyncio

from .models import ContextCapsule, SummaryCapsule


class MemoryStore:
    def __init__(self, max_compressed_history: int = 6) -> None:
        self.task_memories: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.workflow_facts: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.summary_capsules: Dict[str, SummaryCapsule] = {}
        self.context_capsules: Dict[str, ContextCapsule] = {}
        self.max_compressed_history = max_compressed_history
        self._lock = asyncio.Lock()

    async def ensure_context(self, task_id: str, objective: str, workflow_type: str) -> ContextCapsule:
        async with self._lock:
            if task_id not in self.context_capsules:
                self.context_capsules[task_id] = ContextCapsule(task_id=task_id, objective=objective, workflow_type=workflow_type)
            return self.context_capsules[task_id]

    async def set_parent_summary(self, task_id: str, summary: str) -> None:
        async with self._lock:
            if task_id in self.context_capsules:
                self.context_capsules[task_id].parent_summary = summary

    async def add_local_message(self, task_id: str, role: str, content: Any) -> None:
        async with self._lock:
            self.context_capsules[task_id].add_message(role, content)

    async def add_local_fact(self, task_id: str, fact: Dict[str, Any]) -> None:
        async with self._lock:
            self.context_capsules[task_id].add_fact(fact)
            self.task_memories[task_id].append(fact)

    async def put_summary(self, task_id: str, summary: SummaryCapsule) -> None:
        async with self._lock:
            self.summary_capsules[task_id] = summary
            capsule = self.context_capsules.get(task_id)
            if capsule is not None:
                capsule.compressed_history.append(summary.outcome)
                capsule.compressed_history = capsule.compressed_history[-self.max_compressed_history:]

    async def get_summary(self, task_id: str) -> Optional[SummaryCapsule]:
        async with self._lock:
            return self.summary_capsules.get(task_id)

    async def get_context(self, task_id: str) -> ContextCapsule:
        async with self._lock:
            return self.context_capsules[task_id]

    async def attach_child_summary(self, parent_task_id: str, child_task_id: str, summary: SummaryCapsule) -> None:
        async with self._lock:
            capsule = self.context_capsules[parent_task_id]
            capsule.child_summaries[child_task_id] = summary.outcome
            digest = self._summary_digest(summary)
            capsule.compressed_history.append(f'child:{child_task_id[:8]} {digest}')
            capsule.compressed_history = capsule.compressed_history[-self.max_compressed_history:]

    async def attach_dependency_summary(self, task_id: str, dep_task_id: str, summary: SummaryCapsule) -> None:
        async with self._lock:
            capsule = self.context_capsules[task_id]
            capsule.dependency_summaries[dep_task_id] = summary.outcome

    async def compressed_snapshot(self, task_id: str) -> List[str]:
        async with self._lock:
            return list(self.context_capsules[task_id].compressed_history)

    def _summary_digest(self, summary: SummaryCapsule) -> str:
        findings = '; '.join(summary.important_findings[:2]) if summary.important_findings else summary.outcome
        if len(findings) > 140:
            findings = findings[:137] + '...'
        return findings
