# Custom Tools v4

This package contains protocol-specific runtime adapters for Toolbox v4.

The separation is intentional:

- `custom/toolboxes/v4` owns lifecycle, registry, deployment, planning, readiness, and orchestration.
- `custom/tools/v4` owns concrete endpoint invocation mechanics.

Current adapters:

- `CliToolAdapter` for CLI/subprocess endpoints.
- `PythonFunctionToolAdapter` for `module:function` endpoints.
- `McpToolAdapter` for stdio MCP endpoints when the official `mcp` SDK is installed.
- `HttpToolAdapter` placeholder.

Toolbox v4 runtime delegates endpoint execution to `ToolAdapterRegistry`.
