import asyncio
from typing import Any, Dict, List, Optional, Tuple
from framework.utils.io_tools import console
from framework.utils.tokenomics import num_tokens_from_string
from datetime import datetime
import json


class ContextManager:
    """Builds context windows for agent prompts.

    Allocation strategy (default ratios, configurable via __init__):
    - recent_chat_ratio: portion of tokens for raw recent chat (≈30%)
    - memory_ratio:      portion for structured memory (≈50%)
    - trace_ratio:       portion for semantic trace retrieval (≈20%)
    
    The context manager maintains a persistent current_ctx buffer that
    represents the actual context sent to the LLM, separate from the full
    chat history. The buffer is rebuilt only when it exceeds a threshold.
    
    FIXED: current_ctx now consistently stores structured dict entries
    with metadata for better control and debugging.
    """

    def __init__(
        self,
        max_ctx_tokens: int = 20000,
        recent_chat_ratio: float = 0.30,
        memory_ratio: float = 0.50,
        trace_ratio: float = 0.20,
        rebuild_threshold: float = 0.85,
        traces_vector_store: Any = None,
        session_dir: str = "./",
    ):
        self.max_ctx_tokens = max_ctx_tokens
        self.recent_chat_ratio = recent_chat_ratio
        self.memory_ratio = memory_ratio
        self.trace_ratio = trace_ratio
        self.rebuild_threshold = rebuild_threshold
        self._traces_vector_store = traces_vector_store
        self.session_dir = session_dir
        
        # Current context buffer - now stores structured dicts with metadata
        # Format: [{"sender": str, "content": str, "timestamp": str, "tokens": int}]
        self.current_ctx: List[Dict[str, Any]] = []
        self.current_ctx_tokens: int = 0
        
        # Metrics and monitoring
        self.rebuild_count: int = 0
        self.total_appends: int = 0
        self.context_version: int = 0
        self.context_history: List[Dict[str, Any]] = []  # For rollback capability
        self.last_rebuild_timestamp: Optional[str] = None

    # ---------------------------------------------------------------------
    # Helper methods
    # ---------------------------------------------------------------------
    def set_traces_vector_store(self, traces_vs: Any) -> None:
        """Attach the traces vector store for semantic retrieval."""
        self._traces_vector_store = traces_vs
        console.print("[CONTEXT] Traces vector store attached.")

    def _estimate_token_count(self, text: str) -> int:
        """Return an approximate token count for *text* using the project's tokenomics utility."""
        return num_tokens_from_string(text)

    def should_rebuild(self) -> bool:
        """Check if current context buffer exceeds rebuild threshold."""
        if self.max_ctx_tokens == 0:
            return False
        utilization = self.current_ctx_tokens / self.max_ctx_tokens
        return utilization >= self.rebuild_threshold

    def _create_context_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Create a standardized context entry with metadata.
        
        Args:
            entry: A chat history entry dict with 'sender', 'content', 'timestamp'
            
        Returns:
            Structured context entry with token count and metadata
        """
        sender = entry.get("sender", "") if isinstance(entry, dict) else getattr(entry, "sender", "")
        content = entry.get("content", "") if isinstance(entry, dict) else getattr(entry, "content", "")
        timestamp = entry.get("timestamp", "") if isinstance(entry, dict) else getattr(entry, "timestamp", "")
        
        ctx_line = f"[{timestamp}][{sender}]: {content}"
        tokens = self._estimate_token_count(ctx_line)
        
        return {
            "sender": sender,
            "content": content,
            "timestamp": timestamp,
            "formatted": ctx_line,
            "tokens": tokens
        }

    def append_to_current_ctx(self, entry: Dict[str, Any]) -> None:
        """Incrementally append a new chat entry to the current context buffer.
        
        Args:
            entry: A chat history entry dict with 'sender', 'content', 'timestamp'
        """
        ctx_entry = self._create_context_entry(entry)
        self.current_ctx.append(ctx_entry)
        self.current_ctx_tokens += ctx_entry["tokens"]
        self.total_appends += 1

    def _save_context_snapshot(self) -> None:
        """Save current context state for potential rollback."""
        snapshot = {
            "version": self.context_version,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entries": self.current_ctx.copy(),
            "tokens": self.current_ctx_tokens,
            "rebuild_count": self.rebuild_count
        }
        self.context_history.append(snapshot)
        # Keep only last 5 snapshots
        if len(self.context_history) > 5:
            self.context_history.pop(0)

    def rollback_context(self, version: Optional[int] = None) -> bool:
        """Rollback to a previous context version.
        
        Args:
            version: Version number to rollback to. If None, rollback to previous version.
            
        Returns:
            True if rollback successful, False otherwise
        """
        if not self.context_history:
            console.print("[CONTEXT] No history available for rollback")
            return False
        
        if version is None:
            # Rollback to previous version
            snapshot = self.context_history[-1]
        else:
            # Find specific version
            snapshot = None
            for s in reversed(self.context_history):
                if s["version"] == version:
                    snapshot = s
                    break
            if not snapshot:
                console.print(f"[CONTEXT] Version {version} not found in history")
                return False
        
        self.current_ctx = snapshot["entries"].copy()
        self.current_ctx_tokens = snapshot["tokens"]
        self.context_version = snapshot["version"]
        console.print(f"[CONTEXT] Rolled back to version {self.context_version}")
        return True

    def _identify_critical_entries(self, chat_history: List[Dict[str, Any]]) -> List[int]:
        """Identify indices of critical entries that should be preserved during rebuild.
        
        Critical entries include:
        - User preferences
        - System configurations
        - Key decisions
        - Error/warning messages
        
        Returns:
            List of indices to preserve
        """
        critical_indices = []
        critical_keywords = [
            "preference", "config", "decision", "error", "warning",
            "critical", "important", "remember", "note", "todo"
        ]
        
        for idx, entry in enumerate(chat_history):
            content = str(entry.get("content", "")).lower()
            if any(keyword in content for keyword in critical_keywords):
                critical_indices.append(idx)
        
        return critical_indices

    def rebuild_current_ctx(
        self,
        chat_history: List[Dict[str, Any]],
        memory_manager: Any,
        target_utilization: float = 0.6,
        verbose: int = 0
    ) -> None:
        """Rebuild the current context buffer by compacting and summarizing.
        
        IMPROVED: Now uses sliding window + summarization hybrid strategy
        and preserves critical entries.
        
        Args:
            chat_history: Full chat history
            memory_manager: Memory manager instance
            target_utilization: Target utilization ratio after rebuild (0.0-1.0)
            verbose: Verbosity level
        """
        if verbose > 0:
            console.print(f"[CONTEXT] Rebuilding context buffer (current: {self.current_ctx_tokens} tokens)")
        
        # Save snapshot before rebuild
        self._save_context_snapshot()
        
        # Calculate target token budget
        target_tokens = int(self.max_ctx_tokens * target_utilization)
        
        # Identify critical entries to preserve
        critical_indices = self._identify_critical_entries(chat_history)
        
        # Calculate budgets for each section
        recent_budget = int(target_tokens * self.recent_chat_ratio)
        memory_budget = int(target_tokens * self.memory_ratio)
        trace_budget = int(target_tokens * self.trace_ratio)
        
        # Build new context with sliding window approach
        new_ctx: List[Dict[str, Any]] = []
        new_ctx_tokens = 0
        
        # 1. Add most recent entries (working backwards)
        for entry in reversed(chat_history):
            ctx_entry = self._create_context_entry(entry)
            if new_ctx_tokens + ctx_entry["tokens"] > recent_budget:
                break
            new_ctx.insert(0, ctx_entry)
            new_ctx_tokens += ctx_entry["tokens"]
        
        # 2. Add critical entries if not already included
        recent_indices = set(range(len(chat_history) - len(new_ctx), len(chat_history)))
        for idx in critical_indices:
            if idx not in recent_indices and new_ctx_tokens < target_tokens:
                entry = chat_history[idx]
                ctx_entry = self._create_context_entry(entry)
                if new_ctx_tokens + ctx_entry["tokens"] <= target_tokens:
                    # Insert in chronological position
                    insert_pos = sum(1 for e in new_ctx if chat_history.index({"sender": e["sender"], "content": e["content"], "timestamp": e["timestamp"]}) < idx)
                    new_ctx.insert(insert_pos, ctx_entry)
                    new_ctx_tokens += ctx_entry["tokens"]
        
        # Update current context
        self.current_ctx = new_ctx
        self.current_ctx_tokens = new_ctx_tokens
        self.rebuild_count += 1
        self.context_version += 1
        self.last_rebuild_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if verbose > 0:
            console.print(
                f"[CONTEXT] Context rebuilt: {self.current_ctx_tokens} tokens "
                f"(target: {target_tokens}, version: {self.context_version})")

    def get_compacted_context(self) -> str:
        """Return the current context buffer as a formatted string.
        
        This is the context that should be sent to the LLM.
        """
        return "\n".join([entry["formatted"] for entry in self.current_ctx])

    def get_context_diagnostics(self) -> Dict[str, Any]:
        """Return comprehensive diagnostics about the current context buffer.
        
        Returns:
            Dictionary with context statistics, utilization info, and metrics
        """
        utilization = self.current_ctx_tokens / self.max_ctx_tokens if self.max_ctx_tokens > 0 else 0
        
        # Calculate average tokens per entry
        avg_tokens = self.current_ctx_tokens / len(self.current_ctx) if self.current_ctx else 0
        
        return {
            "current_ctx_tokens": self.current_ctx_tokens,
            "max_ctx_tokens": self.max_ctx_tokens,
            "utilization": utilization,
            "utilization_pct": utilization * 100,
            "should_rebuild": self.should_rebuild(),
            "rebuild_threshold": self.rebuild_threshold,
            "num_entries": len(self.current_ctx),
            "avg_tokens_per_entry": avg_tokens,
            "rebuild_count": self.rebuild_count,
            "total_appends": self.total_appends,
            "context_version": self.context_version,
            "last_rebuild": self.last_rebuild_timestamp,
            "snapshots_available": len(self.context_history)
        }

    def save_context(self, filepath: Optional[str] = None) -> None:
        """Save current context to disk for persistence across sessions.
        
        Args:
            filepath: Optional custom filepath. If None, uses session_dir.
        """
        if filepath is None:
            filepath = f"{self.session_dir}/context_state.json"
        
        state = {
            "current_ctx": self.current_ctx,
            "current_ctx_tokens": self.current_ctx_tokens,
            "context_version": self.context_version,
            "rebuild_count": self.rebuild_count,
            "total_appends": self.total_appends,
            "last_rebuild": self.last_rebuild_timestamp,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(filepath, 'w') as f:
            json.dump(state, f, indent=2)
        
        console.print(f"[CONTEXT] State saved to {filepath}")

    def load_context(self, filepath: Optional[str] = None) -> bool:
        """Load context from disk to restore previous session.
        
        Args:
            filepath: Optional custom filepath. If None, uses session_dir.
            
        Returns:
            True if load successful, False otherwise
        """
        if filepath is None:
            filepath = f"{self.session_dir}/context_state.json"
        
        try:
            with open(filepath, 'r') as f:
                state = json.load(f)
            
            self.current_ctx = state["current_ctx"]
            self.current_ctx_tokens = state["current_ctx_tokens"]
            self.context_version = state["context_version"]
            self.rebuild_count = state["rebuild_count"]
            self.total_appends = state["total_appends"]
            self.last_rebuild_timestamp = state.get("last_rebuild")
            
            console.print(f"[CONTEXT] State loaded from {filepath} (version {self.context_version})")
            return True
        except FileNotFoundError:
            console.print(f"[CONTEXT] No saved state found at {filepath}")
            return False
        except Exception as e:
            console.print(f"[CONTEXT] Failed to load state: {e}")
            return False

    # ---------------------------------------------------------------------
    # Public API (legacy method, still used for initial context building)
    # ---------------------------------------------------------------------
    def build_context(
        self,
        chat_history: List[Dict[str, Any]],
        memory_manager: Any,
        recent_chat_budget: int,
        memory_budget: int,
        trace_budget: int,
        user_query: Optional[str] = None,
        agent_plan: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Assemble a context string respecting the provided token budgets.

        Returns a tuple ``(context_string, diagnostics)`` where *diagnostics*
        contains token usage per source and any trace retrieval results.
        """
        segments: List[str] = []
        total_tokens = 0
        diagnostics = {
            "recent_chat_tokens": 0,
            "memory_tokens": 0,
            "trace_tokens": 0,
            "trace_results": [],
        }

        # -------------------------------------------------------------
        # 1. Recent chat history (raw entries)
        # -------------------------------------------------------------
        recent_entries: List[str] = []
        # Walk backwards so we keep the newest entries first
        for entry in reversed(chat_history):
            sender = entry.get("sender", "") if isinstance(entry, dict) else getattr(entry, "sender", "")
            content = entry.get("content", "") if isinstance(entry, dict) else getattr(entry, "content", "")
            timestamp = entry.get("timestamp", "") if isinstance(entry, dict) else getattr(entry, "timestamp", "")
            ctx = f"[{timestamp}][{sender}]: {content}"
            tokens = self._estimate_token_count(ctx)
            if total_tokens + tokens > recent_chat_budget:
                break
            recent_entries.insert(0, ctx)
            total_tokens += tokens
            diagnostics["recent_chat_tokens"] += tokens
        if recent_entries:
            segments.append("--- Recent Chat History ---")
            segments.extend(recent_entries)

        # -------------------------------------------------------------
        # Ensure all segments are strings before joining
        # -------------------------------------------------------------
        segments = [str(s) for s in segments]

        # -------------------------------------------------------------
        # Assemble final string
        # -------------------------------------------------------------
        context_str = "\n\n".join(segments)
        if total_tokens > self.max_ctx_tokens:
            console.print(
                f"[CONTEXT] Warning: context size ({total_tokens} tokens) exceeds max "
                f"({self.max_ctx_tokens}). Consider increasing limits or shrinking budgets."
            )
        return context_str, diagnostics
