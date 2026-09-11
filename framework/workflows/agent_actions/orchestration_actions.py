from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from framework.workflows.base_agent_action import AgentAction
from framework.workflows.agent_actions.formatting_utils import coerce_float, coerce_int, resolve_text_source


class OrchestrationTaskSpecPayload(BaseModel):
    """Model-facing subset of TaskSpec for creating orchestration children."""

    name: str = Field(description="Short child task name")
    objective: Optional[str] = Field(default=None, description="Clear objective for the child task")
    objective_lines: Optional[List[str]] = Field(default=None, description="Safer objective transport; joined with newline characters")
    objective_base64: Optional[str] = Field(default=None, description="Base64 encoded UTF-8 objective")
    workflow_type: str = Field(default="chat", description="Workflow type for the child task")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Optional task-local input payload")
    dependencies: List[str] = Field(default_factory=list, description="Optional dependency task ids")
    priority: int = Field(default=0, description="Scheduler priority hint")
    tags: List[str] = Field(default_factory=list, description="Optional task tags")

    @model_validator(mode="before")
    @classmethod
    def normalize_scalars(cls, data: Any):
        if isinstance(data, dict) and "priority" in data:
            data = dict(data)
            data["priority"] = coerce_int(data["priority"])
        return data

    @model_validator(mode="after")
    def normalize_objective_transport(self):
        self.objective = resolve_text_source(text=self.objective, lines=self.objective_lines, base64_text=self.objective_base64, field_label="objective", required=True)
        self.objective_lines = None
        self.objective_base64 = None
        return self


class CreateSubtasksPayload(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"subtasks": [{"name": "child", "objective_lines": ["Do focused work"], "workflow_type": "chat", "inputs": {}}], "wait_policy": "all", "summary_lines": ["Delegating work"], "rationale_lines": ["Parallelize safely"]}]})

    subtasks: List[OrchestrationTaskSpecPayload] = Field(description="Child tasks to create under the current orchestration task")
    wait_policy: Literal["all", "any", "none"] = Field(default="all", description="Whether the parent waits for all, any, or none of the children")
    summary: Optional[str] = Field(default=None, description="Short parent-thread summary of the delegation")
    summary_lines: Optional[List[str]] = Field(default=None, description="Safer summary transport; joined with newline characters")
    summary_base64: Optional[str] = Field(default=None, description="Base64 encoded UTF-8 summary")
    rationale: Optional[str] = Field(default=None, description="Why these children are being created")
    rationale_lines: Optional[List[str]] = Field(default=None, description="Safer rationale transport; joined with newline characters")
    rationale_base64: Optional[str] = Field(default=None, description="Base64 encoded UTF-8 rationale")

    @model_validator(mode="after")
    def normalize_text_transports(self):
        self.summary = resolve_text_source(text=self.summary, lines=self.summary_lines, base64_text=self.summary_base64, field_label="summary", required=False) or ""
        self.rationale = resolve_text_source(text=self.rationale, lines=self.rationale_lines, base64_text=self.rationale_base64, field_label="rationale", required=False) or ""
        self.summary_lines = None
        self.summary_base64 = None
        self.rationale_lines = None
        self.rationale_base64 = None
        return self


class CreateSubtasksGatewayAction(AgentAction):
    action: Literal["create_subtasks"] = "create_subtasks"
    description: Literal["Create orchestration child tasks under the current task"] = "Create orchestration child tasks under the current task"
    payload: CreateSubtasksPayload
    payload_schema: str = """{"subtasks": [{"name": "short name", "objective_lines": ["specific child objective"], "workflow_type": "chat", "inputs": {}}], "wait_policy": "all|any|none", "summary_lines": ["delegation summary"], "rationale_lines": ["why these children are needed"]}"""


class WaitForTasksPayload(BaseModel):
    wait_policy: Literal["all", "any", "none"] = Field(default="all", description="Child/dependency wait policy")


class WaitForTasksGatewayAction(AgentAction):
    action: Literal["wait_for_tasks"] = "wait_for_tasks"
    description: Literal["Wait for current child/dependency tasks before continuing"] = "Wait for current child/dependency tasks before continuing"
    payload: WaitForTasksPayload = Field(default_factory=WaitForTasksPayload)
    payload_schema: str = """{"wait_policy": "all|any|none"}"""


