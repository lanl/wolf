import asyncio
from typing import Any, Dict, List, Optional, Tuple
from framework.utils.io_tools import console
from framework.utils.tokenomics import num_tokens_from_string
from datetime import datetime
import json
import pickle
import hashlib


def copy_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


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
        rebuild_threshold: float = 0.850,
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

        # Context ledger/manifest state. This describes the active context as a
        # curated view over durable chat history, not as the source of truth.
        self.context_manifest: Dict[str, Any] = self._new_context_manifest()
        self.pinned_entries: Dict[str, Dict[str, Any]] = {}
        self.working_memory_packet: Dict[str, Any] = {}
        self.context_policy: Dict[str, Any] = self._default_context_policy()

    # ---------------------------------------------------------------------
    # Helper methods
    # ---------------------------------------------------------------------

    def _new_context_manifest(self) -> Dict[str, Any]:
        """Create an empty context ledger/manifest."""
        return {
            "version": 0,
            "updated_at": None,
            "raw_entries": [],
            "summarized_ranges": [],
            "pinned_entries": [],
            "memory_references": [],
            "omitted_ranges": [],
            "retrieval_hints": [],
            "working_memory_packet": {},
            "compression_provenance": [],
            "policy": {},
        }

    def _default_context_policy(self) -> Dict[str, Any]:
        """Return default context-management policy metadata."""
        return {
            "profile": "general",
            "auto_rebuild_enabled": True,
            "rebuild_threshold": self.rebuild_threshold,
            "target_utilization": 0.60,
            "preserve_pinned": True,
            "preserve_working_memory": True,
        }

    def _merge_context_policy(self, policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Merge a stored/partial policy with defaults for backward compatibility."""
        merged = self._default_context_policy()
        if isinstance(policy, dict):
            merged.update(policy)
        merged["rebuild_threshold"] = float(merged.get("rebuild_threshold", self.rebuild_threshold))
        self.rebuild_threshold = merged["rebuild_threshold"]
        return merged

    def _cognitive_load_state(self, utilization: Optional[float] = None) -> str:
        """Map context utilization to a human-readable cognitive-load state."""
        if utilization is None:
            utilization = self.current_ctx_tokens / self.max_ctx_tokens if self.max_ctx_tokens > 0 else 0
        if utilization <= 0.50:
            return "sober"
        if utilization <= 0.70:
            return "caution"
        if utilization <= 0.80:
            return "impaired"
        if utilization <= 0.85:
            return "high_risk"
        return "emergency_brake"

    def _score_salience(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Return deterministic advisory salience metadata for a context entry."""
        content = self._stringify_content(entry.get("content", ""))
        content_l = content.lower()
        action_text = self._stringify_content(entry.get("action", "")).lower()
        score = 0
        reasons: List[str] = []
        sender = str(entry.get("sender", "")).lower()
        if sender in {"user", "human"}:
            score += 3
            reasons.append("user_authored")
        if any(k in content_l for k in ["error", "warning", "critical", "failed", "exception"]):
            score += 3
            reasons.append("error_or_warning")
        if any(k in content_l for k in ["decision", "remember", "important", "todo", "task", "bug", "fix"]):
            score += 2
            reasons.append("important_keyword")
        if action_text:
            score += 1
            reasons.append("action_metadata")
        if entry.get("pinned"):
            score += 5
            reasons.append("pinned")
        return {"score": score, "reasons": reasons}

    def set_traces_vector_store(self, traces_vs: Any) -> None:
        """Attach the traces vector store for semantic retrieval."""
        self._traces_vector_store = traces_vs
        console.print("[CONTEXT] Traces vector store attached.")

    def _estimate_token_count(self, text: str) -> int:
        """Return an approximate token count for *text* using the project's tokenomics utility."""
        return num_tokens_from_string(text)

    def should_rebuild(self) -> bool:
        """Check if current context buffer exceeds rebuild threshold.

        The threshold remains an emergency brake, but can be disabled by the
        explicit context policy for workflows that want fully agent-directed
        compaction. Manual/forced rebuild actions are unaffected.
        """
        if self.max_ctx_tokens == 0:
            return False
        if not getattr(self, "context_policy", {}).get("auto_rebuild_enabled", True):
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
        sender = self._entry_field(entry, "sender", "")
        content = self._stringify_content(self._entry_field(entry, "content", ""))
        timestamp = self._entry_field(entry, "timestamp", "")
        
        ctx_line = f"[{timestamp}][{sender}]: {content}"
        tokens = self._estimate_token_count(ctx_line)
        
        history_index = self._entry_field(entry, "history_index", None)
        action = self._entry_field(entry, "action", None)
        entry_id = self._entry_field(entry, "entry_id", None)
        if entry_id is None:
            idx_part = "x" if history_index is None else str(history_index)
            entry_id = f"ctx_{idx_part}_{hashlib.sha1(ctx_line.encode('utf-8')).hexdigest()[:12]}"

        ctx_entry = {
            "entry_id": entry_id,
            "history_index": history_index,
            "sender": sender,
            "content": content,
            "timestamp": timestamp,
            "action": action,
            "formatted": ctx_line,
            "tokens": tokens,
        }
        ctx_entry["salience"] = self._score_salience(ctx_entry)
        if str(entry_id) in self.pinned_entries:
            ctx_entry["pinned"] = True
        return ctx_entry

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

    def _entry_field(self, entry: Any, field: str, default: Any = "") -> Any:
        """Return *field* from either a dict-like or object-like chat entry.

        Chat/history entries are not guaranteed to contain only strings. Some
        action payloads and tool results are stored as dicts/lists. Context
        rebuild code must therefore normalize values before applying string
        operations such as ``lower()`` or token counting.
        """
        if isinstance(entry, dict):
            return entry.get(field, default)
        return getattr(entry, field, default)

    def _stringify_content(self, content: Any) -> str:
        """Convert arbitrary chat content to a stable string representation.

        The context manager receives content from user messages, structured
        agent actions, syscall results, and manager diagnostics. Those may be
        strings, dicts, lists, numbers, or None. JSON is preferred for
        containers so that context text is deterministic and readable.
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(content)

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
            raw_content = self._entry_field(entry, "content", "")
            content = self._stringify_content(raw_content).lower()
            if any(keyword in content for keyword in critical_keywords):
                critical_indices.append(idx)
        
        return critical_indices

    def pin_context_entry(self, entry_id: Optional[str] = None, history_index: Optional[int] = None, reason: Optional[str] = None, label: Optional[str] = None) -> Dict[str, Any]:
        """Pin an active context entry so rebuilds try to preserve it."""
        match = None
        for entry in self.current_ctx:
            if not isinstance(entry, dict):
                continue
            if entry_id is not None and str(entry.get("entry_id")) == str(entry_id):
                match = entry
                break
            if history_index is not None and entry.get("history_index") == history_index:
                match = entry
                break
        if match is None:
            raise ValueError("No active context entry matched entry_id/history_index")
        pinned_id = str(match.get("entry_id"))
        match["pinned"] = True
        match["pin_reason"] = reason
        match["pin_label"] = label
        match["salience"] = self._score_salience(match)
        self.pinned_entries[pinned_id] = {
            "entry_id": pinned_id,
            "history_index": match.get("history_index"),
            "timestamp": match.get("timestamp"),
            "sender": match.get("sender"),
            "reason": reason,
            "label": label,
            "entry": copy_safe(match),
            "pinned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.build_context_manifest()
        return self.pinned_entries[pinned_id]

    def unpin_context_entry(self, entry_id: str) -> Dict[str, Any]:
        """Remove a pin by entry id."""
        removed = self.pinned_entries.pop(str(entry_id), None)
        for entry in self.current_ctx:
            if isinstance(entry, dict) and str(entry.get("entry_id")) == str(entry_id):
                entry.pop("pinned", None)
                entry.pop("pin_reason", None)
                entry.pop("pin_label", None)
                entry["salience"] = self._score_salience(entry)
        self.build_context_manifest()
        return {"entry_id": entry_id, "removed": removed is not None}

    def list_pinned_entries(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.pinned_entries)

    def update_working_memory_packet(self, **fields: Any) -> Dict[str, Any]:
        """Update the compact working-memory packet kept in active context."""
        allowed = {
            "current_objective", "current_plan", "current_step", "active_files",
            "modified_files", "open_tasks", "open_questions", "decisions",
            "known_bugs_warnings", "last_successful_action", "next_recommended_action",
        }
        for key, value in fields.items():
            if key in allowed and value is not None:
                self.working_memory_packet[key] = value
        self.working_memory_packet["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if self.context_policy.get("preserve_working_memory", True):
            self._upsert_working_memory_context_entry()
        self.build_context_manifest()
        return dict(self.working_memory_packet)

    def get_working_memory_packet(self) -> Dict[str, Any]:
        return dict(self.working_memory_packet)

    def _upsert_working_memory_context_entry(self) -> None:
        """Insert or replace the working-memory packet entry in current_ctx."""
        wm_entry = self._working_memory_context_entry()
        self.current_ctx = [
            entry for entry in self.current_ctx
            if not (isinstance(entry, dict) and entry.get("entry_id") == "working_memory_packet")
        ]
        if wm_entry is not None:
            self.current_ctx.insert(0, wm_entry)
        self._recompute_current_ctx_tokens()

    def _working_memory_context_entry(self) -> Optional[Dict[str, Any]]:
        if not self.working_memory_packet:
            return None
        text = "[WORKING MEMORY PACKET]\n" + json.dumps(self.working_memory_packet, indent=2, ensure_ascii=False, sort_keys=True, default=str)
        entry = self._create_context_entry({"sender": "system", "content": text, "timestamp": self.working_memory_packet.get("updated_at", ""), "history_index": -1})
        entry["entry_id"] = "working_memory_packet"
        entry["pinned"] = True
        entry["salience"] = self._score_salience(entry)
        return entry

    def build_context_manifest(self, chat_history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Build/update the machine-readable context ledger."""
        raw_entries = []
        memory_refs = []
        for entry in self.current_ctx:
            if not isinstance(entry, dict):
                continue
            raw_entries.append({
                "entry_id": entry.get("entry_id"),
                "history_index": entry.get("history_index"),
                "sender": entry.get("sender"),
                "timestamp": entry.get("timestamp"),
                "tokens": entry.get("tokens"),
                "pinned": bool(entry.get("pinned")),
                "salience": entry.get("salience", {}),
            })
            if entry.get("memory_key"):
                memory_refs.append({"memory_key": entry.get("memory_key"), "replaces_range": entry.get("replaces_range")})
        included = [e.get("history_index") for e in raw_entries if isinstance(e.get("history_index"), int) and e.get("history_index") >= 0]
        omitted_ranges = []
        if chat_history is not None:
            if not included:
                if len(chat_history) > 0:
                    omitted_ranges.append([0, len(chat_history)])
            else:
                included_set = set(included)
                start = None
                for idx in range(len(chat_history)):
                    if idx not in included_set and start is None:
                        start = idx
                    if (idx in included_set or idx == len(chat_history) - 1) and start is not None:
                        end = idx if idx in included_set else idx + 1
                        omitted_ranges.append([start, end])
                        start = None
        self.context_manifest = {
            "version": self.context_version,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "raw_entries": raw_entries,
            "summarized_ranges": [e.get("replaces_range") for e in self.current_ctx if isinstance(e, dict) and e.get("replaces_range")],
            "pinned_entries": list(self.pinned_entries.values()),
            "memory_references": memory_refs,
            "omitted_ranges": omitted_ranges,
            "retrieval_hints": self.context_policy.get("retrieval_hints", []),
            "working_memory_packet": dict(self.working_memory_packet),
            "compression_provenance": self.context_policy.get("compression_provenance", []),
            "policy": dict(self.context_policy),
        }
        return self.context_manifest

    def list_context_manifest(self) -> Dict[str, Any]:
        return dict(self.context_manifest)

    def audit_context_integrity(self) -> Dict[str, Any]:
        """Audit whether critical working state is represented in active context."""
        wm = self.working_memory_packet
        issues = []
        for field in ["current_objective", "current_plan", "current_step", "next_recommended_action"]:
            if not wm.get(field):
                issues.append(f"working_memory_packet missing {field}")
        if self.context_policy.get("preserve_pinned", True) and not self.pinned_entries:
            issues.append("no pinned entries recorded")
        diag = self.get_context_diagnostics()
        if diag["cognitive_load_state"] in {"high_risk", "emergency_brake"}:
            issues.append(f"context load is {diag['cognitive_load_state']}")
        return {"ok": not issues, "issues": issues, "diagnostics": diag, "manifest_version": self.context_manifest.get("version")}

    def set_context_policy(self, **policy: Any) -> Dict[str, Any]:
        """Update context policy/profile metadata."""
        allowed = {
            "profile", "auto_rebuild_enabled", "rebuild_threshold",
            "target_utilization", "preserve_pinned", "preserve_working_memory",
            "retrieval_hints",
        }
        for key, value in policy.items():
            if key not in allowed or value is None:
                continue
            if key in {"rebuild_threshold", "target_utilization"}:
                value = float(value)
                if not 0.0 < value <= 1.0:
                    raise ValueError(f"{key} must be in the interval (0.0, 1.0]")
            if key in {"auto_rebuild_enabled", "preserve_pinned", "preserve_working_memory"}:
                value = bool(value)
            if key == "retrieval_hints":
                if isinstance(value, str):
                    value = [value]
                value = [str(v) for v in value]
            self.context_policy[key] = value
        self.rebuild_threshold = float(self.context_policy.get("rebuild_threshold", self.rebuild_threshold))
        self.context_policy["rebuild_threshold"] = self.rebuild_threshold
        self.build_context_manifest()
        return dict(self.context_policy)

    def promote_context_to_memory(self, start_index: int, end_index: int, memory_manager: Any, category: str, key: str, note: Optional[str] = None) -> Dict[str, Any]:
        """Promote a durable history/context range into structured memory."""
        if start_index < 0 or end_index < start_index:
            raise ValueError("Invalid range: expected 0 <= start_index <= end_index")
        selected = []
        for pos, entry in enumerate(self.current_ctx):
            if not isinstance(entry, dict):
                continue
            idx = entry.get("history_index", pos)
            if isinstance(idx, int) and start_index <= idx < end_index:
                selected.append(entry)
        value = {
            "source_range": [start_index, end_index],
            "note": note,
            "entries": [{"history_index": e.get("history_index"), "sender": e.get("sender"), "timestamp": e.get("timestamp"), "content": e.get("content")} for e in selected],
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        memory_manager.remember(key=key, value=value, category=category)
        provenance = {"range": [start_index, end_index], "memory_key": key, "category": category, "created_at": value["created_at"], "note": note}
        self.context_policy.setdefault("compression_provenance", []).append(provenance)
        self.build_context_manifest()
        return provenance

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
        
        # 2. Add critical entries if not already included. Keep the original
        # history index as metadata so chronological insertion does not rely on
        # reconstructing a dict and calling ``chat_history.index(...)``. That
        # older approach was fragile for non-string content and duplicate rows.
        recent_indices = set(range(len(chat_history) - len(new_ctx), len(chat_history)))
        for offset, ctx_entry in enumerate(new_ctx):
            ctx_entry.setdefault("history_index", len(chat_history) - len(new_ctx) + offset)

        for idx in critical_indices:
            if idx not in recent_indices and new_ctx_tokens < target_tokens:
                entry = chat_history[idx]
                ctx_entry = self._create_context_entry(entry)
                ctx_entry["history_index"] = idx
                if new_ctx_tokens + ctx_entry["tokens"] <= target_tokens:
                    # Insert in chronological position.
                    insert_pos = sum(1 for e in new_ctx if e.get("history_index", len(chat_history)) < idx)
                    new_ctx.insert(insert_pos, ctx_entry)
                    new_ctx_tokens += ctx_entry["tokens"]
        
        # 3. Re-add pinned entries that may have fallen outside the recent/critical budget.
        if self.context_policy.get("preserve_pinned", True):
            existing_ids = {str(e.get("entry_id")) for e in new_ctx if isinstance(e, dict)}
            for pin in self.pinned_entries.values():
                entry = pin.get("entry")
                if isinstance(entry, dict) and str(entry.get("entry_id")) not in existing_ids:
                    new_ctx.append(entry)
                    existing_ids.add(str(entry.get("entry_id")))

        # 4. Keep the working memory packet in the active context if present.
        if self.context_policy.get("preserve_working_memory", True):
            wm_entry = self._working_memory_context_entry()
            if wm_entry is not None:
                new_ctx = [e for e in new_ctx if not (isinstance(e, dict) and e.get("entry_id") == "working_memory_packet")]
                new_ctx.insert(0, wm_entry)

        new_ctx.sort(key=lambda e: e.get("history_index", 10**12) if isinstance(e, dict) and e.get("history_index") != -1 else -1)
        new_ctx_tokens = sum(int(e.get("tokens", 0)) for e in new_ctx if isinstance(e, dict))

        # Update current context
        self.current_ctx = new_ctx
        self.current_ctx_tokens = new_ctx_tokens
        self.rebuild_count += 1
        self.context_version += 1
        self.last_rebuild_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.build_context_manifest(chat_history=chat_history)
        
        if verbose > 0:
            console.print(
                f"[CONTEXT] Context rebuilt: {self.current_ctx_tokens} tokens "
                f"(target: {target_tokens}, version: {self.context_version})")

    def get_compacted_context(self) -> str:
        """Return the current context buffer as a formatted string.
        
        This is the context that should be sent to the LLM. The working memory
        packet is always prepended when present, even if no rebuild has occurred
        since it was updated.
        """
        if self.working_memory_packet and not any(
            isinstance(entry, dict) and entry.get("entry_id") == "working_memory_packet"
            for entry in self.current_ctx
        ):
            self._upsert_working_memory_context_entry()
        return "\n".join([entry.get("formatted", str(entry)) for entry in self.current_ctx])

    # ---------------------------------------------------------------------
    # Context-window action support methods
    # ---------------------------------------------------------------------
    def _recompute_current_ctx_tokens(self) -> int:
        """Recompute and store the token total for ``current_ctx``.

        This is used by surgical context-window operations that remove,
        replace, or filter entries after they have already been tokenized.
        """
        total = 0
        for entry in self.current_ctx:
            if not isinstance(entry, dict):
                entry = self._create_context_entry(entry)
            tokens = entry.get("tokens")
            if not isinstance(tokens, int):
                formatted = entry.get("formatted")
                if formatted is None:
                    formatted = f"[{entry.get('timestamp', '')}][{entry.get('sender', '')}]: {self._stringify_content(entry.get('content', ''))}"
                    entry["formatted"] = formatted
                tokens = self._estimate_token_count(str(formatted))
                entry["tokens"] = tokens
            total += tokens
        self.current_ctx_tokens = total
        return total

    def _chat_history_from_current_ctx(self) -> List[Dict[str, Any]]:
        """Build a chat-history-like list from the active context buffer.

        ``force_rebuild`` is best when called with the authoritative full chat
        history. This fallback keeps the method safe when it is called directly
        by older code that only passes a recipe.
        """
        chat_history = []
        for entry in self.current_ctx:
            if isinstance(entry, dict):
                chat_history.append({
                    "sender": entry.get("sender", ""),
                    "content": entry.get("content", ""),
                    "timestamp": entry.get("timestamp", ""),
                })
            else:
                chat_history.append({"sender": "system", "content": entry, "timestamp": ""})
        return chat_history

    def force_rebuild(
        self,
        recipe: Optional[str] = "balanced",
        chat_history: Optional[List[Dict[str, Any]]] = None,
        memory_manager: Any = None,
        target_utilization: Optional[float] = None,
        verbose: int = 1,
    ) -> Dict[str, Any]:
        """Force an immediate context-buffer rebuild.

        Parameters
        ----------
        recipe:
            One of ``lean``, ``balanced``, or ``full``. The recipe controls the
            target post-rebuild utilization and the source allocation ratios.
        chat_history:
            The authoritative full chat history. If omitted, the current active
            context buffer is used as a safe fallback.
        memory_manager:
            Optional memory manager passed through to ``rebuild_current_ctx``.
        target_utilization:
            Optional explicit override for the recipe target.
        verbose:
            Verbosity level.

        Returns
        -------
        Dict[str, Any]
            Before/after diagnostics and the recipe used.
        """
        recipe_name = (recipe or "balanced").strip().lower()
        recipes = {
            # Keep only the most recent/raw essentials plus critical entries.
            "lean": {
                "target_utilization": 0.35,
                "recent_chat_ratio": 0.85,
                "memory_ratio": 0.10,
                "trace_ratio": 0.05,
            },
            # Normal automatic-compaction target.
            "balanced": {
                "target_utilization": 0.60,
                "recent_chat_ratio": 0.50,
                "memory_ratio": 0.30,
                "trace_ratio": 0.20,
            },
            # Preserve more raw recent chat while still getting below threshold.
            "full": {
                "target_utilization": 0.75,
                "recent_chat_ratio": 0.65,
                "memory_ratio": 0.25,
                "trace_ratio": 0.10,
            },
        }
        if recipe_name not in recipes:
            raise ValueError(f"Unknown context rebuild recipe '{recipe}'. Expected one of: {', '.join(recipes)}")

        before = self.get_context_diagnostics()
        selected = recipes[recipe_name]
        selected_target = selected["target_utilization"] if target_utilization is None else target_utilization
        if not 0.0 < selected_target <= 1.0:
            raise ValueError("target_utilization must be in the interval (0.0, 1.0]")

        if chat_history is None:
            chat_history = self._chat_history_from_current_ctx()

        # Temporarily apply recipe ratios. Preserve existing configuration so
        # this forced operation does not permanently mutate session policy.
        old_ratios = (self.recent_chat_ratio, self.memory_ratio, self.trace_ratio)
        try:
            self.recent_chat_ratio = selected["recent_chat_ratio"]
            self.memory_ratio = selected["memory_ratio"]
            self.trace_ratio = selected["trace_ratio"]
            self.rebuild_current_ctx(
                chat_history=chat_history,
                memory_manager=memory_manager,
                target_utilization=selected_target,
                verbose=verbose,
            )
        finally:
            self.recent_chat_ratio, self.memory_ratio, self.trace_ratio = old_ratios

        after = self.get_context_diagnostics()
        return {
            "recipe": recipe_name,
            "target_utilization": selected_target,
            "before": before,
            "after": after,
        }

    def set_window_start(self, start_index: int) -> Dict[str, Any]:
        """Slide the active context window forward.

        Entries with ``history_index`` lower than ``start_index`` are removed.
        If entries do not have ``history_index`` metadata, their current list
        position is used as a fallback index.
        """
        if start_index < 0:
            raise ValueError("start_index must be non-negative")
        before_entries = len(self.current_ctx)
        before_tokens = self.current_ctx_tokens
        filtered = []
        for pos, entry in enumerate(self.current_ctx):
            if not isinstance(entry, dict):
                entry = self._create_context_entry(entry)
            idx = entry.get("history_index", pos)
            if idx >= start_index:
                filtered.append(entry)
        self._save_context_snapshot()
        self.current_ctx = filtered
        self._recompute_current_ctx_tokens()
        self.context_version += 1
        self.last_rebuild_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "start_index": start_index,
            "entries_before": before_entries,
            "entries_after": len(self.current_ctx),
            "tokens_before": before_tokens,
            "tokens_after": self.current_ctx_tokens,
        }

    def set_filter(self, excluded: Optional[List[str]] = None) -> Dict[str, Any]:
        """Filter active context entries containing excluded categories/terms.

        The filter checks sender, action metadata, and stringified content. It
        is intentionally conservative and only affects the active context
        buffer, never the permanent chat history.
        """
        excluded = [str(x).strip().lower() for x in (excluded or []) if str(x).strip()]
        self.excluded_categories = excluded
        before_entries = len(self.current_ctx)
        before_tokens = self.current_ctx_tokens
        if not excluded:
            return {
                "excluded": [],
                "entries_before": before_entries,
                "entries_after": before_entries,
                "tokens_before": before_tokens,
                "tokens_after": before_tokens,
            }

        def matches(entry: Dict[str, Any]) -> bool:
            action = entry.get("action", "") if isinstance(entry, dict) else ""
            haystack = " ".join([
                str(entry.get("sender", "")),
                self._stringify_content(entry.get("content", "")),
                self._stringify_content(action),
            ]).lower()
            return any(term in haystack for term in excluded)

        self._save_context_snapshot()
        self.current_ctx = [e for e in self.current_ctx if not matches(e if isinstance(e, dict) else self._create_context_entry(e))]
        self._recompute_current_ctx_tokens()
        self.context_version += 1
        self.last_rebuild_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "excluded": excluded,
            "entries_before": before_entries,
            "entries_after": len(self.current_ctx),
            "tokens_before": before_tokens,
            "tokens_after": self.current_ctx_tokens,
        }

    def replace_range_with_memory(self, start: int, end: int, memory_key: str) -> Dict[str, Any]:
        """Replace an active-context history range with a compact memory ref.

        This supports the ``selective_context_summarization`` action. The
        actual summary is stored by the memory manager; the context buffer keeps
        a lightweight reference to that summary.
        """
        if start < 0 or end < start:
            raise ValueError("Invalid range: expected 0 <= start <= end")
        before_entries = len(self.current_ctx)
        before_tokens = self.current_ctx_tokens
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        replacement = self._create_context_entry({
            "sender": "system",
            "content": f"[CONTEXT SUMMARY REFERENCE] Entries {start}-{end} replaced by memory key '{memory_key}'.",
            "timestamp": now,
        })
        replacement["history_index"] = start
        replacement["memory_key"] = memory_key
        replacement["replaces_range"] = [start, end]

        new_ctx = []
        inserted = False
        for pos, entry in enumerate(self.current_ctx):
            if not isinstance(entry, dict):
                entry = self._create_context_entry(entry)
            idx = entry.get("history_index", pos)
            if start <= idx < end:
                if not inserted:
                    new_ctx.append(replacement)
                    inserted = True
                continue
            new_ctx.append(entry)
        if not inserted:
            new_ctx.append(replacement)
            new_ctx.sort(key=lambda e: e.get("history_index", 10**12))

        self._save_context_snapshot()
        self.current_ctx = new_ctx
        self._recompute_current_ctx_tokens()
        self.context_version += 1
        self.last_rebuild_timestamp = now
        return {
            "range": [start, end],
            "memory_key": memory_key,
            "entries_before": before_entries,
            "entries_after": len(self.current_ctx),
            "tokens_before": before_tokens,
            "tokens_after": self.current_ctx_tokens,
        }

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
            "cognitive_load_state": self._cognitive_load_state(utilization),
            "should_rebuild": self.should_rebuild(),
            "rebuild_threshold": self.rebuild_threshold,
            "num_entries": len(self.current_ctx),
            "avg_tokens_per_entry": avg_tokens,
            "rebuild_count": self.rebuild_count,
            "total_appends": self.total_appends,
            "context_version": self.context_version,
            "last_rebuild": self.last_rebuild_timestamp,
            "snapshots_available": len(self.context_history),
            "pinned_entries": len(self.pinned_entries),
            "working_memory_fields": sorted(list(self.working_memory_packet.keys())),
            "context_policy": dict(self.context_policy),
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
            "context_manifest": self.context_manifest,
            "pinned_entries": self.pinned_entries,
            "working_memory_packet": self.working_memory_packet,
            "context_policy": self.context_policy,
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
            self.context_manifest = state.get("context_manifest", self._new_context_manifest())
            self.pinned_entries = state.get("pinned_entries", {})
            self.working_memory_packet = state.get("working_memory_packet", {})
            self.context_policy = self._merge_context_policy(state.get("context_policy", getattr(self, "context_policy", {})))
            if self.working_memory_packet and self.context_policy.get("preserve_working_memory", True):
                self._upsert_working_memory_context_entry()
            
            console.print(f"[CONTEXT] State loaded from {filepath} (version {self.context_version})")
            return True
        except FileNotFoundError:
            console.print(f"[CONTEXT] No saved state found at {filepath}")
            return False
        except Exception as e:
            console.print(f"[CONTEXT] Failed to load state: {e}")
            return False

    # ------ Snapshot and Restore methods ------
    def snapshot(self) -> Dict[str, Any]:
        """Create a snapshot of the current context manager state.
        
        Returns:
            Dict containing all state information needed to restore the instance.
        """
        snapshot_data = {
            "current_ctx": self.current_ctx,
            "current_ctx_tokens": self.current_ctx_tokens,
            "context_version": self.context_version,
            "rebuild_count": self.rebuild_count,
            "total_appends": self.total_appends,
            "context_history": self.context_history,
            "last_rebuild_timestamp": self.last_rebuild_timestamp,
            "context_manifest": self.context_manifest,
            "pinned_entries": self.pinned_entries,
            "working_memory_packet": self.working_memory_packet,
            "context_policy": self.context_policy,
            "max_ctx_tokens": self.max_ctx_tokens,
            "recent_chat_ratio": self.recent_chat_ratio,
            "memory_ratio": self.memory_ratio,
            "trace_ratio": self.trace_ratio,
            "rebuild_threshold": self.rebuild_threshold,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        return snapshot_data

    def restore(self, snapshot_data: Dict[str, Any]) -> None:
        """Restore the context manager state from a snapshot.
        
        Args:
            snapshot_data: Dictionary containing all state information needed to restore the instance.
        """
        # Restore context buffer and metrics
        self.current_ctx = snapshot_data.get("current_ctx", [])
        self.current_ctx_tokens = snapshot_data.get("current_ctx_tokens", 0)
        self.context_version = snapshot_data.get("context_version", 0)
        self.rebuild_count = snapshot_data.get("rebuild_count", 0)
        self.total_appends = snapshot_data.get("total_appends", 0)
        self.context_history = snapshot_data.get("context_history", [])
        self.last_rebuild_timestamp = snapshot_data.get("last_rebuild_timestamp")
        self.context_manifest = snapshot_data.get("context_manifest", self._new_context_manifest())
        self.pinned_entries = snapshot_data.get("pinned_entries", {})
        self.working_memory_packet = snapshot_data.get("working_memory_packet", {})
        self.context_policy = self._merge_context_policy(snapshot_data.get("context_policy", getattr(self, "context_policy", {})))
        if self.working_memory_packet and self.context_policy.get("preserve_working_memory", True):
            self._upsert_working_memory_context_entry()
        
        # Restore configuration (if present)
        if "max_ctx_tokens" in snapshot_data:
            self.max_ctx_tokens = snapshot_data["max_ctx_tokens"]
        if "recent_chat_ratio" in snapshot_data:
            self.recent_chat_ratio = snapshot_data["recent_chat_ratio"]
        if "memory_ratio" in snapshot_data:
            self.memory_ratio = snapshot_data["memory_ratio"]
        if "trace_ratio" in snapshot_data:
            self.trace_ratio = snapshot_data["trace_ratio"]
        if "rebuild_threshold" in snapshot_data:
            self.rebuild_threshold = snapshot_data["rebuild_threshold"]
        
        # Do not write to disk from restore(); loading/resuming a snapshot should
        # not mutate old session files as a side effect.

    def save_snapshot(self, file_path: str) -> None:
        """Save a snapshot to disk.
        
        Args:
            file_path: Path where the snapshot should be saved.
        """
        snapshot_data = self.snapshot()
        
        # Use pickle for backward compatibility with other components
        with open(file_path, 'wb') as f:
            pickle.dump(snapshot_data, f)
        
        console.print(f"[CONTEXT] Snapshot saved to {file_path}")

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
                console.print(f"[CONTEXT] Snapshot loaded from {file_path}")
                return True
            else:
                console.print(f"[CONTEXT] Failed to load snapshot from {file_path}")
                return False
        except FileNotFoundError:
            console.print(f"[CONTEXT] Snapshot file not found: {file_path}")
            return False
        except Exception as e:
            console.print(f"[CONTEXT] Error loading snapshot: {e}")
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
