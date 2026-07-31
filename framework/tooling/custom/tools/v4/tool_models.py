from __future__ import annotations

from typing import Any, Dict, Optional, Sequence
from pydantic import BaseModel, Field


class ToolExecutionRequest(BaseModel):
    """Runtime-level request passed from a ToolBox execution plan to a Tool adapter."""

    endpoint_id: str
    protocol: str
    uri: Optional[str] = None
    entrypoint: Optional[str] = None
    invocation: Dict[str, Any] = Field(default_factory=dict)
    args: Optional[Sequence[str]] = None
    fn_args: Optional[Sequence[Any]] = None
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None
    timeout: Optional[float] = None
    input_data: Optional[str | bytes] = None
    text: bool = True


class ToolExecutionResult(BaseModel):
    """Runtime-level result returned by a Tool adapter."""

    ok: bool
    returncode: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    result: Any = None
    logs: list[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
