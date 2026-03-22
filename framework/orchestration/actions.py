from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import TaskSpec, WaitPolicy


@dataclass(slots=True)
class OrchestrationAction:
    action: str


@dataclass(slots=True)
class CreateSubtasksAction(OrchestrationAction):
    subtasks: List[TaskSpec]
    wait_policy: WaitPolicy = WaitPolicy.ALL
    summary: str = 'Delegating work to subtasks'
    rationale: str = ''
    def __init__(self, subtasks: List[TaskSpec], wait_policy: WaitPolicy = WaitPolicy.ALL, summary: str = 'Delegating work to subtasks', rationale: str = '') -> None:
        self.action = 'create_subtasks'; self.subtasks = subtasks; self.wait_policy = wait_policy; self.summary = summary; self.rationale = rationale


@dataclass(slots=True)
class CompleteTaskAction(OrchestrationAction):
    summary: str
    facts: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.8
    important_findings: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    def __init__(self, summary: str, facts: Optional[List[Dict[str, Any]]] = None, artifacts: Optional[Dict[str, Any]] = None, trace: Optional[List[Dict[str, Any]]] = None, confidence: float = 0.8, important_findings: Optional[List[str]] = None, blockers: Optional[List[str]] = None) -> None:
        self.action = 'complete_task'; self.summary = summary; self.facts = facts or []; self.artifacts = artifacts or {}; self.trace = trace or []; self.confidence = confidence; self.important_findings = important_findings or []; self.blockers = blockers or []


@dataclass(slots=True)
class PublishProgressAction(OrchestrationAction):
    message: str
    payload: Dict[str, Any] = field(default_factory=dict)
    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.action = 'publish_progress'; self.message = message; self.payload = payload or {}


@dataclass(slots=True)
class WaitForTasksAction(OrchestrationAction):
    wait_policy: WaitPolicy = WaitPolicy.ALL
    def __init__(self, wait_policy: WaitPolicy = WaitPolicy.ALL) -> None:
        self.action = 'wait_for_tasks'; self.wait_policy = wait_policy


@dataclass(slots=True)
class RequestUserInputAction(OrchestrationAction):
    question: str
    reason: str = ''
    def __init__(self, question: str, reason: str = '') -> None:
        self.action = 'request_user_input'; self.question = question; self.reason = reason


@dataclass(slots=True)
class PauseTaskAction(OrchestrationAction):
    reason: str = ''
    def __init__(self, reason: str = '') -> None:
        self.action = 'pause_task'; self.reason = reason


@dataclass(slots=True)
class FailTaskAction(OrchestrationAction):
    error: str = 'unknown error'
    def __init__(self, error: str = 'unknown error') -> None:
        self.action = 'fail_task'; self.error = error


@dataclass(slots=True)
class TaskThreadMessageAction(OrchestrationAction):
    message: str
    role: str = "assistant"
    kind: str = "message"
    def __init__(self, message: str, role: str = "assistant", kind: str = "message") -> None:
        self.action = 'task_message'; self.message = message; self.role = role; self.kind = kind
