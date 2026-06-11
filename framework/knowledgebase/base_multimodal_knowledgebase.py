from __future__ import annotations

import pathlib
import asyncio
import re
import concurrent.futures
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Union

# ---------------------------------------------------------------------------
# Core imports
# ---------------------------------------------------------------------------
import chromadb
from chromadb.config import Settings
from framework.data_store.data_models import MultimodalEmbeddingParams, MultimodalVectorStoreParams
from framework.data_store.multimodal_vstore import MultimodalVectorStore
from framework.knowledgebase.data_models import MultimodalKnowledgeBaseParams

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
        is_visual = any(re.search(r"\\b" + kw + r"\\b", query, re.IGNORECASE) for kw in visual_keywords)

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
        return self.store.get_stats().get("modalities", {})

    def get_stats(self) -> Dict[str, Any]:
        return self.store.get_stats()
