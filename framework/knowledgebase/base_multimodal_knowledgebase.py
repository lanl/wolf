from __future__ import annotations

import pathlib
import asyncio
import re
import concurrent.futures
import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Union
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Core imports
# ---------------------------------------------------------------------------
import chromadb
from chromadb.config import Settings
from framework.data_store.data_models import MultimodalEmbeddingParams, MultimodalVectorStoreParams
from framework.data_store.multimodal_vstore import MultimodalVectorStore
from framework.knowledgebase.data_models import MultimodalKnowledgeBaseParams

# ---------------------------------------------------------------------------
# Inventory (SQLite) schema for multimodal KB
# ---------------------------------------------------------------------------

MULTIMODAL_INVENTORY_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT,
    doc_type   TEXT,
    modality   TEXT,    -- 'text', 'image', 'audio', 'video', 'table', 'binary'
    v_ids_json TEXT,    -- JSON array of vector IDs associated with this source
    n_chunks   INTEGER,
    n_tokens   INTEGER,
    added_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_source_path ON documents(source_path);
CREATE INDEX IF NOT EXISTS idx_documents_modality ON documents(modality);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    v_id       TEXT,      -- vector id returned by the vstore
    source_path TEXT,
    modality   TEXT,      -- modality of this chunk
    line_start INTEGER,   -- line number where chunk starts (for text)
    line_end   INTEGER,   -- line number where chunk ends (for text)
    position   INTEGER,   -- position/order within the source
    n_tokens   INTEGER,
    metadata_json TEXT,
    added_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_v_id ON chunks(v_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_path);
