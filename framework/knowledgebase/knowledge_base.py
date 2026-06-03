# framework/knowledgebase/knowledge_base.py
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from datetime import datetime, timezone
from rich.console import Console

# Import the VectorStore wrapper
import chromadb
from chromadb.config import Settings
from framework.data_store.data_models import EmbeddingParams, VectorStoreParams
from framework.data_store.vstore import VectorStore
from framework.knowledgebase.data_models import KnowledgeBaseParams

console = Console()


# --------------------------
# Inventory (SQLite) schema
# --------------------------

INVENTORY_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT,
    doc_type   TEXT,
    v_ids_json TEXT,    -- JSON array of vector IDs associated with this source
    n_chunks   INTEGER,
    n_tokens   INTEGER,
    added_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_source_path ON documents(source_path);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    v_id       TEXT,      -- vector id returned by the vstore
    source_path TEXT,
    line_start INTEGER,   -- line number where chunk starts
    line_end   INTEGER,   -- line number where chunk ends
    position   INTEGER,   -- position/order within the source
    n_tokens   INTEGER,
    metadata_json TEXT,
    added_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_v_id ON chunks(v_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_path);
"""

def _now_iso() -> str:
    # timezone-aware UTC, RFC3339-ish with trailing Z
    dt = datetime.now(timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def _count_tokens(text: str) -> int:
    # Conservative token proxy (whitespace split); replace with tiktoken if available.
    return len(str(text).split())

'''
@dataclass
class KBParams:
    """Configuration for KnowledgeBase."""

    name: str
    # Where the vstore persists its data
    vstore_params: Dict[str, Any]
    # Where to persist the inventory (SQLite file). Defaults under vstore persist dir.
    inventory_path: Optional[str] = None
    # Verbosity
    vrbz: int = 0

#kbparams_type = NewType('kbparams_type', Type[KBParams])
'''

@dataclass
class IngestResult:
    source_path: str
    doc_type: str
    n_chunks: int
    n_tokens: int
    v_ids: List[str] = field(default_factory=list)

class KnowledgeBase:
    """
    KnowledgeBase ties together a VectorStore (vector DB) with a lightweight
    SQLite "inventory" to track sources, chunk ids, line numbers, and token counts.
    Provides sync and async APIs mirroring the underlying VectorStore operations
    plus inventory/lookup helpers.
    
    Enhanced features from vstore_v1:
    - Line number tracking for all chunks
    - Context window support in queries
    - Raw text document support
    - URL document loading
    - Custom file extension support
    - Score filtering in searches
    - Document retrieval by ID
    """

    def __init__(self, params: KnowledgeBaseParams, db_client: chromadb.Client):
        self.params = params
        self.name = params.name
        self.VRBZ = int(params.vrbz)
        # Initialize VectorStore
        vs_params = VectorStoreParams(collection_name=f"kb_{params.name}_collection",
                                      chunk_size = params.chunk_size,
                                      chunk_overlap = params.chunk_overlap,
                                      embedding= params.text_embedding,
                                      rebuild_vstore = params.rebuild_text_vstore,
                                      vs_VRBZ = params.vrbz)
        self.vstore = VectorStore(vs_params, client=db_client)

        # Inventory location
        inv_path = params.inventory_path or os.path.join(
            self.vstore.persist_directory, f"{self.name}_inventory.sqlite"
        )
        self.inventory_path = inv_path
        Path(os.path.dirname(inv_path)).mkdir(parents=True, exist_ok=True)

        # Ensure inventory schema
        self._init_inventory()

    # --------------------------
    # Inventory helpers
    # --------------------------

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.inventory_path, isolation_level=None)

    def _init_inventory(self) -> None:
        with self._connect() as cx:
            cx.executescript(INVENTORY_SCHEMA)

    def _record_document(self, source_path: str, doc_type: str, results: List[Dict[str, Any]]) -> IngestResult:
        """
        Record a document in the inventory after it's been added to the vector store.
        Uses the query results from vstore which include line_start, line_end, and chunk_id.
        """
        n_chunks = len(results)
        n_tokens = sum(_count_tokens(r.get("document", "")) for r in results)
        v_ids = [r.get("id", f"{source_path}_chunk_{r.get('chunk_id', i)}") for i, r in enumerate(results)]
        added_at = _now_iso()

        with self._connect() as cx:
            cx.execute(
                "INSERT INTO documents (source_path, doc_type, v_ids_json, n_chunks, n_tokens, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                (source_path, doc_type, json.dumps(v_ids), n_chunks, n_tokens, added_at),
            )
            # Store chunk-level info with line numbers
            for result in results:
                meta = result.get("metadata", {})
                v_id = result.get("id", f"{source_path}_chunk_{meta.get('chunk_id', 0)}")
                line_start = meta.get("line_start", 0)
                line_end = meta.get("line_end", 0)
                chunk_id = meta.get("chunk_id", 0)
                content = result.get("document", "")
                
                cx.execute(
                    "INSERT INTO chunks (v_id, source_path, line_start, line_end, position, n_tokens, metadata_json, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (v_id, source_path, line_start, line_end, chunk_id, _count_tokens(content), json.dumps(meta), added_at),
                )

        return IngestResult(source_path=source_path, doc_type=doc_type, n_chunks=n_chunks, n_tokens=n_tokens, v_ids=v_ids)

    def inventory_stats(self) -> Dict[str, int]:
        """Get statistics about the knowledge base inventory."""
        with self._connect() as cx:
            cur = cx.execute("SELECT COUNT(*), COALESCE(SUM(n_chunks),0), COALESCE(SUM(n_tokens),0) FROM documents")
            n_docs, n_chunks, n_tokens = cur.fetchone()
        return {"n_sources": int(n_docs), "n_chunks": int(n_chunks), "n_tokens": int(n_tokens)}

    def list_sources(self) -> List[Dict[str, Any]]:
        """List all sources in the knowledge base."""
        with self._connect() as cx:
            cur = cx.execute("SELECT source_path, doc_type, n_chunks, n_tokens, added_at FROM documents ORDER BY added_at DESC")
            return [
                {"source_path": r[0], "doc_type": r[1], "n_chunks": r[2], "n_tokens": r[3], "added_at": r[4]}
                for r in cur.fetchall()
            ]

    def get_chunks_for_source(self, source_path: str) -> List[Dict[str, Any]]:
        """Get all chunks for a specific source."""
        with self._connect() as cx:
            cur = cx.execute(
                "SELECT v_id, line_start, line_end, position, n_tokens, metadata_json FROM chunks WHERE source_path=? ORDER BY position ASC",
                (source_path,),
            )
            rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for v_id, line_start, line_end, position, n_tokens, meta_json in rows:
            try:
                meta = json.loads(meta_json) if meta_json else {}
            except Exception:
                meta = {}
            out.append(
                {"v_id": v_id, "line_start": line_start, "line_end": line_end, "position": position, "n_tokens": n_tokens, "metadata": meta}
            )
        return out

    # --------------------------
    # Ingestion APIs (sync)
    # --------------------------

    def add_text_docs(self, texts: Sequence[str], doc_source: str = "kb") -> IngestResult:
        """Add raw text documents to the knowledge base."""
        # Add to vstore synchronously (it will handle chunking)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.vstore.add_text_docs(texts, doc_source=doc_source, pbar=None))
        finally:
            loop.close()
        
        # Query back to get the chunk information for inventory
        results = self.vstore.query_search(query=doc_source, k=len(texts) * 10, with_score=False)
        filtered_results = [r for r in results if r.get("metadata", {}).get("source") == doc_source]
        
        return self._record_document(source_path=f"text://{doc_source}", doc_type="text", results=filtered_results)

    def add_supported_doc(self, path: str, doc_type: Optional[str] = None) -> IngestResult:
        """Add a single supported document to the knowledge base."""
        if doc_type is None:
            doc_type = Path(path).suffix.lstrip(".").lower()
        
        # Add to vstore synchronously
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.vstore.add_documents([path], pbar=None))
        finally:
            loop.close()
        
        # Query back to get the chunk information for inventory
        results = self.vstore.query_search(query=str(path), k=1000, with_score=False)
        filtered_results = [r for r in results if r.get("metadata", {}).get("source") == str(path)]
        
        return self._record_document(source_path=str(path), doc_type=doc_type, results=filtered_results)

    def add_supported_docs(self, paths: Sequence[str]) -> List[IngestResult]:
        """Add multiple supported documents to the knowledge base."""
        results: List[IngestResult] = []
        for p in paths:
            try:
                results.append(self.add_supported_doc(p))
            except Exception as e:
                console.print(f"[!][KB] Failed to add {p}: {e}")
        return results

    def upload_dir(self, dir_path: str, target_file_ext: Sequence[str] | None = None) -> List[IngestResult]:
        """Upload all supported files from a directory."""
        target_file_ext = target_file_ext or list(self.vstore.supported_extensions)
        
        # Use vstore's recursive upload
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.vstore.recursive_upload(dir_path, extensions=target_file_ext))
        finally:
            loop.close()
        
        # Now we need to catalog what was added
        # Get all files that were added
        import glob
        ext_set = set(e.lower().strip(".") for e in target_file_ext)
        file_paths = []
        patterns = [f"**/*.{ext}" for ext in ext_set]
        for pattern in patterns:
            file_paths.extend(glob.glob(os.path.join(dir_path, pattern), recursive=True))
        
        valid_files = list({Path(p).resolve() for p in file_paths if Path(p).suffix.lower().strip(".") in ext_set})
        
        # Record each file in inventory
        results = []
        for file_path in valid_files:
            try:
                doc_type = Path(file_path).suffix.lstrip(".").lower()
                # Query back to get the chunk information
                query_results = self.vstore.query_search(query=str(file_path), k=1000, with_score=False)
                filtered_results = [r for r in query_results if r.get("metadata", {}).get("source") == str(file_path)]
                if filtered_results:
                    results.append(self._record_document(source_path=str(file_path), doc_type=doc_type, results=filtered_results))
            except Exception as e:
                console.print(f"[!][KB] Failed to record {file_path}: {e}")
        
        return results

    # --------------------------
    # URL document loading
    # --------------------------

    def add_url_doc(self, url: str) -> IngestResult:
        """Add a single HTML document from URL."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.vstore.add_url_doc(url))
        finally:
            loop.close()
        
        # Query back to get the chunk information
        results = self.vstore.query_search(query=url, k=1000, with_score=False)
        filtered_results = [r for r in results if r.get("metadata", {}).get("source") == url]
        
        return self._record_document(source_path=url, doc_type="html", results=filtered_results)

    def add_url_docs(self, urls: Sequence[str]) -> List[IngestResult]:
        """Add multiple HTML documents from URLs."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.vstore.add_url_docs(list(urls)))
        finally:
            loop.close()
        
        results = []
        for url in urls:
            try:
                query_results = self.vstore.query_search(query=url, k=1000, with_score=False)
                filtered_results = [r for r in query_results if r.get("metadata", {}).get("source") == url]
                if filtered_results:
                    results.append(self._record_document(source_path=url, doc_type="html", results=filtered_results))
            except Exception as e:
                console.print(f"[!][KB] Failed to record {url}: {e}")
        
        return results

    # --------------------------
    # Ingestion APIs (async)
    # --------------------------

    async def aadd_text_docs(self, texts: Sequence[str], doc_source: str = "kb") -> IngestResult:
        """Async add raw text documents to the knowledge base."""
        await self.vstore.add_text_docs(texts, doc_source=doc_source, pbar=None)
        
        # Query back to get the chunk information for inventory
        results = await self.vstore.query(query=doc_source, n_results=len(texts) * 10)
        filtered_results = [r for r in results if r.get("metadata", {}).get("source") == doc_source]
        
        return self._record_document(source_path=f"text://{doc_source}", doc_type="text", results=filtered_results)

    async def aadd_supported_doc(self, path: str, doc_type: Optional[str] = None) -> IngestResult:
        """Async add a single supported document to the knowledge base."""
        if doc_type is None:
            doc_type = Path(path).suffix.lstrip(".").lower()
        
        await self.vstore.add_documents([path], pbar=None)
        
        # Query back to get the chunk information for inventory
        results = await self.vstore.query(query=str(path), n_results=1000)
        filtered_results = [r for r in results if r.get("metadata", {}).get("source") == str(path)]
        
        return self._record_document(source_path=str(path), doc_type=doc_type, results=filtered_results)

    async def aadd_supported_docs(self, paths: Sequence[str], concurrency: int = 8) -> List[IngestResult]:
        """Async add multiple supported documents to the knowledge base."""
        sem = asyncio.Semaphore(concurrency)
        results: List[IngestResult] = []

        async def worker(p: str):
            async with sem:
                try:
                    res = await self.aadd_supported_doc(p)
                    results.append(res)
                except Exception as e:
                    console.print(f"[!][KB] Failed to add {p}: {e}")

        tasks = [asyncio.create_task(worker(p)) for p in paths]
        await asyncio.gather(*tasks)
        return results

    async def aupload_dir(self, dir_path: str, target_file_ext: Sequence[str] | None = None) -> List[IngestResult]:
        """Async upload all supported files from a directory."""
        target_file_ext = target_file_ext or list(self.vstore.supported_extensions)
        
        await self.vstore.recursive_upload(dir_path, extensions=target_file_ext)
        
        # Catalog what was added
        import glob
        ext_set = set(e.lower().strip(".") for e in target_file_ext)
        file_paths = []
        patterns = [f"**/*.{ext}" for ext in ext_set]
        for pattern in patterns:
            file_paths.extend(glob.glob(os.path.join(dir_path, pattern), recursive=True))
        
        valid_files = list({Path(p).resolve() for p in file_paths if Path(p).suffix.lower().strip(".") in ext_set})
        
        results = []
        for file_path in valid_files:
            try:
                doc_type = Path(file_path).suffix.lstrip(".").lower()
                query_results = await self.vstore.query(query=str(file_path), n_results=1000)
                filtered_results = [r for r in query_results if r.get("metadata", {}).get("source") == str(file_path)]
                if filtered_results:
                    results.append(self._record_document(source_path=str(file_path), doc_type=doc_type, results=filtered_results))
            except Exception as e:
                console.print(f"[!][KB] Failed to record {file_path}: {e}")
        
        return results

    async def aadd_url_doc(self, url: str) -> IngestResult:
        """Async add a single HTML document from URL."""
        await self.vstore.add_url_doc(url)
        
        results = await self.vstore.query(query=url, n_results=1000)
        filtered_results = [r for r in results if r.get("metadata", {}).get("source") == url]
        
        return self._record_document(source_path=url, doc_type="html", results=filtered_results)

    async def aadd_url_docs(self, urls: Sequence[str]) -> List[IngestResult]:
        """Async add multiple HTML documents from URLs."""
        await self.vstore.add_url_docs(list(urls))
        
        results = []
        for url in urls:
            try:
                query_results = await self.vstore.query(query=url, n_results=1000)
                filtered_results = [r for r in query_results if r.get("metadata", {}).get("source") == url]
                if filtered_results:
                    results.append(self._record_document(source_path=url, doc_type="html", results=filtered_results))
            except Exception as e:
                console.print(f"[!][KB] Failed to record {url}: {e}")
        
        return results

    # --------------------------
    # Purge / Maintenance
    # --------------------------
    def _purge_inventory(self) -> None:
        """
        Clear the inventory tables. If the SQLite file was deleted by vstore purge,
        recreate an empty DB with the proper schema.
        """
        db_file = self.inventory_path

        # If DB file vanished (vstore purge cleared the persist dir), recreate schema.
        if not os.path.exists(db_file):
            Path(os.path.dirname(db_file)).mkdir(parents=True, exist_ok=True)
            self._init_inventory()
            return

        try:
            with self._connect() as cx:
                cx.execute("DELETE FROM chunks")
                cx.execute("DELETE FROM documents")
        except sqlite3.OperationalError:
            # Tables might not exist (brand new or wiped DB) — ensure schema exists.
            self._init_inventory()

    def purge(self) -> None:
        """Purge both the VectorStore collection and the inventory database contents."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.vstore.purge())
        finally:
            loop.close()
        self._purge_inventory()

    async def apurge(self) -> None:
        """Async purge of both the VectorStore and the inventory database contents."""
        await self.vstore.purge()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._purge_inventory)

    # --------------------------
    # Retrieval with enhanced features
    # --------------------------

    def search(
        self, 
        query: str, 
        k: int = 5, 
        with_score: bool = True, 
        max_score: float | None = 0.95,
        context_window: int = 1,
        filter: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """Search with context window and score filtering."""
        if context_window > 0:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                results = loop.run_until_complete(
                    self.vstore.query(query=query, n_results=k, context_window=context_window, filter=filter)
                )
            finally:
                loop.close()
        else:
            results = self.vstore.query_search(query=query, k=k, with_score=with_score, max_score=max_score, filter=filter)
        
        return results

    async def asearch(
        self, 
        query: str, 
        k: int = 5, 
        with_score: bool = True, 
        max_score: float | None = 0.95,
        context_window: int = 1,
        filter: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """Async search with context window and score filtering."""
        return await self.vstore.query(query=query, n_results=k, context_window=context_window, filter=filter)

    def search_by_source(self, source_path: str) -> List[Dict[str, Any]]:
        """Return chunk entries (with vector ids) recorded for a given source path."""
        return self.get_chunks_for_source(source_path)

    def get_document_by_id(self, document_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a document by its vector ID."""
        return self.vstore.get_document_by_id(document_id)

    def neighbor_chunks(self, v_id: str, window: int = 2) -> List[Dict[str, Any]]:
        """Return nearby chunks (by position) around a given vector-id within the same source."""
        with self._connect() as cx:
            cur = cx.execute("SELECT source_path, position FROM chunks WHERE v_id=?", (v_id,))
            row = cur.fetchone()
            if not row:
                return []
            source_path, pos = row[0], row[1]
            cur = cx.execute(
                "SELECT v_id, line_start, line_end, position, n_tokens, metadata_json FROM chunks WHERE source_path=? AND position BETWEEN ? AND ? ORDER BY position ASC",
                (source_path, max(0, pos - window), pos + window),
            )
            rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for vid, line_start, line_end, position, n_tokens, meta_json in rows:
            try:
                meta = json.loads(meta_json) if meta_json else {}
            except Exception:
                meta = {}
            out.append({"v_id": vid, "line_start": line_start, "line_end": line_end, "position": position, "n_tokens": n_tokens, "metadata": meta})
        return out

    # --------------------------
    # Utility methods
    # --------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive knowledge base statistics."""
        inventory_stats = self.inventory_stats()
        vstore_stats = self.vstore.get_stats()
        
        return {
            **inventory_stats,
            **vstore_stats,
            "name": self.name,
        }
