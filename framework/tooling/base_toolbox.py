from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BaseToolBoxParams(BaseModel):
    """Minimal params common to dynamically loaded ToolBox implementations."""

    name: str = "toolbox"
    root_dir: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseToolBox(ABC):
    """Minimal interface for ToolBox implementations.

    Concrete implementations can be much richer. This base only captures the
    operations that loaders and hosts can assume exist.
    """

    implementation_name: str = "base_toolbox"
    implementation_version: str = "0.0.0"

    @abstractmethod
    def list_tools(self, *args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def search_tools(self, query: str, k: int = 10, *args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def tool_info(self, tool_id_or_name: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def execute_tool(self, tool_id_or_name: str, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {
            "implementation_name": self.implementation_name,
            "implementation_version": self.implementation_version,
            "class": type(self).__name__,
            "stats": self.get_stats(),
        }
