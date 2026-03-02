import asyncio
import os, glob, json, csv, pdfplumber, nbformat
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from alive_progress import alive_bar
import chromadb
from chromadb.config import Settings


class VectorStore:
    """An enhanced asynchronous vector store using ChromaDB for document management.
    
    Features:
    - File-based document loading with line number tracking
    - Raw text document support (for memory manager compatibility)
    - Recursive directory uploads
    - Context window support for query results
    - URL document loading (HTML)
    - Purging and document removal
    - Multiple embedding model support
    """

    def __init__(self, params: Dict[str, Any]):
        """Initialize the VectorStore with configuration parameters."""
        self.embedding_model = params.get("embedding_model", "all-MiniLM-L6-v2")
        self.chunk_size = params.get("chunk_size", 512)
        self.chunk_overlap = params.get("chunk_overlap", 64)
        self.collection_name = params.get("collection_name", "default_collection")
        self.persist_directory = params.get("persist_directory", "./chroma_db")
        self.rebuild_vstore = params.get("rebuild_vstore", False)

        # Supported file extensions
        self.supported_extensions = {
            "py", "js", "ts", "md", "html", "txt", "dat", "pdf", "csv", "json",
            "log", "info", "c", "cpp", "f", "f77", "f90", "f95", "ipynb",
        }

        # Load custom extensions if available
        self._load_custom_extensions()

        # Initialize ChromaDB client
        self.client = chromadb.Client(
            Settings(persist_directory=self.persist_directory, anonymized_telemetry=False)
        )

        # Handle rebuild flag
        if self.rebuild_vstore:
            try:
                self.client.delete_collection(name=self.collection_name)
            except:
                pass

        # Create or get collection
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name, metadata={"hnsw_space": "cosine"}
        )

    def _load_custom_extensions(self):
        """Load custom file extensions from custom_list_ascii_file_extensions.txt"""
        try:
            custom_file = Path("./custom_list_ascii_file_extensions.txt")
            if custom_file.exists():
                for line in custom_file.read_text().splitlines():
                    ext = line.strip().lower().lstrip(".")
                    if ext and ext not in self.supported_extensions:
                        self.supported_extensions.add(ext)
        except Exception as e:
            print(f"[VSTORE][WARN] Could not read extra ASCII extension list: {e}")

    # ----------------------------
    # Text Splitting with Line Numbers
    # ----------------------------
    async def _split_text_with_lines(
        self, text: str
    ) -> List[Tuple[str, int, int]]:
        """Split text into chunks with overlap, tracking line number ranges.

        Returns:
            List of (chunk_text, line_start, line_end)
        """
        lines = text.splitlines()
        chunks = []
        current = []
        char_count = 0
        start_line = 0

        for i, line in enumerate(lines):
            if char_count + len(line) > self.chunk_size and current:
                chunks.append(("\n".join(current), start_line, i))
                # Overlap: keep last few lines
                overlap_lines = min(self.chunk_overlap // 50, len(current))
                current = current[-overlap_lines:] if overlap_lines > 0 else []
                start_line = i - len(current)
                char_count = sum(len(l) for l in current)

            current.append(line)
            char_count += len(line)

        if current:
            chunks.append(("\n".join(current), start_line, len(lines)))

        return chunks

    # ----------------------------
    # File Reading (async wrapper)
    # ----------------------------
    async def _read_file_content(self, file_path: Path) -> str:
        return await asyncio.to_thread(self._read_file_content_sync, file_path)

    def _read_file_content_sync(self, file_path: Path) -> str:
        try:
            ext = file_path.suffix.lower().strip(".")
            if ext == "pdf":
                content = ""
                with pdfplumber.open(file_path) as pdf:
                    for page in pdf.pages:
                        content += page.extract_text() or ""
                return content
            elif ext == "json":
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.dumps(json.load(f), indent=2)
            elif ext == "csv":
                with open(file_path, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    return "\n".join([",".join(row) for row in reader])
            elif ext == "ipynb":
                with open(file_path, "r", encoding="utf-8") as f:
                    nb = nbformat.read(f, as_version=4)
                return "\n".join(
                    [cell["source"] for cell in nb.cells if cell["cell_type"] == "code"]
                )
            elif ext in self.supported_extensions:
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read()
            else:
                return f"[Unsupported file type: {ext}]"
        except Exception as e:
            return f"[Error reading {file_path}: {str(e)}]"

    # ----------------------------
    # File Processing
    # ----------------------------
    async def _process_file(
        self, file_path: Path
    ) -> Tuple[str, List[Tuple[str, int, int]]]:
        content = await self._read_file_content(file_path)
        chunks = await self._split_text_with_lines(content)
        return str(file_path), chunks

    # ----------------------------
    # Raw Text Document Support (for memory manager)
    # ----------------------------
    async def add_text_docs(
        self,
        texts: List[str],
        doc_source: str = "user",
        pbar: Optional[str] = "filling",
        pbar_title: str = "[@] Adding text documents",
        pbar_length: int = 20,
        pbar_spinner: str = "wait",
    ) -> None:
        """Add raw text documents to the vector store.
        This method is required for memory manager compatibility.
        
        Args:
            texts: List of text strings to add
            doc_source: Source identifier for metadata
            pbar: Progress bar style (None to disable)
        """
        if not texts:
            return

        all_ids, all_docs, all_metas = [], [], []
        
        async def process_text(idx: int, text: str):
            chunks = await self._split_text_with_lines(text)
            for chunk_idx, (chunk, line_start, line_end) in enumerate(chunks):
                doc_id = f"{doc_source}_text_{idx}_chunk_{chunk_idx}"
                all_ids.append(doc_id)
                all_docs.append(chunk)
                all_metas.append({
                    "source": doc_source,
                    "text_id": idx,
                    "chunk_id": chunk_idx,
                    "line_start": line_start,
                    "line_end": line_end,
                })

        tasks = [process_text(i, text) for i, text in enumerate(texts)]
        
        if pbar:
            with alive_bar(
                len(tasks), bar=pbar, title=pbar_title, length=pbar_length, spinner=pbar_spinner
            ) as bar:
                for task in asyncio.as_completed(tasks):
                    await task
                    bar()
        else:
            await asyncio.gather(*tasks)

        if all_ids:
            self.collection.add(ids=all_ids, documents=all_docs, metadatas=all_metas)

    # ----------------------------
    # Document Management (Files)
    # ----------------------------
    async def add_documents(
        self,
        documents: List[str],
        pbar: Optional[str] = "filling",
        pbar_title: str = "[@] Adding documents to vstore",
        pbar_length: int = 20,
        pbar_spinner: str = "wait",
    ) -> None:
        """Add file-based documents to the vector store.
        Supports both file paths and raw text strings for backward compatibility.
        """
        if not documents:
            return

        # Detect if documents are file paths or raw text
        file_docs = []
        text_docs = []
        
        for doc in documents:
            doc_str = str(doc)
            # Only check Path.exists() if string is reasonably short and doesn't look like JSON/dict
            is_potential_path = (
                len(doc_str) < 4096 and 
                not doc_str.startswith(('{', '[')) and
                not doc_str.startswith("{'")
            )
            
            if is_potential_path:
                try:
                    doc_path = Path(doc_str)
                    if doc_path.exists() and doc_path.is_file():
                        file_docs.append(doc_path)
                    else:
                        text_docs.append(doc_str)
                except (OSError, ValueError):
                    # Path() failed, treat as text content
                    text_docs.append(doc_str)
            else:
                text_docs.append(doc_str)

        # Process file documents
        if file_docs:
            tasks = [self._process_file(fp) for fp in file_docs]
            results = await asyncio.gather(*tasks)

            all_ids, all_docs, all_metas = [], [], []
            for file_path, chunks in results:
                for i, (chunk, line_start, line_end) in enumerate(chunks):
                    all_ids.append(f"{file_path}_chunk_{i}")
                    all_docs.append(chunk)
                    all_metas.append(
                        {
                            "source": file_path,
                            "chunk_id": i,
                            "line_start": line_start,
                            "line_end": line_end,
                        }
                    )

            if all_ids:
                self.collection.add(ids=all_ids, documents=all_docs, metadatas=all_metas)

        # Process text documents
        if text_docs:
            await self.add_text_docs(text_docs, doc_source="raw_text", pbar=None)

    async def remove_documents(
        self,
        file_paths: List[str],
        pbar: Optional[str] = "halloween",
        pbar_title: str = "[@] Removing documents from vstore",
        pbar_length: int = 20,
        pbar_spinner: str = "wait",
    ) -> None:
        if not file_paths:
            return

        if pbar:
            with alive_bar(
                len(file_paths), bar=pbar, title=pbar_title, length=pbar_length, spinner=pbar_spinner
            ) as bar:
                for path in file_paths:
                    results = self.collection.get(where={"source": path})
                    if results["ids"]:
                        self.collection.delete(ids=results["ids"])
                    bar()
        else:
            for path in file_paths:
                results = self.collection.get(where={"source": path})
                if results["ids"]:
                    self.collection.delete(ids=results["ids"])

    # ----------------------------
    # URL Document Loading (HTML)
    # ----------------------------
    async def add_url_doc(self, url: str) -> None:
        """Add a single HTML document from URL."""
        try:
            import requests
            from bs4 import BeautifulSoup
            
            response = await asyncio.to_thread(requests.get, url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            text = soup.get_text(separator='\n', strip=True)
            
            await self.add_text_docs([text], doc_source=url, pbar=None)
        except Exception as e:
            print(f"[VSTORE] Failed to load URL {url}: {e}")

    async def add_url_docs(
        self,
        urls: List[str],
        pbar: str = "filling",
        pbar_title: str = "[@] Loading HTML docs",
        pbar_length: int = 20,
        pbar_spinner: str = "wait",
    ) -> None:
        """Add multiple HTML documents from URLs."""
        if not urls:
            return

        with alive_bar(
            len(urls), bar=pbar, title=pbar_title, length=pbar_length, spinner=pbar_spinner
        ) as bar:
            for url in urls:
                await self.add_url_doc(url)
                bar()

    # ----------------------------
    # Query with Context Window
    # ----------------------------
    async def query(
        self, query: str, n_results: int = 5, context_window: int = 1, filter: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """Query with optional metadata filtering and context window support."""
        query_kwargs = {"query_texts": [query], "n_results": n_results}
        if filter:
            query_kwargs["where"] = filter

        results = self.collection.query(**query_kwargs)

        output = []
        for doc, meta, score in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        ):
            file_path = meta.get("source", "unknown")
            chunk_id = meta.get("chunk_id", 0)

            # Get neighboring chunks for context
            neighbor_ids = [
                f"{file_path}_chunk_{i}"
                for i in range(chunk_id - context_window, chunk_id + context_window + 1)
                if i >= 0
            ]
            neighbors = self.collection.get(ids=neighbor_ids)

            context_text = (
                "\n".join(neighbors["documents"])
                if neighbors and neighbors["documents"]
                else doc
            )

            output.append(
                {
                    "document": doc,
                    "source": file_path,
                    "chunk_id": chunk_id,
                    "line_start": meta.get("line_start"),
                    "line_end": meta.get("line_end"),
                    "score": score,
                    "context": context_text,
                    "metadata": meta,
                }
            )

        return output

    def query_search(
        self,
        query: str,
        k: int = 4,
        filter: Optional[Dict] = None,
        with_score: bool = True,
        max_score: Optional[float] = 0.95,
    ) -> List[Dict[str, Any]]:
        """Synchronous query with score filtering (for backward compatibility)."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Create a new event loop in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self.query(query, n_results=k, filter=filter, context_window=0)
                )
                results = future.result()
        else:
            results = loop.run_until_complete(
                self.query(query, n_results=k, filter=filter, context_window=0)
            )

        # Apply score filtering
        filtered = []
        for r in results:
            if with_score:
                score = r.get("score", 0.0)
                if max_score is None or max_score >= 1.0 or score <= max_score:
                    filtered.append(r)
            else:
                filtered.append({"document": r["document"], "metadata": r["metadata"]})

        return filtered

    # ----------------------------
    # Recursive Directory Upload
    # ----------------------------
    async def recursive_upload(
        self,
        directory: str,
        extensions: Optional[List[str]] = None,
        pbar: str = "filling",
        pbar_title: str = "[@] Uploading Files to vstore",
        pbar_length: int = 20,
        pbar_spinner: str = "wait",
    ) -> None:
        """Recursively upload all supported files from directory."""
        if extensions is None:
            extensions = list(self.supported_extensions)

        ext_set = set(ext.lower().strip(".") for ext in extensions)
        file_paths = []

        patterns = [f"**/*.{ext}" for ext in ext_set]
        for pattern in patterns:
            file_paths.extend(glob.glob(os.path.join(directory, pattern), recursive=True))

        # Deduplicate by converting to absolute paths
        valid_files = list({Path(p).resolve() for p in file_paths if Path(p).suffix.lower().strip(".") in ext_set})

        if not valid_files:
            print(f"No supported files found in {directory}")
            return

        print(f"Found {len(valid_files)} files to upload.")

        tasks = [self._process_file(f) for f in valid_files]
        results = await asyncio.gather(*tasks)

        all_ids, all_docs, all_metas = [], [], []
        if pbar:
            with alive_bar(
                len(results), bar=pbar, title=pbar_title, length=pbar_length, spinner=pbar_spinner
            ) as bar:
                for file_path, chunks in results:
                    for i, (chunk, line_start, line_end) in enumerate(chunks):
                        all_ids.append(f"{file_path}_chunk_{i}")
                        all_docs.append(chunk)
                        all_metas.append(
                            {
                                "source": str(file_path),
                                "chunk_id": i,
                                "line_start": line_start,
                                "line_end": line_end,
                            }
                        )
                    bar()
        else:
            for file_path, chunks in results:
                for i, (chunk, line_start, line_end) in enumerate(chunks):
                    all_ids.append(f"{file_path}_chunk_{i}")
                    all_docs.append(chunk)
                    all_metas.append(
                        {
                            "source": str(file_path),
                            "chunk_id": i,
                            "line_start": line_start,
                            "line_end": line_end,
                        }
                    )

        if all_ids:
            self.collection.add(ids=all_ids, documents=all_docs, metadatas=all_metas)

    # Alias for backward compatibility
    async def upload_dir(self, *args, **kwargs):
        return await self.recursive_upload(*args, **kwargs)

    # ----------------------------
    # Document Retrieval by ID
    # ----------------------------
    def get_document_by_id(self, document_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a document by its ID."""
        result = self.collection.get(ids=[document_id])
        if not result or not result.get("ids"):
            return None

        ids = result.get("ids", [])
        docs = result.get("documents", [])
        metas = result.get("metadatas", [])

        for idx, did in enumerate(ids):
            if did == document_id:
                return {
                    "id": did,
                    "page_content": docs[idx],
                    "metadata": metas[idx],
                }
        return None

    # ----------------------------
    # Purge and Close
    # ----------------------------
    async def purge(self) -> None:
        """Purge the entire vector store."""
        print("Purging the vector store...")
        self.client.delete_collection(self.collection_name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name, metadata={"hnsw_space": "cosine"}
        )
        print("Vector store purged.")

    async def close(self) -> None:
        """Chroma client does not need explicit close."""
        pass

    # ----------------------------
    # Utility Methods
    # ----------------------------
    def update_doc_count(self) -> int:
        """Get current document count."""
        result = self.collection.get()
        return len(result.get("ids", []))

    def get_stats(self) -> Dict[str, Any]:
        """Get vector store statistics."""
        result = self.collection.get()
        ids = result.get("ids", [])
        metas = result.get("metadatas", [])
        
        sources = set(m.get("source", "unknown") for m in metas)
        
        return {
            "total_chunks": len(ids),
            "unique_sources": len(sources),
            "collection_name": self.collection_name,
            "persist_directory": self.persist_directory,
        }