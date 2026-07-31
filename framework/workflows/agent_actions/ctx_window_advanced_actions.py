"""Advanced Context Window Management Actions

This module provides actions to surgically manipulate the active context window
without modifying the permanent chat history record.
"""

import json
from typing import Literal, Dict, Optional, List, Any
from pydantic import BaseModel, Field
from framework.workflows.base_agent_action import AgentAction

# =============================================================================
# CONTEXT WINDOW SURGICAL ACTIONS
# =============================================================================

class TruncateContextWindowArg(BaseModel):
    start_index: int = Field(description="The history index from which to start including entries in the context window")
    purpose: Optional[str] = Field(default=None, description="Reason for truncating the window")

class TruncateContextWindow(AgentAction):
    action: Literal["truncate_context_window"] = "truncate_context_window"
    description: Literal["Slide the context window forward to ignore early history"] = "Slide the context window forward to ignore early history"
    payload: TruncateContextWindowArg
    payload_schema: str = """
    {"start_index": <int>: "The history index to start from",
     "purpose": <Optional<string>>: "Reason for truncation"}
    """

    def execute(self, infra) -> None:
        try:
            # Instruct context_manager to slide the window
            infra.context_manager.set_window_start(self.payload.start_index)
            ctx_msg = f"[CONTEXT] Window truncated. Now starting from index {self.payload.start_index}."
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][CONTEXT] Failed to truncate window: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

class FilterContextWindowArg(BaseModel):
    excluded_categories: List[str] = Field(description="List of entry types or categories to exclude from the active window (e.g., ['system_info', 'debug'])")
    purpose: Optional[str] = Field(default=None, description="Reason for filtering")

class FilterContextWindow(AgentAction):
    action: Literal["filter_context_window"] = "filter_context_window"
    description: Literal["Exclude specific types of noise from the active context window"] = "Exclude specific types of noise from the active context window"
    payload: FilterContextWindowArg
    payload_schema: str = """
    {"excluded_categories": <list[string]>: "Categories to filter out",
     "purpose": <Optional<string>>: "Reason for filtering"}
    """

    def execute(self, infra) -> None:
        try:
            infra.context_manager.set_filter(excluded=self.payload.excluded_categories)
            ctx_msg = f"[CONTEXT] Filter applied. Excluding: {', '.join(self.payload.excluded_categories)}"
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][CONTEXT] Failed to filter window: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

class ForceContextRebuildArg(BaseModel):
    recipe: Optional[str] = Field(default="lean", description="Recipe to use: 'lean' (recent only), 'balanced' (recent + key memories), 'full' (default)")
    purpose: Optional[str] = Field(default=None, description="Reason for forcing a rebuild")

class ForceContextRebuild(AgentAction):
    action: Literal["force_context_rebuild"] = "force_context_rebuild"
    description: Literal["Force an immediate rebuild of the active context window"] = "Force an immediate rebuild of the active context window"
    payload: ForceContextRebuildArg
    payload_schema: str = """
    {"recipe": <Optional<string>>: "Rebuild recipe ('lean', 'balanced', 'full')",
     "purpose": <Optional<string>>: "Reason for rebuild"}
    """

    def execute(self, infra) -> None:
        try:
            result = infra.context_manager.force_rebuild(
                recipe=self.payload.recipe,
                chat_history=infra.chat_manager.CHAT_HISTORY,
                memory_manager=infra.memory_manager,
                verbose=1,
            )
            before = result.get("before", {})
            after = result.get("after", {})
            ctx_msg = (
                f"[CONTEXT] Forced rebuild completed using recipe: {result.get('recipe', self.payload.recipe)}\n"
                f"Purpose: {self.payload.purpose or 'not specified'}\n"
                f"Tokens: {before.get('current_ctx_tokens', 'N/A')} -> {after.get('current_ctx_tokens', 'N/A')}\n"
                f"Utilization: {before.get('utilization_pct', 0):.1f}% -> {after.get('utilization_pct', 0):.1f}%\n"
                f"Entries: {before.get('num_entries', 'N/A')} -> {after.get('num_entries', 'N/A')}\n"
                f"Context version: {after.get('context_version', 'N/A')}"
            )
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][CONTEXT] Failed to force rebuild: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

class SelectiveContextSummarizationArg(BaseModel):
    start_index: int = Field(description="Start index of the range to summarize")
    end_index: int = Field(description="End index of the range to summarize")
    summary_prompt: str = Field(default="Summarize this specific range for the active context.")
    purpose: Optional[str] = Field(default=None, description="Reason for selective summarization")

