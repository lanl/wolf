from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
import requests
from framework.workflows.base_agent_action import AgentAction

# Default timeout for all HTTP requests
DEFAULT_TIMEOUT = 30

# ===========================
# Knowledge Base Interactions
# ===========================
class KBSearchArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    kb_name: str = Field(description="Name of the knowledge base")
    query: str = Field(description="Search query")
    k: int = Field(default=5, description="Number of results to return")
    context_window: int = Field(default=1, description="Context window size for results")


class UniverseKBSearchAction(AgentAction):
    """Search a knowledge base in the universe."""
    action: Literal["universe_kb_search"] = "universe_kb_search"
    description: Literal["Search a knowledge base for relevant information"] = "Search a knowledge base for relevant information"
    payload: KBSearchArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "kb_name": <string>, 
                              "query": <string>, 
                              "k": <int> (optional, default=5), 
                              "context_window": <int> (optional, default=1)
                              }
                              """
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
                f"{univ_base_url}/kbs/{self.payload.kb_name}/search",
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
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] Knowledgebase query results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class KBAppendTextsArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    kb_name: str = Field(description="Name of the knowledge base")
    texts: List[str] = Field(description="List of text documents to add")
    doc_source: str = Field(default="agent", description="Source identifier for the documents")


class UniverseKBAppendTextsAction(AgentAction):
    """Add text documents to a knowledge base."""
    action: Literal["universe_kb_append_texts"] = "universe_kb_append_texts"
    description: Literal["Add text documents to a knowledge base"] = "Add text documents to a knowledge base"
    payload: KBAppendTextsArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "kb_name": <string>,
                              "texts": <list[string]>, 
                              "doc_source": <string> (optional, default="agent")
                              }
                              """
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
                f"{univ_base_url}/kbs/{self.payload.kb_name}/append_texts",
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
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] Knowledgebase Doc append results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class KBAddURLArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    kb_name: str = Field(description="Name of the knowledge base")
    url: str = Field(description="URL to add as a document")


class UniverseKBAddURLAction(AgentAction):
    """Add a single URL document to a knowledge base."""
    action: Literal["universe_kb_add_url"] = "universe_kb_add_url"
    description: Literal["Add a single URL document to a knowledge base"] = "Add a single URL document to a knowledge base"
    payload: KBAddURLArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                            "universe": <string>: "Name of the universe you are interacting with",
                            "kb_name": <string>, 
                            "url": <string>}"""
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
                f"{univ_base_url}/kbs/{self.payload.kb_name}/add_url",
                json={"url": self.payload.url},
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
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] Knowledgebase add ulr doc results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return

class KBAddURLsArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    kb_name: str = Field(description="Name of the knowledge base")
    urls: List[str] = Field(description="List of URLs to add as documents")


class UniverseKBAddURLsAction(AgentAction):
    """Add multiple URL documents to a knowledge base."""
    action: Literal["universe_kb_add_urls"] = "universe_kb_add_urls"
    description: Literal["Add multiple URL documents to a knowledge base"] = "Add multiple URL documents to a knowledge base"
    payload: KBAddURLsArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                            "universe": <string>: "Name of the universe you are interacting with",
                            "kb_name": <string>, 
                            "urls": <list[string]>}"""
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            return
        try:
            univ_base_url = univ.get_base_url()
            response = requests.post(
                f"{univ_base_url}/kbs/{self.payload.kb_name}/add_urls",
                json={"urls": self.payload.urls},
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
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] Knowledgebase add urls results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class KBStatsArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    kb_name: str = Field(description="Name of the knowledge base")


class UniverseKBStatsAction(AgentAction):
    """Get knowledge base statistics."""
    action: Literal["universe_kb_stats"] = "universe_kb_stats"
    description: Literal["Get statistics for a knowledge base"] = "Get statistics for a knowledge base"
    payload: KBStatsArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "kb_name": <string>}"""
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
            response = requests.get(f"{univ_base_url}/kbs/{self.payload.kb_name}/stats", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] Knowledgebase <{self.payload.kb_name}> stats query results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class UniverseKBSourcesAction(AgentAction):
    """List knowledge base sources."""
    action: Literal["universe_kb_sources"] = "universe_kb_sources"
    description: Literal["List all sources in a knowledge base"] = "List all sources in a knowledge base"
    payload: KBStatsArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                            "universe": <string>: "Name of the universe you are interacting with",
                            "kb_name": <string>}"""
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
            response = requests.get(f"{univ_base_url}/kbs/{self.payload.kb_name}/sources", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] List Knowledgebase{self.payload.kb_name} sources results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class UniverseKBPurgeAction(AgentAction):
    """Purge all content from a knowledge base."""
    action: Literal["universe_kb_purge"] = "universe_kb_purge"
    description: Literal["Purge all content from a knowledge base (use with caution!)"] = "Purge all content from a knowledge base (use with caution!)"
    payload: KBStatsArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "kb_name": <string>}"""
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
            response = requests.post(f"{univ_base_url}/kbs/{self.payload.kb_name}/purge", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] Purge Knowledgebase<self.payload.kb_name> query results:\n"
                   f"{result}"
                   )
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class KBGetDocumentArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    kb_name: str = Field(description="Name of the knowledge base")
    document_id: str = Field(description="ID of the document to retrieve")


class UniverseKBGetDocumentAction(AgentAction):
    """Retrieve a specific document by ID from a knowledge base."""
    action: Literal["universe_kb_get_document"] = "universe_kb_get_document"
    description: Literal["Retrieve a specific document by ID from a knowledge base"] = "Retrieve a specific document by ID from a knowledge base"
    payload: KBGetDocumentArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "kb_name": <string>, "document_id": <string>}"""
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
                f"{univ_base_url}/kbs/{self.payload.kb_name}/document/{self.payload.document_id}",
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
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] Knowledgebase<{self.payload.kb_name}> query doc by_ID <{self.payload.document_id}> results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return