CREATE INDEX IF NOT EXISTS idx_chunks_modality ON chunks(modality);
"""

def _now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    dt = datetime.now(timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")

def _count_tokens(text: str) -> int:
    """Conservative token proxy (whitespace split)."""
    return len(str(text).split())

# ---------------------------------------------------------------------------
# Types accepted by the public API
# ---------------------------------------------------------------------------
SupportedModalities = Union[
    str,               # raw text string
    pathlib.Path,      # local file path (any modality)
    bytes,             # raw bytes for non‑text modalities
]

# ---------------------------------------------------------------------------
# MultimodalKnowledgeBase – independent wrapper (no inheritance)
# ---------------------------------------------------------------------------
class MultimodalKnowledgeBase:
    """Knowledge base that supports text, images, audio, video and binary data.

    The async methods required by the tests (``add_text_docs``, ``query``,
    ``close``) directly delegate to the underlying ``MultiModalVectorStore``.
    All other helpers (``add_document``, ``add_documents``, ``add_texts``,
    ``delete_collection``, …) are kept for API compatibility and simply forward
    to the same store.

    Includes SQLite inventory tracking similar to KnowledgeBase.
    """

    # ---------------------------------------------------------------------
    # Construction – normalise params and create the underlying store
    # ---------------------------------------------------------------------
    def __init__(self, params: MultimodalKnowledgeBaseParams, db_client: chromadb.Client):
        """Create a new multimodal KB.

        ``params`` is an instance of ``MultimodalKnowledgeBaseParams``.
        ``db_client`` is the chromadb.Client instance (session-wide client).
        """
        self.params = params
        self.name = params.name
        self.VRBZ = int(params.vrbz)

        # Initialize MultimodalVectorStoreParams from MultimodalKnowledgeBaseParams
        vs_params = MultimodalVectorStoreParams(
            collection_name=f"kb_{params.name}_collection",
            chunk_size=params.chunk_size,
            chunk_overlap=params.chunk_overlap,
            persist_directory=params.persist_dir or "./chroma_db",
            rebuild_vstore=params.rebuild_vstore,
            embedding=params.embedding,
            use_bm25=params.use_bm25,
            use_rrf=params.use_rrf,
            rrf_k=params.rrf_k,
            use_reranker=params.use_reranker,
            reranker_model=params.reranker_model,
            allow_online=params.allow_online,
            http_timeout=params.http_timeout,
            vs_VRBZ=params.vrbz
        )

        # Instantiate the underlying MultimodalVectorStore with the session-wide client
        self.store = MultimodalVectorStore(
            params=vs_params,
            client=db_client
        )
        self.default_collection: str = vs_params.collection_name

        # Ensure a logger is configured (info level by default)
        import logging
        self._logger = logging.getLogger(__name__)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("[%(levelname)s] %(message)s")
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)

        # Backward‑compatibility shim – expose the client for callers that may need direct access
        self.client = db_client

        # Initialize inventory
        inv_dir = params.persist_dir or "./chroma_db"
        pathlib.Path(inv_dir).mkdir(parents=True, exist_ok=True)
        self.inventory_path = os.path.join(inv_dir, f"{self.name}_inventory.sqlite")
        self._init_inventory()

    # --------------------------
    # Inventory helpers
    # --------------------------

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.inventory_path, isolation_level=None)

    def _init_inventory(self) -> None:
        with self._connect() as cx:
            cx.executescript(MULTIMODAL_INVENTORY_SCHEMA)

    def _record_document(self, source_path: str, doc_type: str, modality: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Record a document in the inventory after it's been added to the vector store."""
        n_chunks = len(results)
        n_tokens = sum(_count_tokens(str(r.get("document", ""))) for r in results)
        v_ids = [r.get("id", f"{source_path}_chunk_{i}") for i, r in enumerate(results)]
        added_at = _now_iso()

        with self._connect() as cx:
            cx.execute(
                "INSERT INTO documents (source_path, doc_type, modality, v_ids_json, n_chunks, n_tokens, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source_path, doc_type, modality, json.dumps(v_ids), n_chunks, n_tokens, added_at),
            )
            # Store chunk-level info
            for i, result in enumerate(results):
                meta = result.get("metadata", {})
                v_id = result.get("id", f"{source_path}_chunk_{i}")
                line_start = meta.get("line_start", 0)
                line_end = meta.get("line_end", 0)
                chunk_id = meta.get("chunk_id", i)
                content = result.get("document", "")

                cx.execute(
                    "INSERT INTO chunks (v_id, source_path, modality, line_start, line_end, position, n_tokens, metadata_json, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (v_id, source_path, modality, line_start, line_end, chunk_id, _count_tokens(str(content)), json.dumps(meta), added_at),
                )

        return {"source_path": source_path, "doc_type": doc_type, "modality": modality, "n_chunks": n_chunks, "n_tokens": n_tokens, "v_ids": v_ids}

    def inventory_stats(self) -> Dict[str, Any]:
        """Get statistics about the knowledge base inventory."""
        with self._connect() as cx:
            cur = cx.execute("SELECT COUNT(*), COALESCE(SUM(n_chunks),0), COALESCE(SUM(n_tokens),0) FROM documents")
            n_docs, n_chunks, n_tokens = cur.fetchone()

            # Get modality breakdown
            cur = cx.execute("SELECT modality, COUNT(*), COALESCE(SUM(n_chunks),0) FROM documents GROUP BY modality")
            modality_stats = {row[0]: {"n_docs": row[1], "n_chunks": row[2]} for row in cur.fetchall()}

        return {
            "n_sources": int(n_docs),
            "n_chunks": int(n_chunks),
            "n_tokens": int(n_tokens),
            "modalities": modality_stats
        }

    def list_sources(self) -> List[Dict[str, Any]]:
        """List all sources in the knowledge base."""
        with self._connect() as cx:
            cur = cx.execute("SELECT source_path, doc_type, modality, n_chunks, n_tokens, added_at FROM documents ORDER BY added_at DESC")
            return [
                {"source_path": r[0], "doc_type": r[1], "modality": r[2], "n_chunks": r[3], "n_tokens": r[4], "added_at": r[5]}
                for r in cur.fetchall()
            ]

    # ---------------------------------------------------------------------
    # Async API required by the test suite
    # ---------------------------------------------------------------------
    async def add_text_docs(
        self,
        texts: List[str],
        doc_source: str = "user",
        pbar: Optional[str] = None,
    ) -> None:
        """Asynchronously ingest plain‑text documents.

        Mirrors the behaviour of ``KnowledgeBase.add_text_docs`` but forwards
        straight to the multimodal store.
        """
        await self.store.add_text_docs(texts, doc_source=doc_source)

        # Record in inventory
        results = await self.store.query_hybrid(query=doc_source, k=len(texts) * 10)
        filtered_results = [r for r in results if r.get("metadata", {}).get("source") == doc_source]
        if filtered_results:
            self._record_document(source_path=f"text://{doc_source}", doc_type="text", modality="text", results=filtered_results)

    async def query(
        self,
        query: str,
        n_results: int = 5,
        filter: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Run a query against the multimodal knowledge base.

        ``filter`` may contain ``channel_weights``; this logic is retained.  The
        default number of results is 5 (the test asks for five hits).  No
        modality‑specific filtering is performed – the store ranks all modalities
        together.
        """
        # ----- detect visual intent ------------------------------------------------
        visual_keywords = ["picture", "image", "photo", "diagram", "figure", "show", "display"]
        is_visual = any(re.search(r"\b" + kw + r"\b", query, re.IGNORECASE) for kw in visual_keywords)

        # Preserve original ``channel_weights`` handling.
        channel_weights: Optional[Dict[str, float]] = None
        if filter is not None:
            filter = dict(filter)  # shallow copy
            if "channel_weights" in filter:
                channel_weights = filter.pop("channel_weights")
                if not filter:
                    filter = None

        # ----- if visual query and user did not specify weights, apply a boost ----
        if is_visual and channel_weights is None:
            channel_weights = {"image": 2.0, "text": 0.5}  # tune as needed

        store_kwargs: Dict[str, Any] = {
            "query": query,
            "k": n_results,
            "filter": filter,
            **kwargs,
        }
        if channel_weights is not None:
            store_kwargs["channel_weights"] = channel_weights

        return await self.store.query_hybrid(**store_kwargs)

    async def close(self) -> None:
        """Close any resources held by the underlying store."""
        await self.store.close()

    # ---------------------------------------------------------------------
    # Compatibility async search interface expected by BaseUniverse
    # ---------------------------------------------------------------------
    async def asearch(
        self,
        query: str,
        k: int = 5,
        with_score: bool = False,
        context_window: int = 1,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Alias for ``query`` to satisfy ``BaseUniverse`` expectations.
        ``with_score`` and ``context_window`` are ignored as the multimodal store
        does not expose them directly; they are kept for signature compatibility.
        """
        return await self.query(query, n_results=k, **kwargs)

    # ---------------------------------------------------------------------
    # Synchronous convenience helpers (kept for backward compatibility)
    # ---------------------------------------------------------------------
    def _run_async_in_thread(self, coro):
        """Run an async coroutine in a thread to avoid ``asyncio.run`` errors.
        """
        def runner():
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(runner)
            return future.result()

    def add_document(
        self,
        content: SupportedModalities,
        metadata: Optional[Dict[str, Any]] = None,
        modality: str = "text",
        collection: Optional[str] = None,
    ) -> List[str]:
        """Add a single document (or file) to the knowledge base.

        For all modalities (text, table, image, audio, video, binary), we now
        use the unified store.add_documents method with the binary_payload parameter.
        """
        col = collection or self.default_collection

        # ----- Text modality ------------------------------------------------
        if modality == "text":
            if isinstance(content, pathlib.Path):
                coro = self.store.add_documents([str(content)])
                self._run_async_in_thread(coro)
                return []
            else:
                coro = self.store.add_text_docs([str(content)])
                self._run_async_in_thread(coro)
                return []

        # ----- All other modalities (table, image, audio, video, binary) ------
        if isinstance(content, pathlib.Path):
            data = content.read_bytes()
            # Extract the filename for metadata
            if metadata is None:
                metadata = {}
            if "source_file" not in metadata:
                metadata["source_file"] = content.name
        elif isinstance(content, bytes):
            data = content
        elif isinstance(content, str):
            # For table modality, accept raw string
            data = content.encode("utf-8")
        else:
            raise ValueError(
                f"Unsupported content type for modality '{modality}': {type(content)}"
            )

        payload = {modality: data}
        if metadata:
            payload["metadata"] = metadata
        self._logger.info("Adding %s document via store.add_documents", modality)
        coro = self.store.add_documents([], binary_payload=payload)
        self._run_async_in_thread(coro)
        return []

    def add_documents(
        self,
        contents: Iterable[SupportedModalities],
        metadata: Optional[Iterable[Dict[str, Any]]] = None,
        modality: str = "text",
        collection: Optional[str] = None,
    ) -> List[List[str]]:
        col = collection or self.default_collection
        meta_iter = metadata or (None for _ in contents)
        ids: List[List[str]] = []
        for content, meta in zip(contents, meta_iter):
            ids.append(self.add_document(content, meta, modality, col))
        return ids

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
        collection: Optional[str] = None,
    ) -> List[List[str]]:
        col = collection or self.default_collection
        meta_iter = metadatas or (None for _ in texts)
        ids: List[List[str]] = []
        for txt, meta in zip(texts, meta_iter):
            ids.append(self.add_document(txt, meta, modality="text", collection=col))
        return ids

    def delete_collection(self, collection_name: str) -> None:
        try:
            self.client.delete_collection(name=collection_name)
        except Exception as e:
            self._logger.warning(f"Failed to delete collection {collection_name}: {e}")

    def list_collections(self) -> List[str]:
        collections = self.client.list_collections()
        return [c.name for c in collections]

    def get_collection_info(self, collection_name: str) -> Dict[str, Any]:
        try:
            coll = self.client.get_collection(name=collection_name)
            return {"name": coll.name, "count": coll.count()}
        except Exception as e:
            return {"error": str(e)}

    # ---------------------------------------------------------------------
    # Optional synchronous wrapper for legacy callers of ``add_text_docs``
    # ---------------------------------------------------------------------
    def add_text_docs_sync(
        self,
        texts: List[str],
        doc_source: str = "user",
        pbar: Optional[str] = None,
    ) -> None:
        """Run ``add_text_docs`` synchronously when no event loop is active.
        """
        try:
            asyncio.get_running_loop()
            raise RuntimeError(
                "add_text_docs_sync cannot be used inside an async context – use the async 'await kb.add_text_docs(...)' instead."
            )
        except RuntimeError:
            asyncio.run(self.add_text_docs(texts, doc_source=doc_source, pbar=pbar))

    # ---------------------------------------------------------------------
    # Convenience helpers for debugging / tests
    # ---------------------------------------------------------------------
    def list_modalities(self) -> Dict[str, int]:
        """Return a dict ``{modality: count}`` summarising what is stored.
        Useful in tests to verify that each modality was ingested.
        """
        stats = self.inventory_stats()
        return {k: v.get("n_chunks", 0) for k, v in stats.get("modalities", {}).items()}

    def get_stats(self) -> Dict[str, Any]:
        inventory_stats = self.inventory_stats()
        vstore_stats = self.store.get_stats()
        return {
            **inventory_stats,
            **vstore_stats,
            "name": self.name
        }