class SelectiveContextSummarization(AgentAction):
    action: Literal["selective_context_summarization"] = "selective_context_summarization"
    description: Literal["Summarize a range and use the summary instead of raw entries in the window"] = "Summarize a range and use the summary instead of raw entries in the window"
    payload: SelectiveContextSummarizationArg
    payload_schema: str = """
    {"start_index": <int>: "Start of range",
     "end_index": <int>: "End of range",
     "summary_prompt": <string>: "Custom prompt",
     "purpose": <Optional<string>>: "Reason"}
    """

    def execute(self, infra) -> None:
        try:
            # 1. Extract raw text for the range
            history_segment = infra.chat_manager.CHAT_HISTORY[self.payload.start_index : self.payload.end_index]
            segment_text = "\n".join([str(e) for e in history_segment])
            
            # 2. Generate summary
            prompt = f"{self.payload.summary_prompt}\n\nSegment:\n{segment_text}"
            summary = infra.agent.get_chat_response(user_prompt=prompt)
            
            # 3. Store in memory
            key = f"selective_sum_{self.payload.start_index}_{self.payload.end_index}"
            infra.memory_manager.remember(key=key, value=summary, category="context_summaries")
            
            # 4. Tell context manager to use this summary instead of raw entries for this range
            infra.context_manager.replace_range_with_memory(start=self.payload.start_index, end=self.payload.end_index, memory_key=key)
            
            ctx_msg = f"[CONTEXT] Range {self.payload.start_index}-{self.payload.end_index} replaced by summary {key} in active window."
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][CONTEXT] Selective summarization failed: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)


# =============================================================================
# CONTEXT LEDGER / PINNING / POLICY ACTIONS
# =============================================================================

class PinContextEntryArg(BaseModel):
    entry_id: Optional[str] = Field(default=None, description="Active context entry id to pin")
    history_index: Optional[int] = Field(default=None, description="History index of active context entry to pin")
    label: Optional[str] = Field(default=None, description="Short label for the pin")
    reason: Optional[str] = Field(default=None, description="Reason the entry must survive rebuilds")

class PinContextEntry(AgentAction):
    action: Literal["pin_context_entry"] = "pin_context_entry"
    description: Literal["Pin an active context entry so it survives rebuilds"] = "Pin an active context entry so it survives rebuilds"
    payload: PinContextEntryArg
    payload_schema: str = '{"entry_id": <optional string>, "history_index": <optional int>, "label": <optional string>, "reason": <optional string>}'
    def execute(self, infra) -> None:
        try:
            result = infra.context_manager.pin_context_entry(entry_id=self.payload.entry_id, history_index=self.payload.history_index, label=self.payload.label, reason=self.payload.reason)
            infra.append_chat_history(actor="system", content=f"[CONTEXT] Pinned entry: {result}", action={"action": "system_info"}, log_console=True)
        except Exception as e:
            infra.append_chat_history(actor="system", content=f"[ERROR][CONTEXT] Failed to pin entry: {e}", action={"action": "system_error"}, log_console=True)

class UnpinContextEntryArg(BaseModel):
    entry_id: str = Field(description="Pinned context entry id to unpin")
    reason: Optional[str] = Field(default=None, description="Reason for unpinning")

class UnpinContextEntry(AgentAction):
    action: Literal["unpin_context_entry"] = "unpin_context_entry"
    description: Literal["Unpin a context entry"] = "Unpin a context entry"
    payload: UnpinContextEntryArg
    payload_schema: str = '{"entry_id": <string>, "reason": <optional string>}'
    def execute(self, infra) -> None:
        try:
            result = infra.context_manager.unpin_context_entry(self.payload.entry_id)
            infra.append_chat_history(actor="system", content=f"[CONTEXT] Unpinned entry: {result}", action={"action": "system_info"}, log_console=True)
        except Exception as e:
            infra.append_chat_history(actor="system", content=f"[ERROR][CONTEXT] Failed to unpin entry: {e}", action={"action": "system_error"}, log_console=True)

class PromoteContextToMemoryArg(BaseModel):
    start_index: int = Field(description="Start history index, inclusive")
    end_index: int = Field(description="End history index, exclusive")
    category: str = Field(default="session_summaries", description="Memory category to store promoted context")
    key: str = Field(description="Memory key")
    note: Optional[str] = Field(default=None, description="Why this range is being promoted")

class PromoteContextToMemory(AgentAction):
    action: Literal["promote_context_to_memory"] = "promote_context_to_memory"
    description: Literal["Promote an active context range into durable structured memory"] = "Promote an active context range into durable structured memory"
    payload: PromoteContextToMemoryArg
    payload_schema: str = '{"start_index": <int>, "end_index": <int>, "category": <string>, "key": <string>, "note": <optional string>}'
    def execute(self, infra) -> None:
        try:
            result = infra.context_manager.promote_context_to_memory(self.payload.start_index, self.payload.end_index, infra.memory_manager, self.payload.category, self.payload.key, self.payload.note)
            infra.append_chat_history(actor="system", content=f"[CONTEXT] Promoted context to memory: {result}", action={"action": "system_info"}, log_console=True)
        except Exception as e:
            infra.append_chat_history(actor="system", content=f"[ERROR][CONTEXT] Failed to promote context: {e}", action={"action": "system_error"}, log_console=True)

