from __future__ import annotations

"""Non-invasive adapter for hosting legacy and v4 ToolBoxes in Universes.

This module does not modify active Universe routes. It provides a small
compatibility layer so future Universe/actionbox integration can treat legacy
`framework.tooling.toolbox.ToolBox` and new `ToolBoxV4` instances through a
common shape.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class ToolBoxAdapterInfo:
    name: str
    implementation: str
    object_type: str
    supports_v4_planning: bool
    supports_readiness: bool
    supports_mcp: bool


class UniverseToolBoxAdapter:
    """Compatibility wrapper around a toolbox-like object.

    The adapter intentionally uses duck typing. It supports both the existing
    legacy ToolBox API and the new ToolBoxV4 API without requiring either class
    to inherit from a common base yet.
    """

    def __init__(self, name: str, toolbox: Any):
        self.name = name
        self.toolbox = toolbox

    def info(self) -> ToolBoxAdapterInfo:
        impl = getattr(self.toolbox, "implementation_name", type(self.toolbox).__name__)
        return ToolBoxAdapterInfo(
            name=self.name,
            implementation=str(impl),
            object_type=f"{type(self.toolbox).__module__}.{type(self.toolbox).__name__}",
            supports_v4_planning=hasattr(self.toolbox, "plan_execution"),
            supports_readiness=hasattr(self.toolbox, "test_tool") or hasattr(self.toolbox, "mark_ready"),
            supports_mcp=hasattr(self.toolbox, "import_mcp_server") or hasattr(self.toolbox, "serve_mcp"),
        )

    def list_tools(self) -> List[Dict[str, Any]]:
        if hasattr(self.toolbox, "list_tools"):
            result = self.toolbox.list_tools()
            # Legacy ToolBox returns list[str]; v4 returns list[dict]. Normalize.
            if result and isinstance(result[0], str):
                return [{"tool_name": name, "name": name} for name in result]
            return list(result or [])
        raise AttributeError(f"ToolBox {self.name} does not support list_tools")

    def search_tools(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        if hasattr(self.toolbox, "search_tools"):
            return list(self.toolbox.search_tools(query, k=k) or [])
        # Fallback: simple substring over list_tools summaries.
        q = query.lower()
        hits = []
        for item in self.list_tools():
            hay = str(item).lower()
            if q in hay:
                hits.append(item)
        return hits[:k]

    def tool_info(self, tool_id_or_name: str) -> Optional[Dict[str, Any]]:
        if hasattr(self.toolbox, "tool_info"):
            return self.toolbox.tool_info(tool_id_or_name)
        if hasattr(self.toolbox, "get_tool"):
            tool = self.toolbox.get_tool(tool_id_or_name)
            if tool is None:
                return None
            if hasattr(tool, "info"):
                return tool.info()
            return {"tool": repr(tool)}
        return None

    def get_stats(self) -> Dict[str, Any]:
        if hasattr(self.toolbox, "get_stats"):
            return self.toolbox.get_stats()
        return {
            "name": self.name,
            "num_tools": len(self.list_tools()),
            "adapter_info": self.info().__dict__,
        }

    def execute_tool(self, tool_id_or_name: str, *fn_args: Any, args: Optional[Sequence[str]] = None, kwargs: Optional[Dict[str, Any]] = None, **exec_kwargs: Any) -> Any:
        if hasattr(self.toolbox, "execute_tool"):
            return self.toolbox.execute_tool(tool_id_or_name, *fn_args, args=args, kwargs=kwargs, **exec_kwargs)
        raise AttributeError(f"ToolBox {self.name} does not support execute_tool")

    def plan_execution(self, tool_id_or_name: str, **kwargs: Any) -> Any:
        if hasattr(self.toolbox, "plan_execution"):
            return self.toolbox.plan_execution(tool_id_or_name, **kwargs)
        return {
            "ok": False,
            "status": "unsupported",
            "message": "This toolbox implementation does not expose plan_execution.",
            "tool": tool_id_or_name,
            "adapter_info": self.info().__dict__,
        }

    def test_tool(self, tool_id_or_name: str) -> Any:
        if hasattr(self.toolbox, "test_tool"):
            return self.toolbox.test_tool(tool_id_or_name)
        return {
            "ok": False,
            "status": "unsupported",
            "message": "This toolbox implementation does not expose test_tool/readiness checks.",
            "tool": tool_id_or_name,
            "adapter_info": self.info().__dict__,
        }


class UniverseToolBoxRegistryAdapter:
    """Adapter over a Universe-like object containing a `TBs` registry."""

    def __init__(self, universe: Any):
        self.universe = universe

    def list_tbs(self) -> List[str]:
        if hasattr(self.universe, "list_tbs"):
            return list(self.universe.list_tbs())
        return sorted(getattr(self.universe, "TBs", {}).keys())

    def get_adapter(self, tb_name: str) -> UniverseToolBoxAdapter:
        if hasattr(self.universe, "get_tb"):
            tb = self.universe.get_tb(tb_name)
        else:
            tb = getattr(self.universe, "TBs", {})[tb_name]
        return UniverseToolBoxAdapter(tb_name, tb)

    def list_tools(self, tb_name: str) -> List[Dict[str, Any]]:
        return self.get_adapter(tb_name).list_tools()

    def search_tools(self, tb_name: str, query: str, k: int = 5) -> List[Dict[str, Any]]:
        return self.get_adapter(tb_name).search_tools(query, k=k)

    def tool_info(self, tb_name: str, tool_id_or_name: str) -> Optional[Dict[str, Any]]:
        return self.get_adapter(tb_name).tool_info(tool_id_or_name)

    def execute_tool(self, tb_name: str, tool_id_or_name: str, **kwargs: Any) -> Any:
        return self.get_adapter(tb_name).execute_tool(tool_id_or_name, **kwargs)

    def toolbox_stats(self, tb_name: str) -> Dict[str, Any]:
        return self.get_adapter(tb_name).get_stats()

    def registry_info(self) -> Dict[str, Any]:
        tbs = {}
        for name in self.list_tbs():
            adapter = self.get_adapter(name)
            tbs[name] = adapter.info().__dict__
        return {"toolboxes": tbs, "count": len(tbs)}
