import asyncio
from typing import Any, Dict, List, Optional, Tuple
from framework.utils.io_tools import console
from framework.utils.tokenomics import num_tokens_from_string


class ContextManager:
    """Builds context windows for agent prompts.

    Allocation strategy (default ratios, configurable via __init__):
    - recent_chat_ratio: portion of tokens for raw recent chat (≈30%)
    - memory_ratio:      portion for structured memory (≈50%)
    - trace_ratio:       portion for semantic trace retrieval (≈20%)
    """

    def __init__(
        self,
        max_ctx_tokens: int = 20000,
        recent_chat_ratio: float = 0.30,
        memory_ratio: float = 0.50,
        trace_ratio: float = 0.20,
        traces_vector_store: Any = None,
    ):
        self.max_ctx_tokens = max_ctx_tokens
        self.recent_chat_ratio = recent_chat_ratio
        self.memory_ratio = memory_ratio
        self.trace_ratio = trace_ratio
        self._traces_vector_store = traces_vector_store

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

    # ---------------------------------------------------------------------
    # Public API
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
                # Budget for recent chat reached – stop adding further entries
                break # NOTE: This is not a useful strategy (place holder): We should try to may be summarize the entry insted
            recent_entries.insert(0, ctx)
            total_tokens += tokens
            diagnostics["recent_chat_tokens"] += tokens
        if recent_entries:
            segments.append("--- Recent Chat History ---")
            segments.extend(recent_entries)
        """   W.I.P
        # -------------------------------------------------------------
        # 2. Structured memory (facts, preferences, task state, summaries)
        # -------------------------------------------------------------
        memory_parts: List[str] = []
        for mem_cat in memory_manager.memory_fragment_types:
            cat_memories = memory_manager.recall(category=mem_cat)
            #print(f"[!!!!!] category[{mem_cat}]: cat_memories[{type(cat_memories)}] = {cat_memories}")
            #Ks = list(cat_memories.keys())
            if len(cat_memories) <1 : continue
            memory_parts.append(f"--- {mem_cat} ---")
            for mem_frag in cat_memories: 
                memory_parts.append(f"  {mem_frag}")
        #print(f"[+][CTX]: Memorries =\n {memory_parts}")
        memory_text = "\n".join(memory_parts)
        memory_tokens = self._estimate_token_count(memory_text)
        if memory_tokens <= memory_budget and total_tokens + memory_tokens <= self.max_ctx_tokens:
            segments.append(memory_text)
            total_tokens += memory_tokens
            diagnostics["memory_tokens"] = memory_tokens

        # -------------------------------------------------------------
        # 3. Semantic trace retrieval (if a vector store is attached)
        # -------------------------------------------------------------
        if self._traces_vector_store and (user_query or agent_plan):
            query = user_query or agent_plan or ""
            try:
                results = asyncio.run(
                    self._traces_vector_store.query(query=query, n_results=5)
                )
                trace_segments: List[str] = []
                trace_tokens_used = 0
                for r in results:
                    if isinstance(r, dict):
                        doc = r.get("document", "")
                        src = r.get("source", "trace")
                    else:
                        doc = getattr(r, "document", "")
                        src = getattr(r, "source", "trace")
                    entry = f"[{src}] {doc}"
                    entry_tokens = self._estimate_token_count(entry)
                    if trace_tokens_used + entry_tokens > trace_budget:
                        continue
                    trace_segments.append(entry)
                    trace_tokens_used += entry_tokens
                    diagnostics["trace_results"].append(r)
                if trace_segments:
                    segments.append("--- Semantic Traces ---")
                    segments.extend(trace_segments)
                    total_tokens += trace_tokens_used
                    diagnostics["trace_tokens"] = trace_tokens_used
            except Exception as exc:
                console.print(f"[CONTEXT] Trace retrieval failed: {exc}")
        """
        # -------------------------------------------------------------
        # Ensure all segments are strings before joining (fix for TypeError)
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