class BuildContextManifestArg(BaseModel):
    include_history_omissions: bool = Field(default=True, description="Compute omitted ranges relative to full chat history")
    purpose: Optional[str] = Field(default=None, description="Reason for building manifest")

class BuildContextManifest(AgentAction):
    action: Literal["build_context_manifest"] = "build_context_manifest"
    description: Literal["Build or refresh the context ledger manifest"] = "Build or refresh the context ledger manifest"
    payload: BuildContextManifestArg
    payload_schema: str = '{"include_history_omissions": <bool>, "purpose": <optional string>}'
    def execute(self, infra) -> None:
        try:
            hist = infra.chat_manager.CHAT_HISTORY if self.payload.include_history_omissions else None
            result = infra.context_manager.build_context_manifest(chat_history=hist)
            infra.append_chat_history(actor="system", content=f"[CONTEXT MANIFEST]\n{json.dumps(result, indent=2, default=str)}", action={"action": "system_info"}, log_console=True)
        except Exception as e:
            infra.append_chat_history(actor="system", content=f"[ERROR][CONTEXT] Failed to build manifest: {e}", action={"action": "system_error"}, log_console=True)

class ListContextManifestArg(BaseModel):
    purpose: Optional[str] = Field(default=None, description="Reason for listing manifest")

class ListContextManifest(AgentAction):
    action: Literal["list_context_manifest"] = "list_context_manifest"
    description: Literal["List the current context ledger manifest"] = "List the current context ledger manifest"
    payload: ListContextManifestArg
    payload_schema: str = '{"purpose": <optional string>}'
    def execute(self, infra) -> None:
        try:
            result = infra.context_manager.list_context_manifest()
            infra.append_chat_history(actor="system", content=f"[CONTEXT MANIFEST]\n{json.dumps(result, indent=2, default=str)}", action={"action": "system_info"}, log_console=True)
        except Exception as e:
            infra.append_chat_history(actor="system", content=f"[ERROR][CONTEXT] Failed to list manifest: {e}", action={"action": "system_error"}, log_console=True)

class AuditContextIntegrityArg(BaseModel):
    purpose: Optional[str] = Field(default=None, description="Reason for audit")

class AuditContextIntegrity(AgentAction):
    action: Literal["audit_context_integrity"] = "audit_context_integrity"
    description: Literal["Audit whether active context preserves required working state"] = "Audit whether active context preserves required working state"
    payload: AuditContextIntegrityArg
    payload_schema: str = '{"purpose": <optional string>}'
    def execute(self, infra) -> None:
        try:
            result = infra.context_manager.audit_context_integrity()
            infra.append_chat_history(actor="system", content=f"[CONTEXT AUDIT]\n{json.dumps(result, indent=2, default=str)}", action={"action": "system_info"}, log_console=True)
        except Exception as e:
            infra.append_chat_history(actor="system", content=f"[ERROR][CONTEXT] Failed to audit context: {e}", action={"action": "system_error"}, log_console=True)

class SetContextPolicyArg(BaseModel):
    profile: Optional[str] = Field(default=None, description="Profile such as general/debugging/writing/data_analysis/infrastructure")
    auto_rebuild_enabled: Optional[bool] = Field(default=None, description="Enable automatic threshold rebuild")
    rebuild_threshold: Optional[float] = Field(default=None, description="Emergency rebuild threshold")
    target_utilization: Optional[float] = Field(default=None, description="Preferred post-rebuild utilization")
    preserve_pinned: Optional[bool] = Field(default=None, description="Preserve pinned entries during rebuild")
    preserve_working_memory: Optional[bool] = Field(default=None, description="Preserve working memory packet during rebuild")
    retrieval_hints: Optional[List[str]] = Field(default=None, description="Hints for future semantic retrieval")
    purpose: Optional[str] = Field(default=None, description="Reason for policy update")

