from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Sequence

from pydantic import BaseModel, Field


class BaseToolExecutionRequest(BaseModel):
    """Base request shape for dynamically loaded Tool implementations.

    Concrete tool versions may define richer request models, but loaders and
    adapters can rely on these minimum fields.
    """

    protocol: str
    entrypoint: Optional[str] = None
    uri: Optional[str] = None
    invocation: Dict[str, Any] = Field(default_factory=dict)
    args: Optional[Sequence[str]] = None
    fn_args: Optional[Sequence[Any]] = None
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None
    timeout: Optional[float] = None
    input_data: Optional[str | bytes] = None
    text: bool = True


class BaseToolExecutionResult(BaseModel):
    """Base result shape for dynamically loaded Tool implementations."""

    ok: bool
    returncode: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    result: Any = None
    error: Optional[str] = None
    logs: list[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseTool(ABC):
    """Minimal interface for executable tool/runtime implementations."""

    implementation_name: str = "base_tool"
    implementation_version: str = "0.0.0"

    @abstractmethod
    def execute(self, request: Any) -> Any:
        """Execute a request and return a result object."""
        raise NotImplementedError

    def supports(self, protocol: str) -> bool:
        """Return whether this tool implementation supports a protocol."""
        return False

    def describe(self) -> Dict[str, Any]:
        return {
            "implementation_name": self.implementation_name,
            "implementation_version": self.implementation_version,
            "class": type(self).__name__,
        }


class BaseToolAdapter(BaseTool):
    """Base class for protocol-specific adapters."""

    protocol: str = "unknown"

    def supports(self, protocol: str) -> bool:
        return protocol == self.protocol
