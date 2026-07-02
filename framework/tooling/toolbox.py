from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from rich.console import Console

import chromadb
from framework.knowledgebase.knowledge_base import KnowledgeBase, IngestResult
from framework.knowledgebase.base_multimodal_knowledgebase import MultimodalKnowledgeBase
from framework.knowledgebase.data_models import KnowledgeBaseParams, MultimodalKnowledgeBaseParams
from framework.tooling.tools import Tool, ToolCard
from framework.tooling.tool_models import ToolMeta

console = Console()


@dataclass
class ToolBoxParams:
    name: str = "toolbox"
    index_persist_dir: str = "./VSTORE/toolbox_index"
    embedding_model: str = "all-MiniLM-L6-v2"
    inventory_path: Optional[str] = None
    vrbz: int = 0
    registry_path: Optional[str] = None  # JSON file to persist registries
    use_multimodal: bool = False  # Enable multimodal KB support


class ToolBox:
    """ToolBox indexes, manages, and executes Tools.
    
    Enhanced features:
    - Persistent tool registry (tool_id <-> tool_name mapping)
    - Full async support for all operations
    - Tool documentation management (text and multimodal)
    - Tool execution (sync and async)
    - Semantic tool search
    - Support for both ToolCard and ToolMeta
    - Multimodal KB support for rich tool documentation
    """

    def __init__(self, params: Dict[str, Any] | ToolBoxParams, db_client: chromadb.Client):
        if isinstance(params, dict):
            params = ToolBoxParams(**params)

        self.name = params.name
        self.vrbz = int(params.vrbz)
        self.registry_path = params.registry_path
        self.db_client = db_client
        self.use_multimodal = params.use_multimodal

        # Initialize KB (text or multimodal based on configuration)
        if self.use_multimodal:
            kb_params = MultimodalKnowledgeBaseParams(
                name=f"{self.name}_kb",
                persist_dir=params.index_persist_dir,
                embedding={
                    "model_name": params.embedding_model,
                    "modalities": ["text", "image", "audio", "video", "table"]
                },
                chunk_size=500,
                chunk_overlap=50,
                vrbz=self.vrbz
            )
            self.index = MultimodalKnowledgeBase(kb_params, self.db_client)
        else:
            vstore_params = {
                "collection_name": f"{self.name}_index",
                "persist_directory": params.index_persist_dir,
                "embedding_model": params.embedding_model,
            }
            kb_params = {
                "name": f"{self.name}_kb",
                "vstore_params": vstore_params,
                "inventory_path": params.inventory_path or os.path.join(params.index_persist_dir, f"{self.name}_inventory.sqlite"),
                "vrbz": self.vrbz,
            }
            self.index = KnowledgeBase(KnowledgeBaseParams(**kb_params), self.db_client)

        # Bidirectional registry
        self.tool_id: Dict[str, str] = {}  # name -> vector_id
        self.tool_name: Dict[str, str] = {}  # vector_id -> name
        self.tools: Dict[str, Tool] = {}  # name -> Tool

        if self.registry_path and os.path.exists(self.registry_path):
            try:
                data = json.loads(Path(self.registry_path).read_text(encoding="utf-8"))
                self.tool_id = data.get("tool_id", {})
                self.tool_name = data.get("tool_name", {})
            except Exception as e:
                console.print(f"[!][ToolBox] Failed to read registry: {e}")

    def _save_registry(self) -> None:
        """Persist the tool registry to disk."""
        if not self.registry_path:
            return
        Path(os.path.dirname(self.registry_path) or ".").mkdir(parents=True, exist_ok=True)
        payload = {"tool_id": self.tool_id, "tool_name": self.tool_name}
        Path(self.registry_path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    # -------------- Add / Remove / Replace --------------

    def add_tools(self, tools: Sequence[Tool]) -> List[Tuple[str, str]]:
        """Add multiple tools to the toolbox (sync)."""
        results: List[Tuple[str, str]] = []
        for t in tools:
            name = t.card.name
            card_json = t.card.to_json()
            ingest: IngestResult = self.index.add_text_docs([card_json], doc_source=f"toolcard:{name}")
            toolcard_id = ingest.v_ids[0] if ingest.v_ids else None
            if not toolcard_id:
                raise RuntimeError(f"Failed to index ToolCard for {name}")
            self.tool_id[name] = toolcard_id
            self.tool_name[toolcard_id] = name
            self.tools[name] = t
            results.append((name, toolcard_id))
        self._save_registry()
        return results

    async def aadd_tools(self, tools: Sequence[Tool]) -> List[Tuple[str, str]]:
        """Add multiple tools to the toolbox (async)."""
        results: List[Tuple[str, str]] = []
        for t in tools:
            name = t.card.name
            card_json = t.card.to_json()
            ingest: IngestResult = await self.index.aadd_text_docs([card_json], doc_source=f"toolcard:{name}")
            toolcard_id = ingest.v_ids[0] if ingest.v_ids else None
            if not toolcard_id:
                raise RuntimeError(f"Failed to index ToolCard for {name}")
            self.tool_id[name] = toolcard_id
            self.tool_name[toolcard_id] = name
            self.tools[name] = t
            results.append((name, toolcard_id))
        self._save_registry()
        return results

    def add_tool_from_meta(self, meta: ToolMeta, kb_persist_dir: str) -> Tuple[str, str]:
        """Create and add a tool from ToolMeta."""
        tool = Tool.from_tool_meta(meta, kb_persist_dir=kb_persist_dir, vrbz=self.vrbz)
        return self.add_tools([tool])[0]

    async def aadd_tool_from_meta(self, meta: ToolMeta, kb_persist_dir: str) -> Tuple[str, str]:
        """Create and add a tool from ToolMeta (async)."""
        tool = Tool.from_tool_meta(meta, kb_persist_dir=kb_persist_dir, vrbz=self.vrbz)
        return (await self.aadd_tools([tool]))[0]

    def remove_tool(self, name: str) -> bool:
        """Remove a tool from the toolbox."""
        toolcard_id = self.tool_id.pop(name, None)
        if toolcard_id:
            self.tool_name.pop(toolcard_id, None)
            self.tools.pop(name, None)
            self._save_registry()
            return True
        return False

    def replace_tool(self, name: str, new_tool: Tool) -> Tuple[str, str]:
        """Replace an existing tool with a new one."""
        self.remove_tool(name)
        return self.add_tools([new_tool])[0]

    async def areplace_tool(self, name: str, new_tool: Tool) -> Tuple[str, str]:
        """Replace an existing tool with a new one (async)."""
        self.remove_tool(name)
        return (await self.aadd_tools([new_tool]))[0]

    # -------------- Discovery & Info --------------

    def search_tools(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Search for tools by semantic query (sync)."""
        hits = self.index.search(query, k=k, with_score=False)
        results: List[Dict[str, Any]] = []
        for h in hits:
            try:
                card_obj = json.loads(h.get("document", h.get("page_content", "{}")))
                name = card_obj.get("name")
            except Exception:
                name = None
                card_obj = None
            results.append({"tool_name": name, "toolcard": card_obj, "raw": h})
        return results

    async def asearch_tools(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Search for tools by semantic query (async)."""
        hits = await self.index.asearch(query, k=k, with_score=False)
        results: List[Dict[str, Any]] = []
        for h in hits:
            try:
                card_obj = json.loads(h.get("document", h.get("page_content", "{}")))
                name = card_obj.get("name")
            except Exception:
                name = None
                card_obj = None
            results.append({"tool_name": name, "toolcard": card_obj, "raw": h})
        return results

    def tool_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a tool."""
        t = self.tools.get(name)
        if not t:
            return None
        info = t.info()
        
        # Add modality information if using multimodal KB
        if self.use_multimodal and hasattr(t.index, 'list_modalities'):
            info['modalities'] = t.index.list_modalities()
        
        return info

    def list_tools(self) -> List[str]:
        """List all tool names in the toolbox."""
        return list(self.tools.keys())

    def get_tool(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self.tools.get(name)

    # -------------- Tool Documentation --------------

    def search_tool_docs(self, name: str, query: str, k: int = 5, context_window: int = 1) -> List[Dict[str, Any]]:
        """Search tool documentation (sync)."""
        t = self.tools.get(name)
        if not t:
            raise KeyError(f"Unknown tool: {name}")
        return t.search(query, k=k, with_score=False, context_window=context_window)

    async def asearch_tool_docs(self, name: str, query: str, k: int = 5, context_window: int = 1) -> List[Dict[str, Any]]:
        """Search tool documentation (async)."""
        t = self.tools.get(name)
        if not t:
            raise KeyError(f"Unknown tool: {name}")
        return await t.asearch(query, k=k, with_score=False, context_window=context_window)

    def append_tool_docs(self, name: str, texts: Sequence[str], doc_source: str = "toolbox") -> Any:
        """Add text documents to tool documentation (sync)."""
        t = self.tools.get(name)
        if not t:
            raise KeyError(f"Unknown tool: {name}")
        return t.add_text_docs(texts, doc_source=doc_source)

    async def aappend_tool_docs(self, name: str, texts: Sequence[str], doc_source: str = "toolbox") -> Any:
        """Add text documents to tool documentation (async)."""
        t = self.tools.get(name)
        if not t:
            raise KeyError(f"Unknown tool: {name}")
        return await t.aadd_text_docs(texts, doc_source=doc_source)

    def upload_tool_docs(self, name: str, dir_path: str, target_file_ext: Sequence[str] | None = None) -> Any:
        """Upload documentation directory to tool (sync)."""
        t = self.tools.get(name)
        if not t:
            raise KeyError(f"Unknown tool: {name}")
        return t.upload_dir(dir_path, target_file_ext=target_file_ext)

    async def aupload_tool_docs(self, name: str, dir_path: str, target_file_ext: Sequence[str] | None = None) -> Any:
        """Upload documentation directory to tool (async)."""
        t = self.tools.get(name)
        if not t:
            raise KeyError(f"Unknown tool: {name}")
        return await t.aupload_dir(dir_path, target_file_ext=target_file_ext)

    # -------------- Tool Execution --------------

    def execute_tool(
        self,
        name: str,
        *fn_args: Any,
        args: Optional[Sequence[str]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        **exec_kwargs
    ) -> Dict[str, Any]:
        """Execute a tool (sync)."""
        t = self.tools.get(name)
        if not t:
            raise KeyError(f"Unknown tool: {name}")
        return t.execute(*fn_args, args=args, kwargs=kwargs, **exec_kwargs)

    async def aexecute_tool(
        self,
        name: str,
        *fn_args: Any,
        args: Optional[Sequence[str]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        **exec_kwargs
    ) -> Dict[str, Any]:
        """Execute a tool (async)."""
        t = self.tools.get(name)
        if not t:
            raise KeyError(f"Unknown tool: {name}")
        return await t.aexecute(*fn_args, args=args, kwargs=kwargs, **exec_kwargs)

    # -------------- Tool Discovery from Filesystem --------------

    async def recursive_upload_tools(
        self,
        path: str,
        extensions: Optional[List[str]] = None,
        kb_persist_dir: Optional[str] = None
    ) -> List[Tuple[str, str]]:
        """Walk a directory and auto-discover tools from matching files."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)

        if kb_persist_dir is None:
            kb_persist_dir = os.path.join(self.index.vstore.persist_directory, "tools")

        exts_map = {
            "py": "python_script",
            "sh": "shell_script",
            "go": "go_script",
            "js": "js_script",
            "ts": "typescript_script",
            "rb": "ruby_script",
            "pl": "perl_script",
            "lua": "lua_script",
            "rs": "rust_script",
        }

        results = []
        for file in path.rglob("*"):
            if file.is_file():
                ext = file.suffix.lstrip(".")
                if extensions and ext not in extensions:
                    continue

                tool_type = exts_map.get(ext, "binary")

                meta = ToolMeta(
                    name=file.stem,
                    description=f"Auto-discovered tool from {file}",
                    args=[],
                    tool_type=tool_type,
                    path=str(file),
                )
                
                try:
                    result = await self.aadd_tool_from_meta(meta, kb_persist_dir=kb_persist_dir)
                    results.append(result)
                except Exception as e:
                    console.print(f"[!][ToolBox] Failed to add tool from {file}: {e}")

        return results

    # -------------- Maintenance --------------

    def purge_index(self) -> None:
        """Purge the toolbox index (sync)."""
        self.index.purge()
        self.tool_id.clear()
        self.tool_name.clear()
        self._save_registry()

    async def apurge_index(self) -> None:
        """Purge the toolbox index (async)."""
        await self.index.apurge()
        self.tool_id.clear()
        self.tool_name.clear()
        self._save_registry()

    def get_stats(self) -> Dict[str, Any]:
        """Get toolbox statistics."""
        stats = {
            "name": self.name,
            "num_tools": len(self.tools),
            "tool_names": list(self.tools.keys()),
            "index_stats": self.index.get_stats(),
            "multimodal_enabled": self.use_multimodal
        }
        
        # Add modality breakdown for multimodal toolboxes
        if self.use_multimodal and hasattr(self.index, 'list_modalities'):
            stats["modalities"] = self.index.list_modalities()
        
        return stats
