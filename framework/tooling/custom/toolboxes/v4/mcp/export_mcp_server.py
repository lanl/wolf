from __future__ import annotations

from typing import Any, Dict, Optional


def _dependency_status() -> Dict[str, Any]:
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
        return {"available": True, "error": None}
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}"}


def build_mcp_server(toolbox, server_name: Optional[str] = None):
    """Build a FastMCP server exposing a Toolbox v4 instance.

    This returns the FastMCP object rather than running it, so callers can pick
    stdio/SSE/HTTP behavior according to the MCP SDK version and deployment
    context.
    """
    dep = _dependency_status()
    if not dep["available"]:
        raise RuntimeError(
            "The official `mcp` Python package with FastMCP support is not importable in this environment."
        )

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(server_name or getattr(toolbox, "name", "wolf_toolbox_v4"))

    @mcp.tool()
    def toolbox_list_tools() -> Dict[str, Any]:
        """List tools registered in the WOLF Toolbox."""
        return {"tools": toolbox.list_tools(), "stats": toolbox.get_stats()}

    @mcp.tool()
    def toolbox_search_tools(query: str, k: int = 10) -> Dict[str, Any]:
        """Search tools registered in the WOLF Toolbox."""
        return {"results": toolbox.search_tools(query, k=k)}

    @mcp.tool()
    def toolbox_tool_info(tool_id_or_name: str) -> Dict[str, Any]:
        """Get detailed information about a WOLF Toolbox tool."""
        info = toolbox.tool_info(tool_id_or_name)
        return {"ok": info is not None, "info": info}

    @mcp.tool()
    def toolbox_execute_tool(tool_id_or_name: str, args: Optional[list[str]] = None, kwargs: Optional[dict] = None) -> Dict[str, Any]:
        """Execute a WOLF Toolbox tool by name/id. MVP supports CLI/Python/MCP endpoints."""
        run = toolbox.execute_tool(tool_id_or_name, args=args, kwargs=kwargs or {})
        if hasattr(run, "model_dump"):
            return run.model_dump(mode="json")
        return run.dict()

    return mcp


def serve_toolbox_as_mcp(toolbox, server_name: Optional[str] = None, run: bool = False, transport: str = "stdio", **run_kwargs: Any) -> Dict[str, Any]:
    """Expose Toolbox v4 through an MCP server.

    If `run=False` returns readiness metadata and the server object. If
    `run=True`, attempts to call FastMCP.run(...). Because MCP SDK run
    signatures have changed across versions, failures are returned clearly.
    """
    dep = _dependency_status()
    if not dep["available"]:
        return {
            "ok": False,
            "status": "missing_dependency",
            "message": "The official `mcp` Python package with FastMCP support is not importable in this environment.",
            "dependency": dep,
            "toolbox": getattr(toolbox, "name", None),
        }
    try:
        server = build_mcp_server(toolbox, server_name=server_name)
        if not run:
            return {
                "ok": True,
                "status": "server_built",
                "message": "FastMCP server object built. Call with run=True to start it in a host process.",
                "toolbox": getattr(toolbox, "name", None),
                "server_name": server_name or getattr(toolbox, "name", "wolf_toolbox_v4"),
                "server_object_type": type(server).__name__,
            }
        try:
            result = server.run(transport=transport, **run_kwargs)
        except TypeError:
            result = server.run(**run_kwargs)
        return {"ok": True, "status": "stopped", "result": str(result)}
    except Exception as e:
        return {"ok": False, "status": "error", "message": str(e), "error_type": type(e).__name__}