class CompleteTaskPayload(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"summary_lines": ["Final outcome"], "facts": [], "artifacts": {}, "trace": [], "confidence": 0.8, "important_findings": [], "blockers": []}]})

    summary: Optional[str] = Field(default=None, description="Final task outcome summary")
    summary_lines: Optional[List[str]] = Field(default=None, description="Safer summary transport; joined with newline characters")
    summary_base64: Optional[str] = Field(default=None, description="Base64 encoded UTF-8 summary")
    facts: List[Dict[str, Any]] = Field(default_factory=list, description="Structured facts learned while completing the task")
    artifacts: Dict[str, Any] = Field(default_factory=dict, description="Artifact payloads or references")
    trace: List[Dict[str, Any]] = Field(default_factory=list, description="Important trace metadata")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence in the result")
    important_findings: List[str] = Field(default_factory=list, description="Important findings to propagate upward")
    blockers: List[str] = Field(default_factory=list, description="Known blockers or caveats")

    @model_validator(mode="before")
    @classmethod
    def normalize_scalars(cls, data: Any):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "confidence" in data:
            data["confidence"] = coerce_float(data["confidence"])

        # Orchestration completion payloads are often produced under high
        # context pressure. Be permissive about common model slips while keeping
        # the canonical payload shape expected by CompleteTaskAction.
        artifacts = data.get("artifacts")
        if artifacts is None:
            data["artifacts"] = {}
        elif isinstance(artifacts, list):
            data["artifacts"] = {"items": artifacts}
        elif not isinstance(artifacts, dict):
            data["artifacts"] = {"value": artifacts}

        for key in ("facts", "trace"):
            value = data.get(key)
            if value is None:
                data[key] = []
            elif isinstance(value, dict):
                data[key] = [value]
            elif isinstance(value, str):
                data[key] = [{"text": value}]
            elif isinstance(value, list):
                normalized_items = []
                for item in value:
                    if isinstance(item, dict):
                        normalized_items.append(item)
                    elif isinstance(item, str):
                        normalized_items.append({"text": item})
                    else:
                        normalized_items.append({"value": item})
                data[key] = normalized_items
            else:
                data[key] = [{"value": value}]

        for key in ("important_findings", "blockers"):
            value = data.get(key)
            if value is None:
                data[key] = []
            elif isinstance(value, str):
                data[key] = [value]
            elif isinstance(value, tuple):
                data[key] = list(value)
        return data

    @model_validator(mode="after")
    def normalize_summary_transport(self):
        self.summary = resolve_text_source(text=self.summary, lines=self.summary_lines, base64_text=self.summary_base64, field_label="summary", required=True)
        self.summary_lines = None
        self.summary_base64 = None
        return self


class CompleteTaskGatewayAction(AgentAction):
    action: Literal["complete_task"] = "complete_task"
    description: Literal["Complete the current orchestration task with a structured summary"] = "Complete the current orchestration task with a structured summary"
    payload: CompleteTaskPayload
    payload_schema: str = """{"summary_lines": ["final outcome"], "facts": [], "artifacts": {}, "trace": [], "confidence": 0.8, "important_findings": [], "blockers": []}"""


class PublishProgressPayload(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"message_lines": ["progress update"], "payload": {}}]})

    message: Optional[str] = Field(default=None, description="Progress update message")
    message_lines: Optional[List[str]] = Field(default=None, description="Safer message transport; joined with newline characters")
    message_base64: Optional[str] = Field(default=None, description="Base64 encoded UTF-8 message")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Optional structured progress metadata")

    @model_validator(mode="after")
    def normalize_message_transport(self):
        self.message = resolve_text_source(text=self.message, lines=self.message_lines, base64_text=self.message_base64, field_label="message", required=False) or ""
        self.message_lines = None
        self.message_base64 = None
        return self


class PublishProgressGatewayAction(AgentAction):
    action: Literal["publish_progress"] = "publish_progress"
    description: Literal["Publish orchestration task progress without completing the task"] = "Publish orchestration task progress without completing the task"
    payload: PublishProgressPayload = Field(default_factory=PublishProgressPayload)
    payload_schema: str = """{"message_lines": ["progress update"], "payload": {}}"""


class FailTaskPayload(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"error_lines": ["failure reason"]}]})

    error: Optional[str] = Field(default=None, description="Reason the current task cannot be completed")
    error_lines: Optional[List[str]] = Field(default=None, description="Safer error transport; joined with newline characters")
    error_base64: Optional[str] = Field(default=None, description="Base64 encoded UTF-8 error")

    @model_validator(mode="after")
    def normalize_error_transport(self):
        self.error = resolve_text_source(text=self.error, lines=self.error_lines, base64_text=self.error_base64, field_label="error", required=True)
        self.error_lines = None
        self.error_base64 = None
        return self


class FailTaskGatewayAction(AgentAction):
    action: Literal["fail_task"] = "fail_task"
    description: Literal["Fail the current orchestration task with an explicit error"] = "Fail the current orchestration task with an explicit error"
    payload: FailTaskPayload
    payload_schema: str = """{"error_lines": ["failure reason"]}"""
