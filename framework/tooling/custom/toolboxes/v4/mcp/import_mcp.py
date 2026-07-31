from __future__ import annotations

import asyncio
import shutil
from typing import Any, Dict, List, Optional

from ..tool_models import (
    DeploymentKind,
    EndpointProtocol,
    LifecycleStatus,
    LocalityKind,
    LocalityRef,
    SourceKind,
)
from .schema_translation import mcp_tool_to_wolf_spec_payload


def _dependency_status() -> Dict[str, Any]:
    try:
        import mcp  # noqa: F401
        from mcp import ClientSession, StdioServerParameters  # noqa: F401
        from mcp.client.stdio import stdio_client  # noqa: F401
        return {"available": True, "error": None}
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}"}


async def _list_mcp_tools_stdio(
    command: str,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command=command,
        args=args or [],
        env=env,
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tools = getattr(result, "tools", result)
            out: List[Dict[str, Any]] = []
            for tool in tools:
                if hasattr(tool, "model_dump"):
                    out.append(tool.model_dump(mode="json"))
                elif hasattr(tool, "dict"):
                    out.append(tool.dict())
                else:
                    out.append({
                        "name": getattr(tool, "name", None),
                        "description": getattr(tool, "description", ""),
                        "inputSchema": getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {},
                    })
            return out


def list_mcp_tools(
    command: str,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    transport: str = "stdio",
) -> Dict[str, Any]:
    """List tools exposed by an MCP server.

    Currently implements stdio transport using the official `mcp` Python SDK
    when installed. Other transports are planned.
    """
    dep = _dependency_status()
    if not dep["available"]:
        return {
            "ok": False,
            "status": "missing_dependency",
            "message": "The official `mcp` Python package is not importable in this environment. It is listed in environment.yml but not installed in the active interpreter.",
            "dependency": dep,
        }
    if transport != "stdio":
        return {
            "ok": False,
            "status": "unsupported_transport",
            "message": f"Only stdio MCP transport is implemented in Toolbox v4 MVP; got {transport!r}.",
        }
    if shutil.which(command) is None and "/" not in command:
        return {
            "ok": False,
            "status": "command_not_found",
            "message": f"MCP server command not found on PATH: {command}",
        }
    try:
        tools = asyncio.run(_list_mcp_tools_stdio(command, args=args, env=env))
        return {"ok": True, "status": "ok", "tools": tools, "count": len(tools)}
    except Exception as e:
        return {"ok": False, "status": "error", "message": str(e), "error_type": type(e).__name__}


def import_mcp_server(
    toolbox,
    name: str,
    command: str,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    transport: str = "stdio",
    locality: Optional[Dict[str, Any]] = None,
    mark_ready: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Import an MCP server's tools into Toolbox v4.

    For each MCP tool, this creates:
    - one ToolSource describing the MCP server,
    - one ToolSpec describing the MCP tool capability,
    - one ToolDeployment for the MCP server process,
    - one MCP ToolEndpoint with server invocation details.

    If the `mcp` package is unavailable, this fails gracefully and does not
    mutate the toolbox registries.
    """
    listed = list_mcp_tools(command=command, args=args, env=env, transport=transport)
    if not listed.get("ok"):
        return {
            "ok": False,
            "status": listed.get("status", "error"),
            "message": "Could not list MCP tools; import aborted.",
            "detail": listed,
            "toolbox": getattr(toolbox, "name", None),
        }

    tools = listed.get("tools", [])
    if dry_run:
        return {
            "ok": True,
            "status": "dry_run",
            "message": "MCP tools were listed but not registered.",
            "tools": tools,
            "count": len(tools),
        }

    loc = LocalityRef(**locality) if locality else LocalityRef(kind=LocalityKind.LOCAL, id="local")
    imported = []
    for mcp_tool in tools:
        tool_name = mcp_tool.get("name") or "mcp_tool"
        full_name = f"{name}.{tool_name}"
        source = toolbox.register_source(
            name=f"{name}_mcp_server",
            kind=SourceKind.MCP_SERVER,
            uri=command,
            entrypoint=command,
            description=f"MCP server imported from command: {command}",
            metadata={"transport": transport, "command": command, "args": args or [], "env_keys": sorted((env or {}).keys())},
        )
        spec_payload = mcp_tool_to_wolf_spec_payload(mcp_tool)
        spec_payload["name"] = full_name
        spec_payload["source_refs"] = [source.id]
        spec_payload["status"] = LifecycleStatus.REGISTERED
        spec_payload.setdefault("metadata", {})
        spec_payload["metadata"].update({"mcp_server_name": name, "mcp_tool_name": tool_name})
        spec = toolbox.register_tool_spec(**spec_payload)
        deployment = toolbox.register_deployment(
            tool_id=spec.id,
            name=f"{full_name}_mcp_stdio",
            kind=DeploymentKind.MCP_SERVER,
            locality=loc,
            status=LifecycleStatus.DEPLOYED,
            metadata={"transport": transport, "command": command, "args": args or []},
        )
        endpoint = toolbox.register_endpoint(
            tool_id=spec.id,
            deployment_id=deployment.id,
            protocol=EndpointProtocol.MCP,
            entrypoint=tool_name,
            invocation={
                "transport": transport,
                "command": command,
                "args": args or [],
                "env": env or {},
                "tool_name": tool_name,
            },
            input_schema=mcp_tool.get("inputSchema") or mcp_tool.get("input_schema") or {},
            status=LifecycleStatus.READY if mark_ready else LifecycleStatus.REGISTERED,
            metadata={"mcp_tool": mcp_tool},
        )
        if mark_ready:
            toolbox.mark_ready(spec.id)
        imported.append({
            "tool_id": spec.id,
            "tool_name": spec.name,
            "source_id": source.id,
            "deployment_id": deployment.id,
            "endpoint_id": endpoint.id,
        })

    return {
        "ok": True,
        "status": "imported",
        "server_name": name,
        "count": len(imported),
        "imported": imported,
    }
