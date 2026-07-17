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
            infra.context_manager.force_rebuild(recipe=self.payload.recipe)
            ctx_msg = f"[CONTEXT] Forced rebuild completed using recipe: {self.payload.recipe}"
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
