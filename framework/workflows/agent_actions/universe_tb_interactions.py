from __future__ import annotations

import os
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator
import requests
from framework.workflows.base_agent_action import AgentAction
from framework.universes.endpoint_resolver import get_universe_base_url_or_error
from framework.workflows.agent_actions.formatting_utils import coerce_bool, coerce_float, resolve_text_list_source, resolve_text_source

# Default timeout for all HTTP requests
DEFAULT_TIMEOUT = 30

# ---------------------------
# Create ToolBox Action
# ---------------------------
from framework.tooling.toolbox import ToolBox

class CreateToolBoxArgs(BaseModel):
    system: str = Field(description="System where the toolbox will be created, e.g., 'local'")
    name: str = Field(description="Name for the new ToolBox")
    params: dict = Field(default_factory=dict, description="Optional dict of parameters for ToolBox constructor")

class CreateToolBoxAction(AgentAction):
    """Create a ToolBox instance and register it in ``infra.managed_deployments``.

    The created ``ToolBox`` object is stored under the provided ``name``.
    """
    action: Literal["create_toolbox"] = "create_toolbox"
    description: Literal["Create and register a ToolBox"] = "Create and register a ToolBox"
    payload: CreateToolBoxArgs
    payload_schema: str = "{\n    \"system\": \"string\",\n    \"name\": \"string\",\n    \"params\": \"optional dict of ToolBox init args\"\n}"

    def execute(self, infra) -> None:
        # Instantiate ToolBox with given params (if any)
        tb = ToolBox(**self.payload.params)
        # Store in managed_deployments
        infra.managed_deployments[self.payload.name] = {
            "handle": tb,
            "params": self.payload.params,
            "meta_data": {
                "type": "toolbox",
                "status": "ready",
                "created_at": datetime.utcnow().isoformat(),
            },
        }
        infra.append_chat_history(
            actor="system",
            content=f"ToolBox '{self.payload.name}' created and registered.",
            action={"action": "create_toolbox"},
            log_console=True,
        )
        return


# ===========================
# ToolBox Interactions
# ===========================
class TBSearchArgs(BaseModel):
    #universe_url: str = Field(description="Base URL of the universe")
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    tb_name: str = Field(description="Name of the toolbox")
    query: str = Field(description="Search query")
    k: int = Field(default=5, description="Number of results to return")


