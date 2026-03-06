from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
import requests
from framework.workflows.base_agent_action import AgentAction

# Default timeout for all HTTP requests
DEFAULT_TIMEOUT = 30


# ===========================
# Base Universe Interaction
# ===========================
class KnownUniversesQueryArgs(BaseModel):
    system: str = Field(description="Name of the system to which the universes are connected to (typically the local system)")

class UniverseDiscoveryArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe (e.g., http://localhost:8000)")


class FindKnownUniversesAction(AgentAction):
    """Get the list of universes known to a system"""
    action: Literal["get_list_known_universes"] = "get_list_known_universes"
    description: Literal["Action to query the list of universes known to a system"] = "Action to query the list of universes known to a system"
    payload: KnownUniversesQueryArgs
    payload_schema: str = '{"system": <string>: "name of system i.e "local" for the local system"}'
    #yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        result = []
        print(f"[+][UNIVERSES][QURY]: infra = {infra}")
        try:
            for univ in infra.UNIVs:
                result.append(univ.info)
            print(f"[+][UNIVERSES][QURY]: {result}")
            return {"system":"local", "universes": result}
        except Exception as e:
            return {"error": str(e), "action": self.action}

class UniverseInfoAction(AgentAction):
    """Get universe discovery information (KBs, TBs, allowed actions)."""
    action: Literal["universe_info"] = "universe_info"
    description: Literal["Get universe discovery information including available KBs, TBs, and actions"] = "Get universe discovery information including available KBs, TBs, and actions"
    payload: UniverseDiscoveryArgs
    payload_schema: str = '{"universe_url": <string>: "Base URL of the universe"}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.payload.universe_url}/info", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                return {"error": "Invalid response format", "action": self.action}
            return result
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class UniverseHealthAction(AgentAction):
    """Check universe health status."""
    action: Literal["universe_health"] = "universe_health"
    description: Literal["Check universe health status"] = "Check universe health status"
    payload: UniverseDiscoveryArgs
    payload_schema: str = '{"universe_url": <string>: "Base URL of the universe"}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.payload.universe_url}/health", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                return {"error": "Invalid response format", "action": self.action}
            return result
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class UniverseStatsAction(AgentAction):
    """Get comprehensive universe statistics."""
    action: Literal["universe_stats"] = "universe_stats"
    description: Literal["Get comprehensive statistics for all KBs and TBs in the universe"] = "Get comprehensive statistics for all KBs and TBs in the universe"
    payload: UniverseDiscoveryArgs
    payload_schema: str = '{"universe_url": <string>: "Base URL of the universe"}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.payload.universe_url}/stats", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                return {"error": "Invalid response format", "action": self.action}
            return result
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class UniverseListToolsAction(AgentAction):
    """List all available tools across all toolboxes."""
    action: Literal["universe_list_tools"] = "universe_list_tools"
    description: Literal["List all available tools across all toolboxes in the universe"] = "List all available tools across all toolboxes in the universe"
    payload: UniverseDiscoveryArgs
    payload_schema: str = '{"universe_url": <string>: "Base URL of the universe"}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.payload.universe_url}/tools", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                return {"error": "Invalid response format, expected list", "action": self.action}
            return {"tools": result, "count": len(result)}
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


# ===========================
# Knowledge Base Interactions
# ===========================
class KBSearchArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
    kb_name: str = Field(description="Name of the knowledge base")
    query: str = Field(description="Search query")
    k: int = Field(default=5, description="Number of results to return")
    context_window: int = Field(default=1, description="Context window size for results")


class UniverseKBSearchAction(AgentAction):
    """Search a knowledge base in the universe."""
    action: Literal["universe_kb_search"] = "universe_kb_search"
    description: Literal["Search a knowledge base for relevant information"] = "Search a knowledge base for relevant information"
    payload: KBSearchArgs
    payload_schema: str = '{"universe_url": <string>, "kb_name": <string>, "query": <string>, "k": <int> (optional, default=5), "context_window": <int> (optional, default=1)}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.payload.universe_url}/kbs/{self.payload.kb_name}/search",
                json={"query": self.payload.query, "k": self.payload.k, "context_window": self.payload.context_window},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                return {"error": "Invalid response format, expected list", "action": self.action}
            return {"results": result, "count": len(result)}
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class KBAppendTextsArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
    kb_name: str = Field(description="Name of the knowledge base")
    texts: List[str] = Field(description="List of text documents to add")
    doc_source: str = Field(default="agent", description="Source identifier for the documents")


class UniverseKBAppendTextsAction(AgentAction):
    """Add text documents to a knowledge base."""
    action: Literal["universe_kb_append_texts"] = "universe_kb_append_texts"
    description: Literal["Add text documents to a knowledge base"] = "Add text documents to a knowledge base"
    payload: KBAppendTextsArgs
    payload_schema: str = '{"universe_url": <string>, "kb_name": <string>, "texts": <list[string]>, "doc_source": <string> (optional, default="agent")}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.payload.universe_url}/kbs/{self.payload.kb_name}/append_texts",
                json={"texts": self.payload.texts, "doc_source": self.payload.doc_source},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class KBAddURLArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
    kb_name: str = Field(description="Name of the knowledge base")
    url: str = Field(description="URL to add as a document")


class UniverseKBAddURLAction(AgentAction):
    """Add a single URL document to a knowledge base."""
    action: Literal["universe_kb_add_url"] = "universe_kb_add_url"
    description: Literal["Add a single URL document to a knowledge base"] = "Add a single URL document to a knowledge base"
    payload: KBAddURLArgs
    payload_schema: str = '{"universe_url": <string>, "kb_name": <string>, "url": <string>}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.payload.universe_url}/kbs/{self.payload.kb_name}/add_url",
                json={"url": self.payload.url},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class KBAddURLsArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
    kb_name: str = Field(description="Name of the knowledge base")
    urls: List[str] = Field(description="List of URLs to add as documents")


class UniverseKBAddURLsAction(AgentAction):
    """Add multiple URL documents to a knowledge base."""
    action: Literal["universe_kb_add_urls"] = "universe_kb_add_urls"
    description: Literal["Add multiple URL documents to a knowledge base"] = "Add multiple URL documents to a knowledge base"
    payload: KBAddURLsArgs
    payload_schema: str = '{"universe_url": <string>, "kb_name": <string>, "urls": <list[string]>}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.payload.universe_url}/kbs/{self.payload.kb_name}/add_urls",
                json={"urls": self.payload.urls},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class KBStatsArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
    kb_name: str = Field(description="Name of the knowledge base")


class UniverseKBStatsAction(AgentAction):
    """Get knowledge base statistics."""
    action: Literal["universe_kb_stats"] = "universe_kb_stats"
    description: Literal["Get statistics for a knowledge base"] = "Get statistics for a knowledge base"
    payload: KBStatsArgs
    payload_schema: str = '{"universe_url": <string>, "kb_name": <string>}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.payload.universe_url}/kbs/{self.payload.kb_name}/stats", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class UniverseKBSourcesAction(AgentAction):
    """List knowledge base sources."""
    action: Literal["universe_kb_sources"] = "universe_kb_sources"
    description: Literal["List all sources in a knowledge base"] = "List all sources in a knowledge base"
    payload: KBStatsArgs
    payload_schema: str = '{"universe_url": <string>, "kb_name": <string>}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.payload.universe_url}/kbs/{self.payload.kb_name}/sources", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class UniverseKBPurgeAction(AgentAction):
    """Purge all content from a knowledge base."""
    action: Literal["universe_kb_purge"] = "universe_kb_purge"
    description: Literal["Purge all content from a knowledge base (use with caution!)"] = "Purge all content from a knowledge base (use with caution!)"
    payload: KBStatsArgs
    payload_schema: str = '{"universe_url": <string>, "kb_name": <string>}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.post(f"{self.payload.universe_url}/kbs/{self.payload.kb_name}/purge", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class KBGetDocumentArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
    kb_name: str = Field(description="Name of the knowledge base")
    document_id: str = Field(description="ID of the document to retrieve")


class UniverseKBGetDocumentAction(AgentAction):
    """Retrieve a specific document by ID from a knowledge base."""
    action: Literal["universe_kb_get_document"] = "universe_kb_get_document"
    description: Literal["Retrieve a specific document by ID from a knowledge base"] = "Retrieve a specific document by ID from a knowledge base"
    payload: KBGetDocumentArgs
    payload_schema: str = '{"universe_url": <string>, "kb_name": <string>, "document_id": <string>}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self.payload.universe_url}/kbs/{self.payload.kb_name}/document/{self.payload.document_id}",
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


# ===========================
# ToolBox Interactions
# ===========================
class TBSearchArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
    tb_name: str = Field(description="Name of the toolbox")
    query: str = Field(description="Search query")
    k: int = Field(default=5, description="Number of results to return")


class UniverseTBSearchToolsAction(AgentAction):
    """Search for tools in a toolbox."""
    action: Literal["universe_tb_search_tools"] = "universe_tb_search_tools"
    description: Literal["Search for tools in a toolbox based on query"] = "Search for tools in a toolbox based on query"
    payload: TBSearchArgs
    payload_schema: str = '{"universe_url": <string>, "tb_name": <string>, "query": <string>, "k": <int> (optional, default=5)}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.payload.universe_url}/tbs/{self.payload.tb_name}/search",
                json={"query": self.payload.query, "k": self.payload.k},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                return {"error": "Invalid response format, expected list", "action": self.action}
            return {"results": result, "count": len(result)}
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class TBExecuteArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
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
    payload_schema: str = '{"universe_url": <string>, "tb_name": <string>, "tool_name": <string>, "args": <list[string]> (optional), "fn_args": <list> (optional), "kwargs": <dict> (optional), "env": <dict[string,string]> (optional), "cwd": <string> (optional), "timeout": <float> (optional), "input_data": <string> (optional), "text": <bool> (optional, default=True)}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            # Use model_dump to exclude None values and non-API fields
            payload_dict = self.payload.model_dump(
                exclude_none=True,
                exclude={'universe_url', 'tb_name'}
            )
            
            response = requests.post(
                f"{self.payload.universe_url}/tbs/{self.payload.tb_name}/execute",
                json=payload_dict,
                timeout=self.payload.timeout or DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class TBToolInfoArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
    tb_name: str = Field(description="Name of the toolbox")
    tool_name: str = Field(description="Name of the tool")


class UniverseTBToolInfoAction(AgentAction):
    """Get information about a specific tool."""
    action: Literal["universe_tb_tool_info"] = "universe_tb_tool_info"
    description: Literal["Get detailed information about a specific tool in a toolbox"] = "Get detailed information about a specific tool in a toolbox"
    payload: TBToolInfoArgs
    payload_schema: str = '{"universe_url": <string>, "tb_name": <string>, "tool_name": <string>}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self.payload.universe_url}/tbs/{self.payload.tb_name}/tools/{self.payload.tool_name}/info",
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class TBListToolsArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
    tb_name: str = Field(description="Name of the toolbox")


class UniverseTBListToolsAction(AgentAction):
    """List all tools in a toolbox."""
    action: Literal["universe_tb_list_tools"] = "universe_tb_list_tools"
    description: Literal["List all tools available in a toolbox"] = "List all tools available in a toolbox"
    payload: TBListToolsArgs
    payload_schema: str = '{"universe_url": <string>, "tb_name": <string>}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self.payload.universe_url}/tbs/{self.payload.tb_name}/tools",
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                return {"error": "Invalid response format, expected list", "action": self.action}
            return {"tools": result, "count": len(result)}
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class TBSearchDocsArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
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
    payload_schema: str = '{"universe_url": <string>, "tb_name": <string>, "tool_name": <string>, "query": <string>, "k": <int> (optional, default=5), "context_window": <int> (optional, default=1)}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.payload.universe_url}/tbs/{self.payload.tb_name}/tools/{self.payload.tool_name}/search_docs",
                json={"query": self.payload.query, "k": self.payload.k, "context_window": self.payload.context_window},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                return {"error": "Invalid response format, expected list", "action": self.action}
            return {"results": result, "count": len(result)}
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


class UniverseTBStatsAction(AgentAction):
    """Get toolbox statistics."""
    action: Literal["universe_tb_stats"] = "universe_tb_stats"
    description: Literal["Get statistics for a toolbox"] = "Get statistics for a toolbox"
    payload: TBListToolsArgs
    payload_schema: str = '{"universe_url": <string>, "tb_name": <string>}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self.payload.universe_url}/tbs/{self.payload.tb_name}/stats",
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}


# ===========================
# Tool Documentation Management
# ===========================
class TBAppendDocsArgs(BaseModel):
    universe_url: str = Field(description="Base URL of the universe")
    tb_name: str = Field(description="Name of the toolbox")
    tool_name: str = Field(description="Name of the tool")
    texts: List[str] = Field(description="List of text documents to add to tool documentation")
    doc_source: str = Field(default="agent", description="Source identifier for the documents")


class UniverseTBAppendDocsAction(AgentAction):
    """Append documentation texts to a specific tool."""
    action: Literal["universe_tb_append_docs"] = "universe_tb_append_docs"
    description: Literal["Append documentation texts to a specific tool"] = "Append documentation texts to a specific tool"
    payload: TBAppendDocsArgs
    payload_schema: str = '{"universe_url": <string>, "tb_name": <string>, "tool_name": <string>, "texts": <list[string]>, "doc_source": <string> (optional, default="agent")}'
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.payload.universe_url}/tbs/{self.payload.tb_name}/tools/{self.payload.tool_name}/append_texts",
                json={"texts": self.payload.texts, "doc_source": self.payload.doc_source},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            return {"error": str(e), "action": self.action}
