from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence
import time
import uuid


class TaskStatus(str, Enum):
    PENDING = 'pending'
    READY = 'ready'
    RUNNING = 'running'
    WAITING = 'waiting'
    PAUSED = 'paused'
    BLOCKED = 'blocked'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class WaitPolicy(str, Enum):
    ALL = 'all'
    ANY = 'any'
    NONE = 'none'


@dataclass(slots=True)
class ResourceBudget:
    max_depth: int = 8
    max_total_tasks: int = 1024
    max_active_tasks: int = 32
    max_children_per_task: int = 64
    max_subtree_tasks: int = 256
    max_retries_per_task: int = 2


@dataclass(slots=True)
class AgentRequirements:
    capabilities: Sequence[str] = field(default_factory=list)
    preferred_agent_names: Sequence[str] = field(default_factory=list)
    model_family: Optional[str] = None
    min_context_window: Optional[int] = None


@dataclass(slots=True)
class TaskSpec:
    name: str
    objective: str
    workflow_type: str = 'generic'
    inputs: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    priority: int = 0
    tags: List[str] = field(default_factory=list)
    requirements: AgentRequirements = field(default_factory=AgentRequirements)
    allowed_actions: Optional[List[str]] = None
    max_retries: Optional[int] = None
    session_id: Optional[str] = None


@dataclass(slots=True)
class SummaryCapsule:
    task_id: str
    objective: str
    outcome: str
    important_findings: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    confidence: float = 0.5
    artifact_refs: List[str] = field(default_factory=list)
    local_context_digest: str = ''
    child_summary_digest: str = ''
    next_steps: List[str] = field(default_factory=list)


@dataclass(slots=True)
class TaskResult:
    summary: str
    artifacts: Dict[str, Any] = field(default_factory=dict)
    facts: List[Dict[str, Any]] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    summary_capsule: Optional[SummaryCapsule] = None


@dataclass(slots=True)
class ContextCapsule:
    task_id: str
    objective: str
    workflow_type: str
    local_messages: List[Dict[str, Any]] = field(default_factory=list)
    local_facts: List[Dict[str, Any]] = field(default_factory=list)
    parent_summary: Optional[str] = None
    dependency_summaries: Dict[str, str] = field(default_factory=dict)
    child_summaries: Dict[str, str] = field(default_factory=dict)
    compressed_history: List[str] = field(default_factory=list)

    def add_message(self, role: str, content: Any) -> None:
        self.local_messages.append({'role': role, 'content': content, 'ts': time.time()})

    def add_fact(self, fact: Dict[str, Any]) -> None:
        self.local_facts.append(fact)


@dataclass(slots=True)
class TaskNode:
    spec: TaskSpec
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: TaskStatus = TaskStatus.PENDING
    retries: int = 0
    result: Optional[TaskResult] = None
    error: Optional[str] = None
    owner_agent_name: Optional[str] = None
    leased_agent_name: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    depth: int = 0
    lineage: List[str] = field(default_factory=list)
    waiting_policy: Optional[WaitPolicy] = None
    waiting_on: List[str] = field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass(slots=True)
class EngineConfig:
    budgets: ResourceBudget = field(default_factory=ResourceBudget)
    scheduler_poll_interval: float = 0.05
    fail_fast: bool = False
    publish_local_events_to_shared_history: bool = False
    auto_resume_parents: bool = True
    run_sync_actions_in_thread: bool = True
    max_context_messages: int = 8
    max_context_facts: int = 10
    max_compressed_history: int = 6
    artifact_inline_limit: int = 512
