from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
import requests
import chromadb
from framework.data_store.data_models import EmbeddingParams
from framework.knowledgebase.data_models import KnowledgeBaseParams
from framework.knowledgebase.knowledge_base import KnowledgeBase

from framework.universes.base_universe import CreateKBRequest

from framework.workflows.base_agent_action import AgentAction


# Default timeout for all HTTP requests
DEFAULT_TIMEOUT = 30

# ---------------------------
# Create KnowledgeBase Action
# ---------------------------

class CreateKBArgs(BaseModel):
    system: str = Field(description="System where the KB will be created, e.g., 'local'")
    univ_name: str = Field(description="Name for the Universe the KB belongs to")
    kb_params: KnowledgeBaseParams = Field(description=f"Parameter of the KB: {KnowledgeBaseParams.model_fields}")


class CreateKBAction(AgentAction):
    """Create a KnowledgeBase by calling the universe's API endpoint.

    This registers the KB with the universe server, ensuring it appears in
    health checks and info queries.
    """
    action: Literal["create_kb"] = "create_kb"
    description: Literal["Create and register a KnowledgeBase"] = "Create and register a KnowledgeBase"
    payload: CreateKBArgs
    payload_schema: str = f"{CreateKBArgs.model_fields}"


    def execute(self, infra) -> None:
        univ_name = self.payload.univ_name.strip()
        
        # Check if universe exists in managed deployments
        deployments: Dict[str, Dict[str, Any]] = getattr(infra, "managed_deployments", {})
        if univ_name not in deployments:
            ERROR_MSG = f"""Unable to find UNIV[{univ_name}] in the managed deployments. \n 
                                   Try to create UNIV[{univ_name}] first."""
            infra.append_chat_history(actor="system", content=ERROR_MSG, action={"action": "system_info"}, log_console=True)
            return
        
        try:
            univ = infra.UNIVs[univ_name]
            univ_base_url = univ.get_base_url()
            
            # Prepare KB creation request
            kb_params = self.payload.kb_params
            
            # Serialize the request properly as a dictionary
            request_data = {
                "kb_params": kb_params.model_dump(),
                "db_client": None  # This field cannot be serialized; will be constructed server-side
            }
            
            # Call universe's POST /kbs endpoint
            response = requests.post(
                f"{univ_base_url}/kbs",
                json=request_data,
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            
            # Update local reference if needed
            if infra.UNIVs[univ_name].kbs is None:
                infra.UNIVs[univ_name].kbs = {}
            
            infra.append_chat_history(
                actor="system",
                content=f" KB[{kb_params.name}] successfully added to UNIV[{univ_name}] via API",
                action={"action": "create_kb"},
                log_console=True,
            )
            
        except requests.exceptions.Timeout:
            error_msg = f"Request timed out while creating KB[{kb_params.name}] in UNIV[{univ_name}]"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_info"}, log_console=True)
        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to create KB[{kb_params.name}] in UNIV[{univ_name}]: {str(e)}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"Error creating KB[{kb_params.name}] in UNIV[{univ_name}]: {str(e)}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_info"}, log_console=True)
        
        return


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
    payload_schema: str = """{\n                              "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",\n                              "universe": <string>: "Name of the universe you are interacting with",\n                              "kb_name": <string>, \n                              "query": <string>, \n                              "k": <int> (optional, default=5), \n                              "context_window": <int> (optional, default=1)\n                              }\n                              """
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
    payload_schema: str = """{\n                              "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",\n                              "universe": <string>: "Name of the universe you are interacting with",\n                              "kb_name": <string>,\n                              "texts": <list[string]>, \n                              "doc_source": <string> (optional, default="agent")\n                              }\n                              """
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
    payload_schema: str = """{\n                            "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",\n                            "universe": <string>: "Name of the universe you are interacting with",\n                            "kb_name": <string>, \n                            "url": <string>}"""
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
    payload_schema: str = """{\n                            "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",\n                            "universe": <string>: "Name of the universe you are interacting with",\n                            "kb_name": <string>, \n                            "urls": <list[string]>}"""
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
    payload_schema: str = """{\n                              "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",\n                              "universe": <string>: "Name of the universe you are interacting with",\n                              "kb_name": <string>}"""
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
    payload_schema: str = """{\n                            "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",\n                            "universe": <string>: "Name of the universe you are interacting with",\n                            "kb_name": <string>}"""
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
    payload_schema: str = """{\n                              "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",\n                              "universe": <string>: "Name of the universe you are interacting with",\n                              "kb_name": <string>}"""
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
    payload_schema: str = """{\n                              "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",\n                              "universe": <string>: "Name of the universe you are interacting with",\n                              "kb_name": <string>, "document_id": <string>}"""
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
