from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
import requests
import chromadb
from framework.data_store.data_models import EmbeddingParams
from framework.knowledgebase.data_models import KnowledgeBaseParams, MultimodalKnowledgeBaseParams
from framework.knowledgebase.knowledge_base import KnowledgeBase
from framework.knowledgebase.base_multimodal_knowledgebase import MultimodalKnowledgeBase

from framework.universes.base_universe import CreateKBRequest

from framework.workflows.base_agent_action import AgentAction
from framework.workflows.relevance_filtering import (
    search_direct,
    search_direct_with_auto_fallback,
    search_with_rolling_window,
    search_with_agentic_internal_questions,
)


# Default timeout for all HTTP requests
DEFAULT_TIMEOUT = 30

# ---------------------------
# Create KnowledgeBase Action
# ---------------------------

class CreateKBArgs(BaseModel):
    system: str = Field(description="System where the KB will be created, e.g., 'local'")
    univ_name: str = Field(description="Name for the Universe the KB belongs to")
    kb_params: KnowledgeBaseParams | MultimodalKnowledgeBaseParams | Dict[str, Any] = Field(description="Parameters of the KB")
    type: str = Field(default="text", description="Type of KB: 'text' for text-only or 'multimodal' for multimodal KB")


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
        kb_type = self.payload.type.lower()

        # Check if universe exists in managed deployments
        deployments: Dict[str, Dict[str, Any]] = getattr(infra, "managed_deployments", {})
        if univ_name not in deployments:
            ERROR_MSG = f"""Unable to find UNIV[{univ_name}] in the managed deployments. \n 
                                   Try to create UNIV[{univ_name}] first."""
            infra.append_chat_history(actor="system", content=ERROR_MSG, action={"action": "system_info"}, log_console=True)
            return

        kb_params = self.payload.kb_params
        kb_name = getattr(kb_params, "name", None)

        try:
            univ = infra.UNIVs[univ_name]
            univ_base_url = univ.get_base_url()

            # Normalize kb_params to the correct type based on kb_type without dropping fields
            if kb_type == "multimodal":
                if isinstance(kb_params, MultimodalKnowledgeBaseParams):
                    normalized_kb_params = kb_params
                elif isinstance(kb_params, KnowledgeBaseParams):
                    normalized_kb_params = MultimodalKnowledgeBaseParams(**kb_params.model_dump())
                elif isinstance(kb_params, dict):
                    normalized_kb_params = MultimodalKnowledgeBaseParams(**kb_params)
                elif hasattr(kb_params, "model_dump"):
                    normalized_kb_params = MultimodalKnowledgeBaseParams(**kb_params.model_dump())
                else:
                    normalized_kb_params = MultimodalKnowledgeBaseParams(**dict(kb_params))
            elif kb_type == "text":
                if isinstance(kb_params, KnowledgeBaseParams):
                    normalized_kb_params = kb_params
                elif isinstance(kb_params, dict):
                    normalized_kb_params = KnowledgeBaseParams(**kb_params)
                elif hasattr(kb_params, "model_dump"):
                    normalized_kb_params = KnowledgeBaseParams(**kb_params.model_dump())
                else:
                    error_msg = f"For 'text' type, kb_params must be KnowledgeBaseParams-compatible, got {type(kb_params).__name__}"
                    infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_info"}, log_console=True)
                    return
            else:
                error_msg = f"Invalid KB type: {kb_type}. Must be 'text' or 'multimodal'"
                infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_info"}, log_console=True)
                return

            kb_params = normalized_kb_params
            kb_name = kb_params.name

            request_data = {
                "kb_params": kb_params.model_dump(),
                "type": kb_type,
            }

            response = requests.post(
                f"{univ_base_url}/kbs",
                json=request_data,
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()

            if getattr(infra.UNIVs[univ_name], "kbs", None) is None:
                infra.UNIVs[univ_name].kbs = {}

            infra.append_chat_history(
                actor="system",
                content=f" KB[{kb_params.name}] ({kb_type}) successfully added to UNIV[{univ_name}] via API",
                action={"action": "create_kb"},
                log_console=True,
            )

        except requests.exceptions.Timeout:
            error_msg = f"Request timed out while creating KB[{kb_name or 'unknown'}] in UNIV[{univ_name}]"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_info"}, log_console=True)
        except requests.exceptions.HTTPError as e:
            response = getattr(e, "response", None)
            error_detail = f"Failed to create KB[{kb_name or 'unknown'}] in UNIV[{univ_name}]: {str(e)}"
            if response is not None:
                error_detail += f"\nstatus_code={response.status_code}"
                error_detail += f"\nresponse_text={response.text}"
                try:
                    error_detail += f"\nresponse_json={response.json()}"
                except Exception:
                    pass
            infra.append_chat_history(actor="system", content=error_detail, action={"action": "system_info"}, log_console=True)
        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to create KB[{kb_name or 'unknown'}] in UNIV[{univ_name}]: {str(e)}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"Error creating KB[{kb_name or 'unknown'}] in UNIV[{univ_name}]: {str(e)}"
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
    search_mode: Literal["direct_search", "rolling_window", "agentic_internal_questions"] = Field(
        default="direct_search",
        description="KB search strategy to use"
    )
    auto_fallback_to_rolling_window: bool = Field(
        default=True,
        description="When search_mode is direct_search, automatically retry with rolling_window if direct-search results are not relevant enough to answer"
    )
    batch_size: int = Field(default=10, description="Batch size for rolling-window search")
    max_batches: int = Field(default=5, description="Maximum number of batches to process for rolling-window search")
    max_relevant_results: Optional[int] = Field(default=None, description="Optional cap on relevant results returned by advanced search modes")
    dedupe_by: str = Field(default="id", description="Candidate deduplication key: id, source, or document")
    require_strict_yes_no: bool = Field(default=True, description="Require strict yes/no answers for relevance checks")
    include_nonrelevant: bool = Field(default=False, description="Include nonrelevant results metadata for advanced modes")
    show_steps: bool = Field(default=False, description="Print intermediate advanced-search steps for debugging")
    max_internal_questions: int = Field(default=3, description="Maximum number of generated internal questions")
    k_per_internal_question: Optional[int] = Field(default=None, description="Results to retrieve per internal question; defaults to k")
    require_direct_answer_sufficiency: bool = Field(
        default=True,
        description="When auto fallback is enabled for direct_search, require a second-stage sufficiency check before accepting direct-search results"
    )
    max_direct_results_for_sufficiency_check: int = Field(
        default=3,
        description="Maximum number of top relevant direct-search results to inspect during the direct-answer sufficiency check"
    )
    min_relevant_results_for_direct_accept: Optional[int] = Field(
        default=None,
        description="Optional minimum number of relevant direct-search results required to accept direct_search without fallback; if unmet, rolling_window is triggered"
    )


class UniverseKBSearchAction(AgentAction):
    """Search a knowledge base in the universe."""
    action: Literal["universe_kb_search"] = "universe_kb_search"
    description: Literal["Search a knowledge base for relevant information"] = "Search a knowledge base for relevant information"
    payload: KBSearchArgs
    payload_schema: str = """{
                              "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with",
                              "kb_name": <string>,
                              "query": <string>,
                              "k": <int> (optional, default=5),
                              "context_window": <int> (optional, default=1),
                              "search_mode": <string> (optional, default="direct_search"),
                              "auto_fallback_to_rolling_window": <bool> (optional, default=true),
                              "batch_size": <int> (optional, default=10),
                              "max_batches": <int> (optional, default=5),
                              "max_relevant_results": <int|null> (optional),
                              "dedupe_by": <string> (optional, default="id"),
                              "require_strict_yes_no": <bool> (optional, default=true),
                              "include_nonrelevant": <bool> (optional, default=false),
                              "show_steps": <bool> (optional, default=false),
                              "max_internal_questions": <int> (optional, default=3),
                              "k_per_internal_question": <int|null> (optional),
                              "require_direct_answer_sufficiency": <bool> (optional, default=true),
                              "max_direct_results_for_sufficiency_check": <int> (optional, default=3),
                              "min_relevant_results_for_direct_accept": <int|null> (optional)
                              }
                              """
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        result: Dict[str, Any]
        univ_base_url = f"universe={univ_name}"

        try:
            univ = infra.UNIVs[univ_name]
            if hasattr(univ, "get_base_url"):
                univ_base_url = univ.get_base_url()
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
            return

        try:
            search_mode = self.payload.search_mode
            if search_mode == "direct_search":
                if self.payload.auto_fallback_to_rolling_window:
                    result = search_direct_with_auto_fallback(
                        user_input=self.payload.query,
                        agent=infra.agent,
                        infra=infra,
                        universe_name=univ_name,
                        kb_name=self.payload.kb_name,
                        k=self.payload.k,
                        context_window=self.payload.context_window,
                        batch_size=self.payload.batch_size,
                        max_batches=self.payload.max_batches,
                        dedupe_by=self.payload.dedupe_by,
                        require_strict_yes_no=self.payload.require_strict_yes_no,
                        include_nonrelevant=self.payload.include_nonrelevant,
                        max_relevant_results=self.payload.max_relevant_results,
                        show_steps=self.payload.show_steps,
                        require_direct_answer_sufficiency=self.payload.require_direct_answer_sufficiency,
                        max_direct_results_for_sufficiency_check=self.payload.max_direct_results_for_sufficiency_check,
                        min_relevant_results_for_direct_accept=self.payload.min_relevant_results_for_direct_accept,
                    )
                else:
                    result = search_direct(
                        infra=infra,
                        universe_name=univ_name,
                        kb_name=self.payload.kb_name,
                        query=self.payload.query,
                        k=self.payload.k,
                        context_window=self.payload.context_window,
                    )
            elif search_mode == "rolling_window":
                result = search_with_rolling_window(
                    user_input=self.payload.query,
                    agent=infra.agent,
                    infra=infra,
                    universe_name=univ_name,
                    kb_name=self.payload.kb_name,
                    batch_size=self.payload.batch_size,
                    max_batches=self.payload.max_batches,
                    context_window=self.payload.context_window,
                    dedupe_by=self.payload.dedupe_by,
                    require_strict_yes_no=self.payload.require_strict_yes_no,
                    include_nonrelevant=self.payload.include_nonrelevant,
                    max_relevant_results=self.payload.max_relevant_results,
                    show_steps=self.payload.show_steps,
                )
            elif search_mode == "agentic_internal_questions":
                result = search_with_agentic_internal_questions(
                    user_input=self.payload.query,
                    agent=infra.agent,
                    infra=infra,
                    universe_name=univ_name,
                    kb_name=self.payload.kb_name,
                    k_per_internal_question=self.payload.k_per_internal_question or self.payload.k,
                    context_window=self.payload.context_window,
                    max_internal_questions=self.payload.max_internal_questions,
                    dedupe_by=self.payload.dedupe_by,
                    require_strict_yes_no=self.payload.require_strict_yes_no,
                    include_nonrelevant=self.payload.include_nonrelevant,
                    max_relevant_results=self.payload.max_relevant_results,
                    show_steps=self.payload.show_steps,
                )
            else:
                result = {"error": f"Unsupported search_mode: {search_mode}", "action": self.action}
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}

        ctx_msg = (f"[Universe: {univ_base_url}] Knowledgebase query results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        return result


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
    payload_schema: str = """{
                              "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
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
    payload_schema: str = """{
                            "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
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
    payload_schema: str = """{
                            "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
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


class KBAddDocumentArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    kb_name: str = Field(description="Name of the knowledge base")
    content: str = Field(description="Content of the document (text, base64-encoded data, or file path)")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata for the document")
    modality: str = Field(default="text", description="Modality type: 'text', 'image', 'audio', 'video', 'table', 'binary'")


class UniverseKBAddDocumentAction(AgentAction):
    """Add a single document to a multimodal knowledge base."""
    action: Literal["universe_kb_add_document"] = "universe_kb_add_document"
    description: Literal["Add a single document to a multimodal knowledge base"] = "Add a single document to a multimodal knowledge base"
    payload: KBAddDocumentArgs
    payload_schema: str = """{
                            "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                            "universe": <string>: "Name of the universe you are interacting with",
                            "kb_name": <string>,
                            "content": <string>,
                            "metadata": <dict> (optional),
                            "modality": <string> (optional, default="text")}"""
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
                f"{univ_base_url}/kbs/{self.payload.kb_name}/add_document",
                json={
                    "content": self.payload.content,
                    "metadata": self.payload.metadata,
                    "modality": self.payload.modality
                },
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
        ctx_msg = (f"[Universe: {univ_base_url}] Knowledgebase add document results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class KBAddPDFArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")
    kb_name: str = Field(description="Name of the knowledge base")
    pdf_path: Optional[str] = Field(default=None, description="Path to PDF file on server")
    pdf_content: Optional[str] = Field(default=None, description="Base64-encoded PDF content")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata for the PDF")
    extract_images: bool = Field(default=True, description="Whether to extract images from PDF")
    extract_tables: bool = Field(default=True, description="Whether to extract tables from PDF")
    persist_extracted_images: bool = Field(default=True, description="Whether extracted PDF images are physically saved to disk")
    extracted_image_dir: Optional[str] = Field(default=None, description="Optional directory where extracted PDF images should be persisted")


class UniverseKBAddPDFAction(AgentAction):
    """Add a PDF document to a multimodal knowledge base, extracting text, images, and tables."""
    action: Literal["universe_kb_add_pdf"] = "universe_kb_add_pdf"
    description: Literal["Add a PDF document to a multimodal knowledge base, automatically extracting all elements"] = "Add a PDF document to a multimodal knowledge base, automatically extracting all elements"
    payload: KBAddPDFArgs
    payload_schema: str = """{
                            "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                            "universe": <string>: "Name of the universe you are interacting with",
                            "kb_name": <string>,
                            "pdf_path": <string> (optional): "Path to PDF file on server",
                            "pdf_content": <string> (optional): "Base64-encoded PDF content",
                            "metadata": <dict> (optional),
                            "extract_images": <bool> (optional, default=True),
                            "extract_tables": <bool> (optional, default=True),
                            "persist_extracted_images": <bool> (optional, default=True),
                            "extracted_image_dir": <string> (optional)}"""
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

            # Build request payload
            request_payload = {
                "metadata": self.payload.metadata,
                "extract_images": self.payload.extract_images,
                "extract_tables": self.payload.extract_tables,
                "persist_extracted_images": self.payload.persist_extracted_images,
                "extracted_image_dir": self.payload.extracted_image_dir,
            }

            # Add either pdf_path or pdf_content
            if self.payload.pdf_path:
                request_payload["pdf_path"] = self.payload.pdf_path
            elif self.payload.pdf_content:
                request_payload["pdf_content"] = self.payload.pdf_content
            else:
                error_msg = "Must provide either 'pdf_path' or 'pdf_content'"
                infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_info"}, log_console=True,)
                return

            response = requests.post(
                f"{univ_base_url}/kbs/{self.payload.kb_name}/add_pdf",
                json=request_payload,
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.HTTPError as e:
            response = getattr(e, "response", None)
            error_detail: Dict[str, Any] = {
                "error": f"Request failed: {str(e)}",
                "action": self.action,
            }
            if response is not None:
                error_detail["status_code"] = response.status_code
                error_detail["response_text"] = response.text
                try:
                    error_detail["response_json"] = response.json()
                except Exception:
                    pass
            result = error_detail
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] Knowledgebase add PDF results:\n"
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
    payload_schema: str = """{
                              "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
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
    payload_schema: str = """{
                            "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
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
    payload_schema: str = """{
                              "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
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
        ctx_msg = (f"[Universe: {univ_base_url}] Purge Knowledgebase<{self.payload.kb_name}> query results:\n"
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
    payload_schema: str = """{
                              "system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
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