class UniverseTBSearchToolsAction(AgentAction):
    """Search for tools in a toolbox."""
    action: Literal["universe_tb_search_tools"] = "universe_tb_search_tools"
    description: Literal["Search for tools in a toolbox based on query"] = "Search for tools in a toolbox based on query"
    payload: TBSearchArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>, 
                              "query": <string>, 
                              "k": <int> (optional, default=5)}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        univ_base_url = f"<unresolved:{univ_name}>"
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url, resolve_error, _resolution = get_universe_base_url_or_error(infra, univ_name)
            if resolve_error:
                raise RuntimeError(resolve_error)
            response = requests.post(
                f"{univ_base_url}/tbs/{self.payload.tb_name}/search",
                json={"query": self.payload.query, "k": self.payload.k},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                result = {"error": "Invalid response format, expected list", "action": self.action}
            result = {"results": result, "count": len(result)}
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ctx_msg = (f"[Universe: {univ_base_url}] Tool search results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class TBExecuteArgs(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"system": "local", "universe": "main", "tb_name": "tools", "tool_name": "tool", "args": ["--help"], "input_lines": ["stdin line"], "timeout": 30, "text": True}]})

    #universe_url: str = Field(description="Base URL of the universe")
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    tb_name: str = Field(description="Name of the toolbox")
    tool_name: str = Field(description="Name of the tool to execute")
    args: Optional[List[str]] = Field(default=None, description="Command-line arguments for the tool")
    fn_args: Optional[List[Any]] = Field(default=None, description="Function arguments for the tool")
    kwargs: Optional[Dict[str, Any]] = Field(default=None, description="Keyword arguments for the tool")
    env: Optional[Dict[str, str]] = Field(default=None, description="Environment variables")
    cwd: Optional[str] = Field(default=None, description="Working directory")
    timeout: Optional[float] = Field(default=None, description="Execution timeout in seconds")
    input_data: Optional[str] = Field(default=None, description="Input data to pass to the tool")
    input_lines: Optional[List[str]] = Field(default=None, description="Safer multiline stdin transport; joined with newline characters")
    input_base64: Optional[str] = Field(default=None, description="Base64 encoded UTF-8 stdin payload")
    text: bool = Field(default=True, description="Whether to return text output")

    @model_validator(mode="before")
    @classmethod
    def normalize_scalars(cls, data: Any):
        if isinstance(data, dict):
            data = dict(data)
            if "text" in data:
                data["text"] = coerce_bool(data["text"])
            if "timeout" in data:
                data["timeout"] = coerce_float(data["timeout"])
        return data

    @model_validator(mode="after")
    def normalize_input_transport(self):
        self.input_data = resolve_text_source(text=self.input_data, lines=self.input_lines, base64_text=self.input_base64, field_label="input_data", required=False)
        self.input_lines = None
        self.input_base64 = None
        return self


class UniverseTBExecuteAction(AgentAction):
    """Execute a tool in a toolbox."""
    action: Literal["universe_tb_execute"] = "universe_tb_execute"
    description: Literal["Execute a tool in a toolbox with specified parameters"] = "Execute a tool in a toolbox with specified parameters"
    payload: TBExecuteArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>, 
                              "tool_name": <string>, 
                              "args": <list[string]> (optional), 
                              "fn_args": <list> (optional), 
                              "kwargs": <dict> (optional), 
                              "env": <dict[string,string]> (optional), 
                              "cwd": <string> (optional), 
                              "timeout": <float> (optional), 
                              "input_data" OR "input_lines" OR "input_base64": <string/list> (optional), 
                              "text": <bool> (optional, default=True)}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        univ_base_url = f"<unresolved:{univ_name}>"
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url, resolve_error, _resolution = get_universe_base_url_or_error(infra, univ_name)
            if resolve_error:
                raise RuntimeError(resolve_error)
            # Use model_dump to exclude None values and non-API fields
            payload_dict = self.payload.model_dump(
                exclude_none=True,
                exclude={'universe_url', 'tb_name'}
            )

            if hasattr(infra, "request_permission"):
                approval_request = {
                    "action": self.action,
                    "payload": self.payload.model_dump(mode="json", exclude_none=True),
                    "payload_summary": f"Execute tool '{self.payload.tool_name}' in toolbox '{self.payload.tb_name}' on universe '{univ_name}'",
                    "operation": "universe_tool_execution",
                    "command_display": self.payload.tool_name,
                    "timeout": int(self.payload.timeout or DEFAULT_TIMEOUT),
                    "cwd": self.payload.cwd or os.getcwd(),
                    "purpose": self.purpose,
                    "expectations": self.expectations,
                    "risk_hints": [
                        "executes a toolbox tool inside a universe/actionbox",
                        "may run command/script/binary depending on tool implementation",
                        "remote or sandbox side effects possible",
                    ],
                    "metadata": {
                        "universe": univ_name,
                        "universe_url": univ_base_url,
                        "tb_name": self.payload.tb_name,
                        "tool_name": self.payload.tool_name,
                    },
                }
                approval = infra.request_permission("universe_tb_execute", approval_request)
                if not approval.get("approved", False):
                    result = {
                        "ok": False,
                        "approved": False,
                        "action": self.action,
                        "universe": univ_name,
                        "tb_name": self.payload.tb_name,
                        "tool_name": self.payload.tool_name,
                        "error": approval.get("reason") or "universe_tb_execute was denied by user approval policy",
                        "approval": approval,
                    }
                    infra.append_chat_history(
                        actor="system",
                        content=f"[UniverseTBExecuteAction][denied]: {result}",
                        action={"action": "system_info"},
                        log_console=True,
                    )
                    return result
            
            response = requests.post(
                f"{univ_base_url}/tbs/{self.payload.tb_name}/execute",
                json=payload_dict,
                timeout=self.payload.timeout or DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ctx_msg = (f"[Universe: {univ_base_url}] Tool execution results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class TBToolInfoArgs(BaseModel):
    #universe_url: str = Field(description="Base URL of the universe")
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    tb_name: str = Field(description="Name of the toolbox")
    tool_name: str = Field(description="Name of the tool")


class UniverseTBToolInfoAction(AgentAction):
    """Get information about a specific tool."""
    action: Literal["universe_tb_tool_info"] = "universe_tb_tool_info"
    description: Literal["Get detailed information about a specific tool in a toolbox"] = "Get detailed information about a specific tool in a toolbox"
    payload: TBToolInfoArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>, 
                              "tool_name": <string>}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        univ_base_url = f"<unresolved:{univ_name}>"
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url, resolve_error, _resolution = get_universe_base_url_or_error(infra, univ_name)
            if resolve_error:
                raise RuntimeError(resolve_error)
            response = requests.get(
                f"{univ_base_url}/tbs/{self.payload.tb_name}/tools/{self.payload.tool_name}/info",
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ctx_msg = (f"[Universe: {univ_base_url}] Tool <{self.payload.tool_name}> info query results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return



class TBListToolsArgs(BaseModel):
    #universe_url: str = Field(description="Base URL of the universe")
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    tb_name: str = Field(description="Name of the toolbox")


class UniverseTBListToolsAction(AgentAction):
    """List all tools in a toolbox."""
    action: Literal["universe_tb_list_tools"] = "universe_tb_list_tools"
    description: Literal["List all tools available in a toolbox"] = "List all tools available in a toolbox"
    payload: TBListToolsArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        univ_base_url = f"<unresolved:{univ_name}>"
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url, resolve_error, _resolution = get_universe_base_url_or_error(infra, univ_name)
            if resolve_error:
                raise RuntimeError(resolve_error)
            response = requests.get(
                f"{univ_base_url}/tbs/{self.payload.tb_name}/tools",
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                result = {"error": "Invalid response format, expected list", "action": self.action}
            result = {"tools": result, "count": len(result)}
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ctx_msg = (f"[Universe: {univ_base_url}] Tool list results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class TBSearchDocsArgs(BaseModel):
    #universe_url: str = Field(description="Base URL of the universe")
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    tb_name: str = Field(description="Name of the toolbox")
    tool_name: str = Field(description="Name of the tool")
    query: str = Field(description="Search query")
    k: int = Field(default=5, description="Number of results to return")
    context_window: int = Field(default=1, description="Context window size for results")


class UniverseTBSearchDocsAction(AgentAction):
    """Search tool documentation."""
    action: Literal["universe_tb_search_docs"] = "universe_tb_search_docs"
    description: Literal["Search documentation for a specific tool"] = "Search documentation for a specific tool"
    payload: TBSearchDocsArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with", 
                              "tb_name": <string>, 
                              "tool_name": <string>, 
                              "query": <string>, 
                              "k": <int> (optional, default=5), 
                              "context_window": <int> (optional, default=1)}'"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        univ_base_url = f"<unresolved:{univ_name}>"
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url, resolve_error, _resolution = get_universe_base_url_or_error(infra, univ_name)
            if resolve_error:
                raise RuntimeError(resolve_error)
            response = requests.post(
                f"{univ_base_url}/tbs/{self.payload.tb_name}/tools/{self.payload.tool_name}/search_docs",
                json={"query": self.payload.query, "k": self.payload.k, "context_window": self.payload.context_window},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                result = {"error": "Invalid response format, expected list", "action": self.action}
            result = {"results": result, "count": len(result)}
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ctx_msg = (f"[Universe: {univ_base_url}] Tool <{self.payload.tool_name}> doc search results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class UniverseTBStatsAction(AgentAction):
    """Get toolbox statistics."""
    action: Literal["universe_tb_stats"] = "universe_tb_stats"
    description: Literal["Get statistics for a toolbox"] = "Get statistics for a toolbox"
    payload: TBListToolsArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        univ_base_url = f"<unresolved:{univ_name}>"
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url, resolve_error, _resolution = get_universe_base_url_or_error(infra, univ_name)
            if resolve_error:
                raise RuntimeError(resolve_error)
            response = requests.get(
                f"{univ_base_url}/tbs/{self.payload.tb_name}/stats",
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ctx_msg = (f"[Universe: {univ_base_url}] Toolbox <{self.payload.tb_name}> stats query results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


# ===========================
# Tool Documentation Management
# ===========================
class TBAppendDocsArgs(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"system": "local", "universe": "main", "tb_name": "tools", "tool_name": "tool", "text_lines": [["Doc line 1", "Doc line 2"]], "doc_source": "agent"}]})

    #universe_url: str = Field(description="Base URL of the universe")
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    tb_name: str = Field(description="Name of the toolbox")
    tool_name: str = Field(description="Name of the tool")
    texts: Optional[List[str]] = Field(default=None, description="List of text documents to add to tool documentation")
    text_lines: Optional[List[List[str]]] = Field(default=None, description="Safer document transport; each document is a list of lines")
    texts_base64: Optional[List[str]] = Field(default=None, description="Base64 encoded UTF-8 documentation texts")
    doc_source: str = Field(default="agent", description="Source identifier for the documents")

    @model_validator(mode="after")
    def normalize_text_transports(self):
        self.texts = resolve_text_list_source(texts=self.texts, text_lines=self.text_lines, texts_base64=self.texts_base64, field_label="texts", required=True)
        self.text_lines = None
        self.texts_base64 = None
        return self


class UniverseTBAppendDocsAction(AgentAction):
    """Append documentation texts to a specific tool."""
    action: Literal["universe_tb_append_docs"] = "universe_tb_append_docs"
    description: Literal["Append documentation texts to a specific tool"] = "Append documentation texts to a specific tool"
    payload: TBAppendDocsArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>, 
                              "tool_name": <string>, 
                              "texts" OR "text_lines" OR "texts_base64": <list[string]>, 
                              "doc_source": <string> (optional, default="agent")}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        univ_base_url = f"<unresolved:{univ_name}>"
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url, resolve_error, _resolution = get_universe_base_url_or_error(infra, univ_name)
            if resolve_error:
                raise RuntimeError(resolve_error)
            response = requests.post(
                f"{univ_base_url}/tbs/{self.payload.tb_name}/tools/{self.payload.tool_name}/append_texts",
                json={"texts": self.payload.texts, "doc_source": self.payload.doc_source},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ctx_msg = (f"[Universe: {univ_base_url}] Tool <{self.payload.tool_name}> doc append results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return
