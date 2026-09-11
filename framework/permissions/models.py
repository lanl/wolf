from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now().isoformat()


class PermissionRequest(BaseModel):
    """Transport-neutral request for user approval before a risky action executes."""

    id: str = Field(default_factory=lambda: f"perm_{int(time.time() * 1000)}")
    kind: str = Field(description="Permission kind, e.g. run_syscall, write_file, tool_execution")
    action: Optional[str] = Field(default=None, description="Agent action that triggered the request")
    payload: dict[str, Any] = Field(default_factory=dict, description="Sanitized action payload")
    payload_summary: Optional[str] = Field(default=None, description="Human-readable short payload summary")

    resolved_command: Any = None
    command_display: Optional[str] = None
    target_path: Optional[str] = None
    operation: Optional[str] = None
    shell: Optional[bool] = None
    timeout: Optional[int] = None
    cwd: Optional[str] = None

    purpose: Optional[str] = None
    expectations: Optional[str] = None
    risk_hints: list[str] = Field(default_factory=list)

    session_dir: Optional[str] = None
    agent: Optional[str] = None
    actor: Optional[str] = None
    requested_at: float = Field(default_factory=time.time)
    requested_at_iso: str = Field(default_factory=_now_iso)

    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_data(
        cls,
        data: dict[str, Any] | None = None,
        *,
        kind: Optional[str] = None,
        action: Optional[str] = None,
    ) -> "PermissionRequest":
        payload = dict(data or {})
        if kind and not payload.get("kind"):
            payload["kind"] = kind
        if action and not payload.get("action"):
            payload["action"] = action
        if not payload.get("kind"):
            payload["kind"] = str(payload.get("action") or "unknown")
        return cls(**payload)

    def display_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "action": self.action,
            "payload_summary": self.payload_summary,
            "command": self.command_display,
            "target_path": self.target_path,
            "operation": self.operation,
            "shell": self.shell,
            "timeout": self.timeout,
            "cwd": self.cwd,
            "purpose": self.purpose,
            "expectations": self.expectations,
            "risk_hints": self.risk_hints,
            "session_dir": self.session_dir,
            "agent": self.agent,
            "requested_at": self.requested_at_iso,
        }


class PermissionDecision(BaseModel):
    """Decision returned by a human approval backend."""

    request_id: Optional[str] = None
    kind: Optional[str] = None
    approved: bool = False
    approve_for_session: bool = False
    reason: Optional[str] = None
    feedback: Optional[str] = None
    source: str = "unknown"
    status: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: float = Field(default_factory=time.time)
    decided_at_iso: str = Field(default_factory=_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_data(
        cls,
        data: dict[str, Any] | None = None,
        *,
        request: PermissionRequest | None = None,
    ) -> "PermissionDecision":
        payload = dict(data or {})
        if request is not None:
            payload.setdefault("request_id", request.id)
            payload.setdefault("kind", request.kind)
        if "approved" not in payload:
            status = str(payload.get("status") or "").lower()
            payload["approved"] = status == "approved"
        return cls(**payload)

    def as_legacy_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")