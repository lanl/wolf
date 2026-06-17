from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple
import os
from pathlib import Path

import chromadb

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from framework.agentic.agentic_tools import NameGenerator
from framework.knowledgebase.data_models import KnowledgeBaseParams, MultimodalKnowledgeBaseParams
from framework.knowledgebase.knowledge_base import KnowledgeBase
from framework.knowledgebase.base_multimodal_knowledgebase import MultimodalKnowledgeBase
from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams, base_universe_params_type
from framework.tooling.toolbox import ToolBox
from framework.tooling.tools import Tool, ToolCard
from framework.tooling.tool_models import ToolMeta


class BaseUniverse:
    """Self-contained environment that manages KnowledgeBases and ToolBoxes.
    
    Enhanced features from MADA's ActionBox:
    - Full async support for all operations
    - Enhanced KB and TB proxies with context window support
    - Tool discovery and execution
    - REST API factory with comprehensive endpoints
    - Support for both ToolCard and ToolMeta
    - Support for both text-only and multimodal KnowledgeBases
    """

    # -----------------------------
    # Construction & registries
    # -----------------------------
    def __init__(self, params: BaseUniverseParams):
        self.params = params
        info, kbs, tbs = self.params.info, self.params.kbs, self.params.tbs
        self.KBs: Dict[str, KnowledgeBase | MultimodalKnowledgeBase] = dict(kbs or {})
        self.TBs: Dict[str, ToolBox] = dict(tbs or {})
        self.info = info
        self.name = "NAMELESS"
        if info is not None:
            self.name = info.name
        # Initialize db_client for the universe
        self.db_client = chromadb.Client()

    # -----------------------------
    # Allowed actions & discovery
    # -----------------------------
    def allowed_actions(self) -> List[str]:
        """Return the list of supported actions in the Universe."""
        return [
            # Discovery
            "get_allowed_actions",
            "info",
            "get_stats",
            # KB registry ops
            "list_kbs",
            "add_kb",
            "remove_kb",
            # KB operations
            "kb_search",
            "kb_append_texts",
            "kb_upload_dir",
            "kb_add_url",
            "kb_add_urls",
            "kb_add_document",
            "kb_stats",
            "kb_sources",
            "kb_purge",
            "kb_get_document_by_id",
            # TB registry ops
            "list_tbs",
            "add_tb",
            "remove_tb",
            # TB operations
            "tb_search_tools",
            "tb_execute",
            "tb_tool_info",
            "tb_list_tools",
            "tb_append_docs",
            "tb_upload_docs",
            "tb_search_tool_docs",
            "tb_add_tool_from_meta",
            "tb_recursive_upload_tools",
            "tb_get_stats",
            # Tool listings
            "get_available_tools",
            "get_toolbox_tools",
        ]

    def get_info(self) -> Dict[str, Any]:
        """High-level discovery: list KBs, TBs, and allowed actions."""
        return {
            "node_info": self.info,
            "kbs": self.list_kbs(),
            "tbs": self.list_tbs(),
            "allowed_actions": self.allowed_actions(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics for all KBs and TBs."""
        kb_stats = {name: kb.get_stats() for name, kb in self.KBs.items()}
        tb_stats = {name: tb.get_stats() for name, tb in self.TBs.items()}
        return {
            "kbs": kb_stats,
            "tbs": tb_stats,
            "num_kbs": len(self.KBs),
            "num_tbs": len(self.TBs),
        }

    # -----------------------------
    # KB registry ops
    # -----------------------------
    def add_kb(self, name: str, kb: KnowledgeBase | MultimodalKnowledgeBase) -> None:
        self.KBs[name] = kb

    def remove_kb(self, name: str) -> bool:
        return self.KBs.pop(name, None) is not None

    def get_kb(self, name: str) -> KnowledgeBase | MultimodalKnowledgeBase:
        kb = self.KBs.get(name)
        if not kb:
            raise KeyError(f"Unknown KB: {name}")
        return kb

    def list_kbs(self) -> List[str]:
        return sorted(self.KBs.keys())

    # -----------------------------
    # TB registry ops
    # -----------------------------
    def add_tb(self, name: str, tb: ToolBox) -> None:
        self.TBs[name] = tb

    def remove_tb(self, name: str) -> bool:
        return self.TBs.pop(name, None) is not None

    def get_tb(self, name: str) -> ToolBox:
        tb = self.TBs.get(name)
        if not tb:
            raise KeyError(f"Unknown TB: {name}")
        return tb

    def list_tbs(self) -> List[str]:
        return sorted(self.TBs.keys())

    # -----------------------------
    # KB proxies (enhanced)
    # -----------------------------
    def kb_search(self, name: str, query: str, k: int = 5, context_window: int = 1) -> List[Dict[str, Any]]:
        return self.get_kb(name).search(query, k=k, with_score=False, context_window=context_window)

    async def akb_search(self, name: str, query: str, k: int = 5, context_window: int = 1) -> List[Dict[str, Any]]:
        return await self.get_kb(name).asearch(query, k=k, with_score=False, context_window=context_window)

    def kb_append_texts(self, name: str, texts: Sequence[str], doc_source: str = "universe") -> Any:
        return self.get_kb(name).add_text_docs(texts, doc_source=doc_source)

    async def akb_append_texts(self, name: str, texts: Sequence[str], doc_source: str = "universe") -> Any:
        return await self.get_kb(name).aadd_text_docs(texts, doc_source=doc_source)

    def kb_upload_dir(self, name: str, dir_path: str, target_ext: Optional[Sequence[str]] = None) -> Any:
        return self.get_kb(name).upload_dir(dir_path, target_file_ext=target_ext or [])

    async def akb_upload_dir(self, name: str, dir_path: str, target_ext: Optional[Sequence[str]] = None) -> Any:
        return await self.get_kb(name).aupload_dir(dir_path, target_file_ext=target_ext or [])

    def kb_add_url(self, name: str, url: str) -> Any:
        """Add a single HTML document from URL."""
        return self.get_kb(name).add_url_doc(url)

    async def akb_add_url(self, name: str, url: str) -> Any:
        """Add a single HTML document from URL (async)."""
        return await self.get_kb(name).aadd_url_doc(url)

    def kb_add_urls(self, name: str, urls: Sequence[str]) -> Any:
        """Add multiple HTML documents from URLs."""
        return self.get_kb(name).add_url_docs(urls)

    async def akb_add_urls(self, name: str, urls: Sequence[str]) -> Any:
        """Add multiple HTML documents from URLs (async)."""
        return await self.get_kb(name).aadd_url_docs(urls)

    def kb_add_document(self, name: str, content: Any, metadata: Optional[Dict[str, Any]] = None, modality: str = "text") -> Any:
        """Add a single document to a knowledge base (multimodal KB only)."""
        kb = self.get_kb(name)
        if not isinstance(kb, MultimodalKnowledgeBase):
            raise TypeError(f"KB '{name}' is not a multimodal knowledge base")
        return kb.add_document(content, metadata=metadata, modality=modality)

    async def akb_add_document(self, name: str, content: Any, metadata: Optional[Dict[str, Any]] = None, modality: str = "text") -> Any:
        """Add a single document to a knowledge base (multimodal KB only) (async)."""
        kb = self.get_kb(name)
        if not isinstance(kb, MultimodalKnowledgeBase):
            raise TypeError(f"KB '{name}' is not a multimodal knowledge base")
        # MultimodalKnowledgeBase.add_document is sync but uses _run_async_in_thread internally
        return kb.add_document(content, metadata=metadata, modality=modality)

    def kb_stats(self, name: str) -> Dict[str, int]:
        return self.get_kb(name).get_stats()

    def kb_sources(self, name: str) -> List[Dict[str, Any]]:
        return self.get_kb(name).list_sources()

    def kb_purge(self, name: str) -> None:
        self.get_kb(name).purge()

    async def akb_purge(self, name: str) -> None:
        await self.get_kb(name).apurge()

    def kb_get_document_by_id(self, name: str, document_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a document by its ID."""
        return self.get_kb(name).get_document_by_id(document_id)

    # -----------------------------
    # TB proxies (enhanced)
    # -----------------------------
    def tb_search_tools(self, name: str, query: str, k: int = 5) -> List[Dict[str, Any]]:
        return self.get_tb(name).search_tools(query, k=k)

    async def atb_search_tools(self, name: str, query: str, k: int = 5) -> List[Dict[str, Any]]:
        return await self.get_tb(name).asearch_tools(query, k=k)

    def tb_execute(self, name: str, tool_name: str, *fn_args: Any, **exec_kwargs) -> Dict[str, Any]:
        return self.get_tb(name).execute_tool(tool_name, *fn_args, **exec_kwargs)

    async def atb_execute(self, name: str, tool_name: str, *fn_args: Any, **exec_kwargs) -> Dict[str, Any]:
        return await self.get_tb(name).aexecute_tool(tool_name, *fn_args, **exec_kwargs)

    def tb_tool_info(self, name: str, tool_name: str) -> Optional[Dict[str, Any]]:
        return self.get_tb(name).tool_info(tool_name)

    def tb_list_tools(self, name: str) -> List[str]:
        """List all tools in a toolbox."""
        return self.get_tb(name).list_tools()

    def tb_append_docs(self, name: str, tool_name: str, texts: Sequence[str], doc_source: str = "universe") -> Any:
        return self.get_tb(name).append_tool_docs(tool_name, texts, doc_source=doc_source)

    async def atb_append_docs(self, name: str, tool_name: str, texts: Sequence[str], doc_source: str = "universe") -> Any:
        return await self.get_tb(name).aappend_tool_docs(tool_name, texts, doc_source=doc_source)

    def tb_upload_docs(self, name: str, tool_name: str, dir_path: str, target_ext: Optional[Sequence[str]] = None) -> Any:
        return self.get_tb(name).upload_tool_docs(tool_name, dir_path, target_file_ext=target_ext or [])

    async def atb_upload_docs(self, name: str, tool_name: str, dir_path: str, target_ext: Optional[Sequence[str]] = None) -> Any:
        return await self.get_tb(name).aupload_tool_docs(tool_name, dir_path, target_file_ext=target_ext or [])

    def tb_search_tool_docs(self, name: str, tool_name: str, query: str, k: int = 5, context_window: int = 1) -> List[Dict[str, Any]]:
        """Search tool documentation with context window."""
        return self.get_tb(name).search_tool_docs(tool_name, query, k=k, context_window=context_window)

    async def atb_search_tool_docs(self, name: str, tool_name: str, query: str, k: int = 5, context_window: int = 1) -> List[Dict[str, Any]]:
        """Search tool documentation with context window (async)."""
        return await self.get_tb(name).asearch_tool_docs(tool_name, query, k=k, context_window=context_window)

    def tb_add_tool_from_meta(self, name: str, meta: ToolMeta, kb_persist_dir: str) -> Tuple[str, str]:
        """Add a tool from ToolMeta."""
        return self.get_tb(name).add_tool_from_meta(meta, kb_persist_dir=kb_persist_dir)

    async def atb_add_tool_from_meta(self, name: str, meta: ToolMeta, kb_persist_dir: str) -> Tuple[str, str]:
        """Add a tool from ToolMeta (async)."""
        return await self.get_tb(name).aadd_tool_from_meta(meta, kb_persist_dir=kb_persist_dir)

    async def atb_recursive_upload_tools(self, name: str, path: str, extensions: Optional[List[str]] = None, kb_persist_dir: Optional[str] = None) -> List[Tuple[str, str]]:
        """Recursively discover and upload tools from a directory."""
        return await self.get_tb(name).recursive_upload_tools(path, extensions=extensions, kb_persist_dir=kb_persist_dir)

    def tb_get_stats(self, name: str) -> Dict[str, Any]:
        """Get toolbox statistics."""
        return self.get_tb(name).get_stats()

    # -----------------------------
    # Tool listing helpers
    # -----------------------------
    def get_available_tools(self) -> List[Dict[str, Any]]:
        """Return ToolCards for all tools across all ToolBoxes."""
        cards: List[Dict[str, Any]] = []
        for tb_name, tb in self.TBs.items():
            for tool_name, tool in tb.tools.items():
                cards.append({
                    "toolbox": tb_name,
                    "tool_name": tool_name,
                    "toolcard": tool.card.__dict__,
                })
        return cards

    def get_toolbox_tools(self, tb_name: str) -> List[Dict[str, Any]]:
        tb = self.get_tb(tb_name)
        return [
            {"toolbox": tb_name, "tool_name": name, "toolcard": tool.card.__dict__}
            for name, tool in tb.tools.items()
        ]


# --------------------
# FastAPI models
# --------------------
class CreateKBRequest(BaseModel):
    kb_params: KnowledgeBaseParams | MultimodalKnowledgeBaseParams = Field(..., description="Parameters of the KB")
    type: str = Field("text", description="Type of KB: 'text' for text-only or 'multimodal' for multimodal KB")


class CreateTBRequest(BaseModel):
    name: str
    index_persist_dir: str
    embedding_model: str = "all-MiniLM-L6-v2"
    inventory_path: Optional[str] = None
    registry_path: Optional[str] = None
    vrbz: int = 0


class SearchRequest(BaseModel):
    query: str
    k: int = 5
    context_window: int = 1


class AppendTextsRequest(BaseModel):
    texts: List[str]
    doc_source: str = "universe"


class UploadDirRequest(BaseModel):
    dir_path: str
    target_ext: Optional[List[str]] = None


class AddURLRequest(BaseModel):
    url: str


class AddURLsRequest(BaseModel):
    urls: List[str]


class AddDocumentRequest(BaseModel):
    content: str = Field(..., description="Content of the document (text, base64-encoded data, or file path)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Optional metadata for the document")
    modality: str = Field("text", description="Modality type: 'text', 'image', 'audio', 'video', 'table', 'binary'")


class ExecuteRequest(BaseModel):
    tool_name: str
    args: Optional[List[str]] = None
    fn_args: Optional[List[Any]] = None
    kwargs: Optional[Dict[str, Any]] = None
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None
    timeout: Optional[float] = None
    input_data: Optional[str] = None
    text: bool = True


class AddToolFromMetaRequest(BaseModel):
    meta: Dict[str, Any]  # ToolMeta as dict
    kb_persist_dir: str


class RecursiveUploadToolsRequest(BaseModel):
    path: str
    extensions: Optional[List[str]] = None
    kb_persist_dir: Optional[str] = None


# --------------------
# FastAPI factory
# --------------------
def create_app(universe: BaseUniverse, cors_origins: Optional[List[str]] = None) -> FastAPI:
    app = FastAPI(title="WOLF Universe", version="2.0.0")

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # -------- Discovery & actions --------
    @app.get("/health")
    def health():
        return {"status": "ok", "kbs": universe.list_kbs(), "tbs": universe.list_tbs()}

    @app.get("/actions")
    def get_allowed_actions():
        return universe.allowed_actions()

    @app.get("/info")
    def discovery_info():
        return universe.get_info()

    @app.get("/stats")
    def get_stats():
        return universe.get_stats()

    @app.get("/tools")
    def all_tools():
        return universe.get_available_tools()

    # --------------- KB endpoints ---------------
    @app.get("/kbs")
    def list_kbs():
        return universe.list_kbs()

    @app.post("/kbs")
    def create_kb(req: CreateKBRequest):
        kb_type = req.type.lower()

        if kb_type == "text":
            if not isinstance(req.kb_params, KnowledgeBaseParams):
                raise HTTPException(status_code=400, detail="For 'text' type, kb_params must be KnowledgeBaseParams")
            if req.kb_params.name in universe.KBs:
                raise HTTPException(status_code=409, detail=f"KB {req.kb_params.name} already exists")
            kb = KnowledgeBase(req.kb_params, universe.db_client)
            universe.add_kb(req.kb_params.name, kb)
            return {"ok": True, "name": req.kb_params.name, "type": "text"}

        elif kb_type == "multimodal":
            if not isinstance(req.kb_params, MultimodalKnowledgeBaseParams):
                raise HTTPException(status_code=400, detail="For 'multimodal' type, kb_params must be MultimodalKnowledgeBaseParams")
            if req.kb_params.name in universe.KBs:
                raise HTTPException(status_code=409, detail=f"KB {req.kb_params.name} already exists")
            kb = MultimodalKnowledgeBase(req.kb_params, universe.db_client)
            universe.add_kb(req.kb_params.name, kb)
            return {"ok": True, "name": req.kb_params.name, "type": "multimodal"}

        else:
            raise HTTPException(status_code=400, detail=f"Invalid KB type: {req.type}. Must be 'text' or 'multimodal'")

    @app.delete("/kbs/{name}")
    def delete_kb(name: str):
        if not universe.remove_kb(name):
            raise HTTPException(status_code=404, detail="KB not found")
        return {"ok": True}

    @app.post("/kbs/{name}/search")
    async def kb_search(name: str, req: SearchRequest):
        try:
            return await universe.akb_search(name, req.query, k=req.k, context_window=req.context_window)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.get("/kbs/{name}/stats")
    def kb_stats(name: str):
        try:
            return universe.kb_stats(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.get("/kbs/{name}/sources")
    def kb_sources(name: str):
        try:
            return universe.kb_sources(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.post("/kbs/{name}/append_texts")
    async def kb_append(name: str, req: AppendTextsRequest):
        try:
            return await universe.akb_append_texts(name, req.texts, doc_source=req.doc_source)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.post("/kbs/{name}/upload_dir")
    async def kb_upload_dir(name: str, req: UploadDirRequest):
        """Upload directory contents to a knowledge base."""
        try:
            # Get KB to check it exists
            kb = universe.get_kb(name)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"KB '{name}' not found. Universe is running on host: {universe.info.host if universe.info else 'unknown'}"
            )
        
        # Expand user path and validate directory exists
        dir_path = os.path.expanduser(req.dir_path)
        if not os.path.exists(dir_path):
            raise HTTPException(
                status_code=400,
                detail=f"Directory '{req.dir_path}' does not exist on universe host {universe.info.host if universe.info else 'unknown'}:{universe.info.port if universe.info else 'unknown'}. Please verify the path is accessible from the universe's runtime environment."
            )
        
        if not os.path.isdir(dir_path):
            raise HTTPException(
                status_code=400,
                detail=f"Path '{req.dir_path}' exists but is not a directory on host {universe.info.host if universe.info else 'unknown'}. Please provide a valid directory path."
            )
        
        try:
            # Attempt to upload
            result = await universe.akb_upload_dir(name, dir_path, target_ext=req.target_ext)
            
            # Provide detailed feedback
            return {
                "ok": True,
                "kb_name": name,
                "dir_path": req.dir_path,
                "host": universe.info.host if universe.info else "unknown",
                "port": universe.info.port if universe.info else "unknown",
                "result": result,
                "message": f"Successfully uploaded contents from '{req.dir_path}' to KB '{name}'"
            }
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error uploading directory to KB '{name}' on host {universe.info.host if universe.info else 'unknown'}:{universe.info.port if universe.info else 'unknown'}. Error: {str(e)}"
            )

    @app.post("/kbs/{name}/add_url")
    async def kb_add_url(name: str, req: AddURLRequest):
        try:
            return await universe.akb_add_url(name, req.url)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.post("/kbs/{name}/add_urls")
    async def kb_add_urls(name: str, req: AddURLsRequest):
        try:
            return await universe.akb_add_urls(name, req.urls)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.post("/kbs/{name}/add_document")
    async def kb_add_document(name: str, req: AddDocumentRequest):
        """Add a single document to a multimodal knowledge base."""
        try:
            # Convert content based on modality
            if req.modality == "text":
                # Check if content is a file path - if so, convert to Path object
                # so that MultimodalKnowledgeBase.add_document can detect PDFs
                if os.path.exists(req.content):
                    content = Path(req.content)
                else:
                    # Raw text content
                    content = req.content
            else:
                # For non-text modalities, assume content is a file path or base64 data
                # Try as file path first
                if os.path.exists(req.content):
                    content = Path(req.content)
                else:
                    # Assume it's base64-encoded bytes
                    import base64
                    try:
                        content = base64.b64decode(req.content)
                    except Exception:
                        raise HTTPException(
                            status_code=400,
                            detail=f"For modality '{req.modality}', content must be a valid file path or base64-encoded data"
                        )

            result = await universe.akb_add_document(name, content, metadata=req.metadata, modality=req.modality)
            return {
                "ok": True,
                "kb_name": name,
                "modality": req.modality,
                "result": result,
                "message": f"Successfully added {req.modality} document to KB '{name}'"
            }
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")
        except TypeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error adding document to KB '{name}': {str(e)}"
            )

    @app.post("/kbs/{name}/purge")
    async def kb_purge(name: str):
        try:
            await universe.akb_purge(name)
            return {"ok": True}
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.get("/kbs/{name}/document/{document_id}")
    def kb_get_document(name: str, document_id: str):
        try:
            doc = universe.kb_get_document_by_id(name, document_id)
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found")
            return doc
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    # --------------- TB endpoints ---------------
    @app.get("/tbs")
    def list_tbs():
        return universe.list_tbs()

    @app.post("/tbs")
    def create_tb(req: CreateTBRequest):
        if req.name in universe.TBs:
            raise HTTPException(status_code=409, detail="TB already exists")
        params = {
            "name": req.name,
            "index_persist_dir": req.index_persist_dir,
            "embedding_model": req.embedding_model,
            "inventory_path": req.inventory_path,
            "registry_path": req.registry_path,
            "vrbz": req.vrbz,
        }
        tb = ToolBox(params)
        universe.add_tb(req.name, tb)
        return {"ok": True, "name": req.name}

    @app.delete("/tbs/{name}")
    def delete_tb(name: str):
        if not universe.remove_tb(name):
            raise HTTPException(status_code=404, detail="TB not found")
        return {"ok": True}

    @app.post("/tbs/{name}/search")
    async def tb_search(name: str, req: SearchRequest):
        try:
            return await universe.atb_search_tools(name, req.query, k=req.k)
        except KeyError:
            raise HTTPException(status_code=404, detail="TB not found")

    @app.get("/tbs/{name}/tools")
    def tb_list_tools(name: str):
        try:
            return universe.get_toolbox_tools(name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/tbs/{name}/stats")
    def tb_get_stats(name: str):
        try:
            return universe.tb_get_stats(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="TB not found")

    @app.get("/tbs/{name}/tools/{tool}/info")
    def tb_tool_info(name: str, tool: str):
        try:
            info = universe.tb_tool_info(name, tool)
            if info is None:
                raise HTTPException(status_code=404, detail="Tool not found in TB")
            return info
        except KeyError:
            raise HTTPException(status_code=404, detail="TB not found")

    @app.post("/tbs/{name}/tools/{tool}/append_texts")
    async def tb_append_docs(name: str, tool: str, req: AppendTextsRequest):
        try:
            return await universe.atb_append_docs(name, tool, req.texts, doc_source=req.doc_source)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/tbs/{name}/tools/{tool}/search_docs")
    async def tb_search_tool_docs(name: str, tool: str, req: SearchRequest):
        try:
            return await universe.atb_search_tool_docs(name, tool, req.query, k=req.k, context_window=req.context_window)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/tbs/{name}/add_tool_from_meta")
    async def tb_add_tool_from_meta(name: str, req: AddToolFromMetaRequest):
        try:
            meta = ToolMeta(**req.meta)
            return await universe.atb_add_tool_from_meta(name, meta, kb_persist_dir=req.kb_persist_dir)
        except KeyError:
            raise HTTPException(status_code=404, detail="TB not found")

    @app.post("/tbs/{name}/recursive_upload_tools")
    async def tb_recursive_upload_tools(name: str, req: RecursiveUploadToolsRequest):
        try:
            return await universe.atb_recursive_upload_tools(name, req.path, extensions=req.extensions, kb_persist_dir=req.kb_persist_dir)
        except KeyError:
            raise HTTPException(status_code=404, detail="TB not found")

    @app.post("/tbs/{name}/execute")
    async def tb_execute(name: str, req: ExecuteRequest):
        try:
            if req.fn_args is not None:
                args_for_fn = list(req.fn_args)
                return await universe.atb_execute(
                    name,
                    req.tool_name,
                    *args_for_fn,
                    args=req.args,
                    kwargs=req.kwargs or {},
                    env=req.env,
                    cwd=req.cwd,
                    timeout=req.timeout,
                    input_data=req.input_data,
                    text=req.text,
                )
            else:
                return await universe.atb_execute(
                    name,
                    req.tool_name,
                    args=req.args,
                    kwargs=req.kwargs or {},
                    env=req.env,
                    cwd=req.cwd,
                    timeout=req.timeout,
                    input_data=req.input_data,
                    text=req.text,
                )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    return app


def build_default_universe(params: base_universe_params_type|None = None) -> BaseUniverse:
    """Create an base Universe. Extend this in your app bootstrap."""
    if params is not None: 
        return BaseUniverse(params=params)
    else:
        name_generator = NameGenerator()
        info = BaseUniverseModel(name=name_generator.get_name())
        _params = BaseUniverseParams(info=info)
        return BaseUniverse(params=_params)

def create_app_default() -> FastAPI:
    """Zero-argument ASGI factory for uvicorn --factory."""
    universe = build_default_universe()
    return create_app(universe)


def run_app(
    params: BaseUniverseParams | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    cors: Optional[List[str]] = None,
    status_file: Optional[str] = None,
) -> None:
    import json
    from pathlib import Path
    
    host = host.strip()

    if params is None:
        name_generator = NameGenerator()
        info = BaseUniverseModel(
            name=name_generator.get_name(),
            host=host,
            port=port,
        )
        _params = BaseUniverseParams(info=info)
    else:
        _params = params
        if _params.info is None:
            raise ValueError("params.info must not be None")
        _params.info.host = host
        _params.info.port = port

    universe = build_default_universe(_params)
    app = create_app(universe, cors_origins=cors)

    # Pre-bind socket ourselves so we know the real port before Uvicorn starts.
    import socket
    import uvicorn
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))          # port=0 => OS picks a free port
    sock.listen()
    actual_port = sock.getsockname()[1]

    _params.info.port = actual_port
    base_url = _params.info.get_base_url()

    # Write status file with complete information after socket binding
    if status_file:
        try:
            status_data = {
                "status": "ready",
                "host": host,
                "port": actual_port,
                "url": base_url
            }
            Path(status_file).write_text(json.dumps(status_data), encoding="utf-8")
        except Exception as e:
            print(f"Warning: Failed to write status file: {e}")

    print(f"Universe available at {base_url}")

    config = uvicorn.Config(app=app, host=host, port=actual_port)
    server = uvicorn.Server(config)
    server.run(sockets=[sock])
