import os
import copy
import json
import gc
import asyncio
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

from framework.utils.io_tools import console


class MemoryManager:
    """Manages structured and vector‑enhanced memory for a BaseWorkflow instance.

    Features:
    - In‑memory key‑value storage (facts, user preferences, task state)
    - Summarization and indexing of chat history
    - Vector store for raw chat traces (semantic search)
    - Persistent JSON storage and optional vector store for summaries
    """

    def __init__(
        self,
        memory_path: Optional[str] = None,
        session_dir: Optional[str] = None,
        max_summary_tokens: int = 2000,
        max_ctx_tokens: int = 16000,
        memory_fragment_types: Optional[List[str]] = None,
        traces_vector_store: Any = None,
        summaries_vector_store: Any = None,
    ):
        # Session handling – ensure a directory for isolation
        self.session_dir = session_dir if session_dir else "./"

        # Determine the path of the JSON memory file
        if memory_path is None:
            self.memory_path = os.path.join(self.session_dir, "memory.json")
        else:
            self.memory_path = memory_path

        self.max_summary_tokens = max_summary_tokens
        self.max_ctx_tokens = max_ctx_tokens

        # Types of memory fragments we keep (e.g., "facts", "user_prefs", ...)
        self.memory_fragment_types = (
            memory_fragment_types
            if memory_fragment_types is not None
            else [
                "facts",
                "user_prefs",
                "warnings",
                "strategies",
                "decisions",
                "conclusions",
                "solutions",
                "task_state",
                "summaries",
            ]
        )

        # Initialise containers for each fragment type
        self.memory_fragments: Dict[str, Dict[str, Any]] = {}
        for frag_type in self.memory_fragment_types:
            self.memory_fragments[frag_type] = {}

        # Convenience attributes for commonly accessed categories
        self.facts: Dict[str, Any] = self.memory_fragments.get("facts", {})
        self.user_prefs: Dict[str, Any] = self.memory_fragments.get("user_prefs", {})
        self.task_state: Dict[str, Any] = self.memory_fragments.get("task_state", {})
        self.summaries: List[str] = []

        # Vector stores (optional – may be attached later)
        self._traces_vector_store = traces_vector_store
        self._summaries_vector_store = summaries_vector_store
        self._last_indexed_entry_idx = 0

        # Load any existing persisted state
        self._load()

    # ---------------------------------------------------------------------
    # Helper / public API
    # ---------------------------------------------------------------------
    def set_traces_vector_store(self, traces_vs: Any, verbose: int = 0) -> None:
        """Attach or update the traces vector store."""
        self._traces_vector_store = traces_vs
        if verbose > 0:
            console.print("[MEMORY] Traces vector store attached.")

    def set_summaries_vector_store(self, summaries_vs: Any, verbose: int = 0) -> None:
        """Attach or update the summaries vector store."""
        self._summaries_vector_store = summaries_vs
        if verbose > 0:
            console.print("[MEMORY] Summaries vector store attached.")

    # ---------------------------------------------------------------------
    # Persistence
    # ---------------------------------------------------------------------
    def _load(self, verbose: int = 0) -> None:
        """Load persisted memory from *self.memory_path* if it exists."""
        if self.memory_path and os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.memory_fragment_types = data.get("memory_fragment_types", self.memory_fragment_types)
                self.memory_fragments = data.get("memory_fragments", self.memory_fragments)
                # Update convenience shortcuts
                self.facts = self.memory_fragments.get("facts", {})
                self.user_prefs = self.memory_fragments.get("user_prefs", {})
                self.task_state = self.memory_fragments.get("task_state", {})
                self.summaries = data.get("summaries", self.summaries)
                self._last_indexed_entry_idx = data.get("_last_indexed_entry_idx", 0)
                if verbose > 0:
                    console.print(f"[MEMORY] Loaded memory from {self.memory_path}")
            except Exception as e:
                console.print(f"[MEMORY] Failed to load memory: {e}")

    def _save(self, verbose: int = 0) -> None:
        """Write current in‑memory structures to *self.memory_path* safely."""
        if not self.memory_path:
            return
        try:
            Path(self.memory_path).parent.mkdir(parents=True, exist_ok=True)
            data = {
                "memory_fragment_types": self.memory_fragment_types,
                "memory_fragments": self.memory_fragments,
                "summaries": self.summaries,
                "_last_indexed_entry_idx": self._last_indexed_entry_idx,
            }
            json_str = json.dumps(data, indent=2, ensure_ascii=False)
            Path(self.memory_path).write_text(json_str, encoding="utf-8")
            if verbose > 0:
                console.print(f"[MEMORY] Saved memory to {self.memory_path}")
        except Exception as e:
            console.print(f"[MEMORY] Failed to save memory: {e}")

    # ---------------------------------------------------------------------
    # Basic KV operations
    # ---------------------------------------------------------------------
    def remember(self, key: str, value: Any, category: str = "facts") -> None:
        """Store *value* under *key* inside the given *category*.
        Categories are created on‑the‑fly if they do not exist.
        """
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types:
            self.memory_fragment_types.append(cat)
            self.memory_fragments[cat] = {}
        self.memory_fragments[cat][key] = copy.deepcopy(value)
        # Keep shortcuts up‑to‑date for the three core categories
        if cat == "facts":
            self.facts = self.memory_fragments[cat]
        elif cat == "user_prefs":
            self.user_prefs = self.memory_fragments[cat]
        elif cat == "task_state":
            self.task_state = self.memory_fragments[cat]
        self._save()

    def get_category(self, category: str) -> Dict[str, Any]:
        """Return a deep‑copied view of a top‑level category."""
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types:
            raise ValueError(f"Unknown memory category: {category}")
        return copy.deepcopy(self.memory_fragments.get(cat, {}))

    def recall(self, key: Optional[str] = None, category: str = "facts") -> Any:
        """Retrieve stored data.
        * If *key* is provided, return the value for that key within *category*.
        * If *key* is ``None``, return the full dictionary for the category.
        """
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types:
            raise ValueError(f"Unknown memory category: {category}")
        if key is None:
            return copy.deepcopy(self.memory_fragments.get(cat, {}))
        return copy.deepcopy(self.memory_fragments.get(cat, {}).get(key))

    def forget(self, key: str, category: str = "facts") -> None:
        """Remove *key* from the specified *category*.
        Raises if the category or key does not exist.
        """
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types:
            raise ValueError(f"Unknown memory category: {category}")
        if key in self.memory_fragments.get(cat, {}):
            del self.memory_fragments[cat][key]
            gc.collect()
            self._save()
        else:
            raise ValueError(f"{key} not found in category {category}")

    def clear(self, category: Optional[str] = None) -> None:
        """Clear either a single *category* or all stored fragments.
        When *category* is ``None`` the entire memory is reset while keeping the
        defined fragment types.
        """
        if category is None:
            self.memory_fragments = {ct: {} for ct in self.memory_fragment_types}
            self.facts = self.memory_fragments.get("facts", {})
            self.user_prefs = self.memory_fragments.get("user_prefs", {})
            self.task_state = self.memory_fragments.get("task_state", {})
            self.summaries = []
        else:
            cat = category.strip().lower()
            if cat not in self.memory_fragment_types:
                raise ValueError(f"Unknown memory category: {category}")
            self.memory_fragments[cat] = {}
            if cat == "facts":
                self.facts = {}
            elif cat == "user_prefs":
                self.user_prefs = {}
            elif cat == "task_state":
                self.task_state = {}
            elif cat == "summaries":
                self.summaries = []
        gc.collect()
        self._save()

    # ----------------------------------------------------------
    # Chat‑history indexing and summarization
    # ----------------------------------------------------------
    def process_new_entries(self, new_entries: List[Dict[str, Any]], verbose: int = 0) -> None:
        """Index freshly added chat entries into the traces vector store.
        *new_entries* must be an iterable of dictionaries that contain a
        ``"content"`` key (adjust as needed for your chat schema).
        """
        if not new_entries:
            return
        if self._traces_vector_store:
            entries_text = [str(entry.get("content", "")) for entry in new_entries]
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._traces_vector_store.add_documents(entries_text, pbar=None))
                else:
                    loop.run_until_complete(self._traces_vector_store.add_documents(entries_text, pbar=None))
                if verbose > 0:
                    console.print(f"[MEMORY] Indexed {len(new_entries)} chat entries to traces.")
            except RuntimeError:
                asyncio.run(self._traces_vector_store.add_documents(entries_text, pbar=None))
                if verbose > 0:
                    console.print(f"[MEMORY] Indexed {len(new_entries)} chat entries to traces (run).")
            self._last_indexed_entry_idx += len(new_entries)
            self._save()

    def summarize_recent_chat(
        self,
        lines: List[str],
        from_idx: int,
        to_idx: int,
        summarize_fn: Any,
        verbose: int = 0,
    ) -> None:
        """Create a summary for a slice of *lines* and store it.
        ``summarize_fn`` should accept a single string (the joined segment) and
        return a textual summary.
        """
        segment = "\n".join(lines[from_idx:to_idx])
        try:
            summary = summarize_fn(segment)
        except Exception as e:
            console.print(f"[MEMORY] Summarization failed: {e}")
            summary = "[Summary unavailable]"
        self.summaries.append(summary)
        self._save()
        if self._summaries_vector_store:
            self._index_summary_to_store(summary, verbose)

    def _index_summary_to_store(self, summary: str, verbose: int = 0) -> None:
        """Add a summary document to the summaries vector store (if attached)."""
        try:
            idx = len(self.summaries) - 1
            doc_id = f"summary_{idx}"
            # The vector‑store API expects a list of documents
            self._summaries_vector_store.add_documents([summary], pbar=None)
            if verbose > 0:
                console.print(f"[MEMORY] Indexed summary #{idx} to vector store.")
        except Exception as e:
            console.print(f"[MEMORY] Failed to index summary: {e}")

    # ----------------------------------------------------------
    # Semantic recall
    # ----------------------------------------------------------
    def semantic_recall(
        self,
        query: str,
        category: Optional[str] = None,
        n_results: int = 3,
        source: str = "traces",
        verbose: int = 0,
    ) -> List[Dict[str, Any]]:
        """Recall memory semantically via the specified vector store.
        *source* can be ``"traces"`` or ``"summaries"``.
        """
        vs = self._traces_vector_store if source == "traces" else self._summaries_vector_store
        if vs is None:
            if verbose > 0:
                console.print(f"[MEMORY] No {source} vector store attached. Falling back to empty result.")
            return []
        full_query = query + (f" {category}" if category else "")
        try:
            results = vs.query(query=full_query, n_results=n_results)
            return results
        except Exception as e:
            console.print(f"[MEMORY] Semantic recall ({source}) failed: {e}")
            return []

    # ----------------------------------------------------------
    # Prompt contextualisation helper
    # ----------------------------------------------------------
    def contextualize(self, prompt: str) -> str:
        """Inject memory context into *prompt*.
        The method builds a readable block containing facts, preferences,
        task state and any stored summaries.
        """
        parts: List[str] = []
        if self.facts:
            parts.append("--- Facts ---")
            parts.extend([f"{k}: {v}" for k, v in self.facts.items()])
        if self.user_prefs:
            parts.append("--- User Preferences ---")
            parts.extend([f"{k}: {v}" for k, v in self.user_prefs.items()])
        if self.task_state:
            parts.append("--- Task State ---")
            parts.extend([f"{k}: {v}" for k, v in self.task_state.items()])
        if self.summaries:
            parts.append("--- History Summaries ---")
            parts.extend(self.summaries)
        if parts:
            return prompt + "\n\n[MEMORY CONTEXT]\n" + "\n".join(parts)
        return prompt

    # ------ Snapshot and Restore methods ------
    def snapshot(self) -> Dict[str, Any]:
        """Create a snapshot of the current memory manager state.
        
        Returns:
            Dict containing all state information needed to restore the instance.
        """
        snapshot_data = {
            "memory_fragment_types": self.memory_fragment_types,
            "memory_fragments": self.memory_fragments,
            "summaries": self.summaries,
            "_last_indexed_entry_idx": self._last_indexed_entry_idx,
            "max_summary_tokens": self.max_summary_tokens,
            "max_ctx_tokens": self.max_ctx_tokens,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        return snapshot_data

    def restore(self, snapshot_data: Dict[str, Any]) -> None:
        """Restore the memory manager state from a snapshot.
        
        Args:
            snapshot_data: Dictionary containing state information from a previous snapshot.
        """
        # Restore memory fragments and types
        self.memory_fragment_types = snapshot_data.get("memory_fragment_types", self.memory_fragment_types)
        self.memory_fragments = snapshot_data.get("memory_fragments", self.memory_fragments)
        self.summaries = snapshot_data.get("summaries", [])
        self._last_indexed_entry_idx = snapshot_data.get("_last_indexed_entry_idx", 0)
        
        # Restore configuration (if present)
        if "max_summary_tokens" in snapshot_data:
            self.max_summary_tokens = snapshot_data["max_summary_tokens"]
        if "max_ctx_tokens" in snapshot_data:
            self.max_ctx_tokens = snapshot_data["max_ctx_tokens"]
        
        # Update convenience shortcuts
        self.facts = self.memory_fragments.get("facts", {})
        self.user_prefs = self.memory_fragments.get("user_prefs", {})
        self.task_state = self.memory_fragments.get("task_state", {})
        
        # Save restored state to disk for persistence
        self._save()

    def save_snapshot(self, file_path: str) -> None:
        """Save a snapshot to disk.
        
        Args:
            file_path: Path where the snapshot should be saved.
        """
        snapshot_data = self.snapshot()
        
        # Use pickle for backward compatibility with other components
        with open(file_path, 'wb') as f:
            pickle.dump(snapshot_data, f)
        
        console.print(f"[MEMORY] Snapshot saved to {file_path}")

    def load_snapshot(self, file_path: str) -> bool:
        """Load and restore from a snapshot file.
        
        Args:
            file_path: Path to the snapshot file to load.
            
        Returns:
            True if load successful, False otherwise
        """
        try:
            with open(file_path, 'rb') as f:
                snapshot_data = pickle.load(f)
            
            if snapshot_data is not None:
                self.restore(snapshot_data)
                console.print(f"[MEMORY] Snapshot loaded from {file_path}")
                return True
            else:
                console.print(f"[MEMORY] Failed to load snapshot from {file_path}")
                return False
        except FileNotFoundError:
            console.print(f"[MEMORY] Snapshot file not found: {file_path}")
            return False
        except Exception as e:
            console.print(f"[MEMORY] Error loading snapshot: {e}")
            return False
