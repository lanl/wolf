from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
import requests
from framework.workflows.base_agent_action import AgentAction

# Default timeout for all HTTP requests
DEFAULT_TIMEOUT = 30

# ---------------------------
# Create ToolBox Action
# ---------------------------
from framework.tooling.toolbox import ToolBox, ToolBoxParams

class CreateToolBoxArgs(BaseModel):
    system: str = Field(description="System where the toolbox will be created, e.g., 'local'")
    name: str = Field(description="Name for the new ToolBox")
    tb_params: ToolBoxParams = Field(default_factory=ToolBoxParams, description="Structured ToolBox parameters")

class CreateToolBoxAction(AgentAction):
    """Create a ToolBox instance and register it in ``infra.managed_deployments``.

    The created ``ToolBox`` object is stored under the provided ``name``.
    """
    action: Literal["create_toolbox"] = "create_toolbox"
    description: Literal["Create and register a ToolBox"] = "Create and register a ToolBox"
    payload: CreateToolBoxArgs
    payload_schema: str = f"{CreateToolBoxArgs.model_fields}"

    def execute(self, infra) -> None:
        # Instantiate ToolBox with given params and db_client
        tb = ToolBox(self.payload.tb_params, infra.db_client)
        # Store in managed_deployments
        infra.managed_deployments[self.payload.name] = {
            "handle": tb,
            "params": self.payload.tb_params.model_dump() if hasattr(self.payload.tb_params, 'model_dump') else self.payload.tb_params.__dict__,
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
    payload_schema: str = """{{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>, 
                              "query": <string>, 
                              "k": <int> (optional, default=5)}}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
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
    text: bool = Field(default=True, description="Whether to return text output")


class UniverseTBExecuteAction(AgentAction):
    """Execute a tool in a toolbox."""
    action: Literal["universe_tb_execute"] = "universe_tb_execute"
    description: Literal["Execute a tool in a toolbox with specified parameters"] = "Execute a tool in a toolbox with specified parameters"
    payload: TBExecuteArgs
    payload_schema: str = """{{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>, 
                              "tool_name": <string>, 
                              "args": <list[string]> (optional), 
                              "fn_args": <list> (optional), 
                              "kwargs": <dict> (optional), 
                              "env": <dict[string,string]> (optional), 
                              "cwd": <string> (optional), 
                              "timeout": <float> (optional), 
                              "input_data": <string> (optional), 
                              "text": <bool> (optional, default=True)}}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
            # Use model_dump to exclude None values and non-API fields
            payload_dict = self.payload.model_dump(
                exclude_none=True,
                exclude={'universe_url', 'tb_name'}
            )
            
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
    payload_schema: str = """{{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>, 
                              "tool_name": <string>}}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
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
    payload_schema: str = """{{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>}}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
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
    payload_schema: str = """{{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with", 
                              "tb_name": <string>, 
                              "tool_name": <string>, 
                              "query": <string>, 
                              "k": <int> (optional, default=5), 
                              "context_window": <int> (optional, default=1)}'"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
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
    payload_schema: str = """{{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>}}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
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
    #universe_url: str = Field(description="Base URL of the universe")
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    tb_name: str = Field(description="Name of the toolbox")
    tool_name: str = Field(description="Name of the tool")
    texts: List[str] = Field(description="List of text documents to add to tool documentation")
    doc_source: str = Field(default="agent", description="Source identifier for the documents")


class UniverseTBAppendDocsAction(AgentAction):
    """Append documentation texts to a specific tool."""
    action: Literal["universe_tb_append_docs"] = "universe_tb_append_docs"
    description: Literal["Append documentation texts to a specific tool"] = "Append documentation texts to a specific tool"
    payload: TBAppendDocsArgs
    payload_schema: str = """{{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "tb_name": <string>, 
                              "tool_name": <string>, 
                              "texts": <list[string]>, 
                              "doc_source": <string> (optional, default="agent")}}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
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