class SetContextPolicy(AgentAction):
    action: Literal["set_context_policy"] = "set_context_policy"
    description: Literal["Set context-management policy/profile metadata"] = "Set context-management policy/profile metadata"
    payload: SetContextPolicyArg
    payload_schema: str = '{"profile": <optional string>, "auto_rebuild_enabled": <optional bool>, "rebuild_threshold": <optional float>, "target_utilization": <optional float>, "preserve_pinned": <optional bool>, "preserve_working_memory": <optional bool>, "retrieval_hints": <optional list[string]>}'
    def execute(self, infra) -> None:
        try:
            result = infra.context_manager.set_context_policy(**self.payload.model_dump(exclude={"purpose"}))
            infra.append_chat_history(actor="system", content=f"[CONTEXT POLICY]\n{json.dumps(result, indent=2, default=str)}", action={"action": "system_info"}, log_console=True)
        except Exception as e:
            infra.append_chat_history(actor="system", content=f"[ERROR][CONTEXT] Failed to set policy: {e}", action={"action": "system_error"}, log_console=True)

class UpdateWorkingMemoryPacketArg(BaseModel):
    current_objective: Optional[str] = None
    current_plan: Optional[str] = None
    current_step: Optional[str] = None
    active_files: Optional[List[str]] = None
    modified_files: Optional[List[str]] = None
    open_tasks: Optional[List[str]] = None
    open_questions: Optional[List[str]] = None
    decisions: Optional[List[str]] = None
    known_bugs_warnings: Optional[List[str]] = None
    last_successful_action: Optional[str] = None
    next_recommended_action: Optional[str] = None
    purpose: Optional[str] = None

class UpdateWorkingMemoryPacket(AgentAction):
    action: Literal["update_working_memory_packet"] = "update_working_memory_packet"
    description: Literal["Update the compact working memory packet kept in active context"] = "Update the compact working memory packet kept in active context"
    payload: UpdateWorkingMemoryPacketArg
    payload_schema: str = '{"current_objective": <optional string>, "current_plan": <optional string>, "current_step": <optional string>, "active_files": <optional list>, "modified_files": <optional list>, "open_tasks": <optional list>, "open_questions": <optional list>, "decisions": <optional list>, "known_bugs_warnings": <optional list>, "last_successful_action": <optional string>, "next_recommended_action": <optional string>}'
    def execute(self, infra) -> None:
        try:
            data = self.payload.model_dump(exclude={"purpose"}, exclude_none=True)
            result = infra.context_manager.update_working_memory_packet(**data)
            infra.append_chat_history(actor="system", content=f"[WORKING MEMORY UPDATED]\n{json.dumps(result, indent=2, default=str)}", action={"action": "system_info"}, log_console=True)
        except Exception as e:
            infra.append_chat_history(actor="system", content=f"[ERROR][CONTEXT] Failed to update working memory: {e}", action={"action": "system_error"}, log_console=True)

class RecallByMemoryKeyArg(BaseModel):
    category: str = Field(description="Memory category")
    key: str = Field(description="Memory key")
    purpose: Optional[str] = Field(default=None, description="Reason for recall")

class RecallByMemoryKey(AgentAction):
    action: Literal["recall_by_memory_key"] = "recall_by_memory_key"
    description: Literal["Recall a durable memory fragment by category/key"] = "Recall a durable memory fragment by category/key"
    payload: RecallByMemoryKeyArg
    payload_schema: str = '{"category": <string>, "key": <string>, "purpose": <optional string>}'
    def execute(self, infra) -> None:
        try:
            result = infra.memory_manager.recall(key=self.payload.key, category=self.payload.category)
            infra.append_chat_history(actor="system", content=f"[MEMORY RECALL] {self.payload.category}/{self.payload.key}\n{json.dumps(result, indent=2, default=str)}", action={"action": "system_info"}, log_console=True)
        except Exception as e:
            infra.append_chat_history(actor="system", content=f"[ERROR][MEMORY] Failed to recall by key: {e}", action={"action": "system_error"}, log_console=True)

class RecallCompressedRangeArg(BaseModel):
    memory_key: str = Field(description="Memory key created by a summary/compression action")
    category: str = Field(default="context_summaries", description="Memory category containing the compressed range")
    purpose: Optional[str] = Field(default=None, description="Reason for recall")

class RecallCompressedRange(AgentAction):
    action: Literal["recall_compressed_range"] = "recall_compressed_range"
    description: Literal["Recall a compressed/summarized context range from memory"] = "Recall a compressed/summarized context range from memory"
    payload: RecallCompressedRangeArg
    payload_schema: str = '{"memory_key": <string>, "category": <optional string>, "purpose": <optional string>}'
    def execute(self, infra) -> None:
        try:
            result = infra.memory_manager.recall(key=self.payload.memory_key, category=self.payload.category)
            infra.append_chat_history(actor="system", content=f"[COMPRESSED RANGE RECALL] {self.payload.category}/{self.payload.memory_key}\n{json.dumps(result, indent=2, default=str)}", action={"action": "system_info"}, log_console=True)
        except Exception as e:
            infra.append_chat_history(actor="system", content=f"[ERROR][MEMORY] Failed to recall compressed range: {e}", action={"action": "system_error"}, log_console=True)
