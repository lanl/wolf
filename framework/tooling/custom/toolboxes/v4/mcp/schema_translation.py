from __future__ import annotations

from typing import Any, Dict


def mcp_tool_to_wolf_spec_payload(mcp_tool: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a minimal MCP tool descriptor to ToolSpec constructor fields.

    This placeholder intentionally handles only generic dict shapes. Full MCP
    protocol support should be added after selecting the MCP Python dependency.
    """
    return {
        'name': mcp_tool.get('name', 'mcp_tool'),
        'description': mcp_tool.get('description', ''),
        'input_schema': mcp_tool.get('inputSchema') or mcp_tool.get('input_schema') or {},
        'capabilities': ['mcp'],
        'metadata': {'mcp_tool': mcp_tool},
    }
