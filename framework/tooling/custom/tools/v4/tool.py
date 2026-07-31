from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

from .tool_models import ToolExecutionRequest, ToolExecutionResult


class ToolAdapter(ABC):
    """Base runtime adapter for a concrete endpoint protocol.

    ToolBox v4 owns lifecycle/planning/registry concerns. Tool adapters own the
    mechanics of invoking a particular endpoint protocol.
    """

    protocol: str = "unknown"

    def supports(self, protocol: str) -> bool:
        return protocol == self.protocol

    @abstractmethod
    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        raise NotImplementedError


class CliToolAdapter(ToolAdapter):
    protocol = "cli"

    def supports(self, protocol: str) -> bool:
        return protocol in {"cli", "subprocess"}

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        try:
            cmd = self._build_command(request.entrypoint or request.uri, request.args, request.invocation)
            completed = subprocess.run(
                cmd,
                input=request.input_data,
                capture_output=True,
                text=request.text,
                env={**os.environ, **(request.env or {})},
                cwd=request.cwd,
                timeout=request.timeout,
                check=False,
            )
            return ToolExecutionResult(
                ok=completed.returncode == 0,
                returncode=int(completed.returncode),
                stdout=completed.stdout,
                stderr=completed.stderr,
                logs=[f"Executed command: {cmd}"],
                metadata={"cmd": cmd},
            )
        except Exception as e:
            return ToolExecutionResult(ok=False, returncode=-1, stderr=str(e), error=str(e))

    def _build_command(
        self,
        entrypoint: Optional[str],
        args: Optional[Sequence[str]],
        invocation: Optional[Dict[str, Any]],
    ) -> List[str]:
        if not entrypoint:
            raise RuntimeError("CLI/subprocess endpoint requires entrypoint or uri")
        invocation = invocation or {}
        prefix = invocation.get("command_prefix") or []
        if isinstance(prefix, str):
            prefix = prefix.split()
        cmd = list(prefix) + [entrypoint]
        if args:
            cmd.extend([str(a) for a in args])
        return cmd


class PythonFunctionToolAdapter(ToolAdapter):
    protocol = "python_function"

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        try:
            if not request.entrypoint:
                raise RuntimeError("Python function endpoint requires entrypoint module:function")
            module_path, func_name = request.entrypoint.split(":", 1)
            mod = importlib.import_module(module_path)
            fn = getattr(mod, func_name)
            if inspect.iscoroutinefunction(fn):
                # Keep sync API simple for now; run in a private loop.
                result = asyncio.run(fn(*(request.fn_args or []), **(request.kwargs or {})))
            else:
                result = fn(*(request.fn_args or []), **(request.kwargs or {}))
            return ToolExecutionResult(ok=True, returncode=0, result=result)
        except Exception as e:
            return ToolExecutionResult(ok=False, returncode=-1, stderr=str(e), error=str(e))


class HttpToolAdapter(ToolAdapter):
    protocol = "http"

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            ok=False,
            returncode=-1,
            error="HTTP endpoint execution is not implemented yet in custom.tools.v4.",
            stderr="HTTP endpoint execution is not implemented yet in custom.tools.v4.",
        )


class McpToolAdapter(ToolAdapter):
    protocol = "mcp"

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        try:
            invocation = request.invocation or {}
            transport = invocation.get("transport", "stdio")
            if transport != "stdio":
                raise NotImplementedError(f"Unsupported MCP transport: {transport}")
            result = asyncio.run(self._call_mcp_tool_stdio(
                command=invocation.get("command"),
                args=invocation.get("args") or [],
                env=invocation.get("env") or None,
                tool_name=invocation.get("tool_name") or request.entrypoint,
                arguments=request.kwargs or {},
            ))
            return ToolExecutionResult(ok=True, returncode=0, result=result)
        except Exception as e:
            return ToolExecutionResult(ok=False, returncode=-1, stderr=str(e), error=str(e))

    async def _call_mcp_tool_stdio(
        self,
        command: str,
        args: Optional[Sequence[str]],
        env: Optional[Dict[str, str]],
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Any:
        if not command:
            raise RuntimeError("MCP stdio endpoint requires invocation.command")
        if not tool_name:
            raise RuntimeError("MCP endpoint requires invocation.tool_name or endpoint.entrypoint")
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except Exception as e:
            raise RuntimeError(
                "The official `mcp` Python package is required for MCP execution but is not importable in this environment."
            ) from e

        server_params = StdioServerParameters(command=command, args=list(args or []), env=env)
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments or {})
                if hasattr(result, "model_dump"):
                    return result.model_dump(mode="json")
                if hasattr(result, "dict"):
                    return result.dict()
                return result


class ToolAdapterRegistry:
    """Protocol -> adapter resolver used by ToolBox v4 runtime."""

    def __init__(self, adapters: Optional[Sequence[ToolAdapter]] = None):
        self.adapters: List[ToolAdapter] = list(adapters or [
            CliToolAdapter(),
            PythonFunctionToolAdapter(),
            HttpToolAdapter(),
            McpToolAdapter(),
        ])

    def register(self, adapter: ToolAdapter) -> None:
        self.adapters.append(adapter)

    def get(self, protocol: str) -> ToolAdapter:
        for adapter in self.adapters:
            if adapter.supports(protocol):
                return adapter
        raise KeyError(f"No ToolAdapter registered for protocol: {protocol}")
