"""Memory and Context Management Actions

This module provides atomic actions that allow agents to proactively manage
memory and context windows. These actions enable:
1. Memory fragment creation and management
2. Context window monitoring and optimization
3. Strategic summarization and compression of chat history
4. Memory retrieval and querying
"""

import json
from typing import Literal, Dict, Optional, List, Any
from pydantic import BaseModel, Field
from framework.workflows.base_agent_action import AgentAction
from framework.utils.io_tools import console

# =============================================================================
# MEMORY MANAGEMENT ACTIONS
# =============================================================================

class CreateMemoryFragmentArg(BaseModel):
    category: str = Field(description="Category of memory fragment: 'user_prefs', 'warnings', 'strategies', 'decisions', 'conclusions', 'solutions', 'facts', etc.")
    key: str = Field(description="Unique key/identifier for the memory fragment")
    value: Any = Field(description="The memory content to store (can be string, dict, list, etc.)")
    purpose: Optional[str] = Field(default=None, description="Short description of why this memory is being created")

class CreateMemoryFragment(AgentAction):
    action: Literal["create_memory_fragment"] = "create_memory_fragment"
    description: Literal["Create and store a new memory fragment"] = "Create and store a new memory fragment"
    payload: CreateMemoryFragmentArg
    payload_schema: str = """
    {"category": <string>: "Category of memory fragment (e.g., 'user_prefs', 'warnings', 'decisions', etc.)",
     "key": <string>: "Unique key/identifier for the memory fragment",
     "value": <Any>: "The memory content to store",
     "purpose": <Optional<string>>: "Short description of why this memory is being created"
    }
    """

    def execute(self, infra) -> None:
        category = self.payload.category.strip().lower()
        key = self.payload.key
        value = self.payload.value
        purpose = self.payload.purpose or "Memory storage"
        
        try:
            infra.memory_manager.remember(key, value, category=category)
            ctx_msg = f"[MEMORY] Created memory fragment in category '{category}' with key '{key}'"
            if purpose:
                ctx_msg += f"\nPurpose: {purpose}"
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][MEMORY] Failed to create memory fragment: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

class RecallMemoryArg(BaseModel):
    category: Optional[str] = Field(default=None, description="Category to recall from (None = all categories)")
    key: Optional[str] = Field(default=None, description="Specific key to recall (None = all keys in category)")
    purpose: Optional[str] = Field(default=None, description="Short description of the intent")

class RecallMemory(AgentAction):
    action: Literal["recall_memory"] = "recall_memory"
    description: Literal["Recall stored memory fragments"] = "Recall stored memory fragments"
    payload: RecallMemoryArg
    payload_schema: str = """
    {"category": <Optional<string>>: "Category to recall from (None = all categories)",
     "key": <Optional<string>>: "Specific key to recall (None = all keys in category)",
     "purpose": <Optional<string>>: "Short description of the intent"
    }
    """

    def execute(self, infra) -> None:
        category = self.payload.category
        key = self.payload.key
        try:
            if category:
                memory_data = infra.memory_manager.recall(key=key, category=category)
                ctx_msg = f"[MEMORY] Recalled from category '{category}'"
                if key:
                    ctx_msg += f", key '{key}'"
            else:
                memory_data = infra.memory_manager.recall()
                ctx_msg = "[MEMORY] Recalled all memory fragments"
            ctx_msg += f"\n{memory_data}"
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][MEMORY] Failed to recall memory: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

class ForgetMemoryArg(BaseModel):
    category: str = Field(description="Category containing the memory to forget")
    key: str = Field(description="Key of the memory fragment to forget")
    purpose: Optional[str] = Field(default=None, description="Short description of why forgetting this memory")

class ForgetMemory(AgentAction):
    action: Literal["forget_memory"] = "forget_memory"
    description: Literal["Delete a specific memory fragment"] = "Delete a specific memory fragment"
    payload: ForgetMemoryArg
    payload_schema: str = """
    {"category": <string>: "Category containing the memory to forget",
     "key": <string>: "Key of the memory fragment to forget",
     "purpose": <Optional<string>>: "Short description of why forgetting this memory"
    }
    """

    def execute(self, infra) -> None:
        category = self.payload.category.strip().lower()
        key = self.payload.key
        try:
            infra.memory_manager.forget(key, category=category)
            ctx_msg = f"[MEMORY] Forgot memory fragment '{key}' from category '{category}'"
            if self.payload.purpose:
                ctx_msg += f"\nReason: {self.payload.purpose}"
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][MEMORY] Failed to forget memory: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

class ClearMemoryCategoryArg(BaseModel):
    category: str = Field(description="Category to clear completely")
    confirm: bool = Field(default=False, description="Confirmation flag (must be True to execute)")
    purpose: Optional[str] = Field(default=None, description="Short description of why clearing this category")

class ClearMemoryCategory(AgentAction):
    action: Literal["clear_memory_category"] = "clear_memory_category"
    description: Literal["Clear all memories in a specific category"] = "Clear all memories in a specific category"
    payload: ClearMemoryCategoryArg
    payload_schema: str = """
    {"category": <string>: "Category to clear completely",
     "confirm": <bool>: "Confirmation flag (must be True to execute)",
     "purpose": <Optional<string>>: "Short description of why clearing this category"
    }
    """

    def execute(self, infra) -> None:
        if not self.payload.confirm:
            warn_msg = "[WARN][MEMORY] Clear operation requires 'confirm': true to execute"
            infra.append_chat_history(actor="system", content=warn_msg, action={"action": "system_warn"}, log_console=True)
            return
        category = self.payload.category.strip().lower()
        try:
            infra.memory_manager.clear(category=category)
            ctx_msg = f"[MEMORY] Cleared all memories from category '{category}'"
            if self.payload.purpose:
                ctx_msg += f"\nReason: {self.payload.purpose}"
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][MEMORY] Failed to clear category: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)


class ListMemoryCategoriesArg(BaseModel):
    purpose: Optional[str] = Field(default=None, description="Short description of why categories are being listed")

class ListMemoryCategories(AgentAction):
    """List all memory categories currently stored in the memory manager.
    This action does not require any input parameters besides an optional purpose.
    """
    action: Literal["list_memory_categories"] = "list_memory_categories"
    description: Literal["List all memory categories"] = "List all memory categories"
    payload: ListMemoryCategoriesArg
    payload_schema: str = """
    {"purpose": <Optional<string>>: "Short description of why categories are being listed"}
    """

    def execute(self, infra) -> None:
        try:
            categories = infra.memory_manager.list_categories()
            ctx_msg = "[MEMORY] Available categories: " + ", ".join(categories)
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][MEMORY] Failed to list categories: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

class BatchForgetMemoryArg(BaseModel):
    category: Optional[str] = Field(default=None, description="Category to target (None = all categories)")
    key_pattern: Optional[str] = Field(default=None, description="Glob-style pattern to match keys (e.g., 'session_*')")
    confirm: bool = Field(default=False, description="Confirmation flag (must be True to execute)")
    purpose: Optional[str] = Field(default=None, description="Why the batch forget is performed")

class BatchForgetMemory(AgentAction):
    """Forget multiple memory fragments matching a pattern.
    If category is None, the pattern is applied across all categories.
    """
    action: Literal["batch_forget_memory"] = "batch_forget_memory"
    description: Literal["Batch delete memory fragments"] = "Batch delete memory fragments"
    payload: BatchForgetMemoryArg
    payload_schema: str = """
    {"category": <Optional<string>>: "Category to target (None = all categories)",
     "key_pattern": <Optional<string>>: "Glob-style pattern to match keys",
     "confirm": <bool>: "Confirmation flag (must be True to execute)",
     "purpose": <Optional<string>>: "Why the batch forget is performed"}
    """

    def execute(self, infra) -> None:
        if not self.payload.confirm:
            warn_msg = "[WARN][MEMORY] Batch forget requires 'confirm': true to execute"
            infra.append_chat_history(actor="system", content=warn_msg, action={"action": "system_warn"}, log_console=True)
            return
        try:
            deleted = infra.memory_manager.batch_forget(
                category=self.payload.category,
                key_pattern=self.payload.key_pattern
            )
            ctx_msg = f"[MEMORY] Batch forget completed. Deleted keys: {deleted}"
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][MEMORY] Failed batch forget: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

class RenameMemoryCategoryArg(BaseModel):
    old_category: str = Field(description="Existing category name to rename")
    new_category: str = Field(description="New category name")
    confirm: bool = Field(default=False, description="Confirmation flag (must be True to execute)")
    purpose: Optional[str] = Field(default=None, description="Why the category is being renamed")

class RenameMemoryCategory(AgentAction):
    """Rename an existing memory category.
    All fragments move from old_category to new_category.
    """
    action: Literal["rename_memory_category"] = "rename_memory_category"
    description: Literal["Rename a memory category"] = "Rename a memory category"
    payload: RenameMemoryCategoryArg
    payload_schema: str = """
    {"old_category": <string>: "Existing category name to rename",
     "new_category": <string>: "New category name",
     "confirm": <bool>: "Confirmation flag (must be True to execute)",
     "purpose": <Optional<string>>: "Why the category is being renamed"}
    """

    def execute(self, infra) -> None:
        if not self.payload.confirm:
            warn_msg = "[WARN][MEMORY] Rename operation requires 'confirm': true to execute"
            infra.append_chat_history(actor="system", content=warn_msg, action={"action": "system_warn"}, log_console=True)
            return
        try:
            infra.memory_manager.rename_category(self.payload.old_category, self.payload.new_category)
            ctx_msg = f"[MEMORY] Renamed category '{self.payload.old_category}' to '{self.payload.new_category}'"
            if self.payload.purpose:
                ctx_msg += f"\nPurpose: {self.payload.purpose}"
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][MEMORY] Failed to rename category: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

# =============================================================================
# CONTEXT WINDOW MANAGEMENT ACTIONS (unchanged)
# =============================================================================

class CheckContextUtilizationArg(BaseModel):
    report_details: bool = Field(default=True, description="Include detailed breakdown of token usage")
    purpose: Optional[str] = Field(default=None, description="Short description of the intent")

class CheckContextUtilization(AgentAction):
    action: Literal["check_context_utilization"] = "check_context_utilization"
    description: Literal["Check current context window utilization and get diagnostics"] = "Check current context window utilization and get diagnostics"
    payload: CheckContextUtilizationArg
    payload_schema: str = """
    {"report_details": <bool>: "Include detailed breakdown of token usage (default: True)",
     "purpose": <Optional<string>>: "Short description of the intent"}
    """

    def execute(self, infra) -> None:
        try:
            diagnostics = infra.context_manager.get_context_diagnostics()
            ctx_msg = f"[CONTEXT UTILIZATION REPORT]\n {diagnostics} \n"
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][CONTEXT] Failed to check context utilization: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

class OptimizeContextWindowArg(BaseModel):
    strategy: str = Field(
        default="balanced",
        description="Optimization strategy: 'aggressive' (max compression), 'balanced' (moderate), 'conservative' (minimal)"
    )
    target_utilization: float = Field(
        default=0.6,
        description="Target utilization percentage (0.0-1.0) after optimization"
    )
    preserve_recent: int = Field(
        default=10,
        description="Number of recent chat entries to always preserve"
    )
    purpose: Optional[str] = Field(default=None, description="Short description of the intent")

class OptimizeContextWindow(AgentAction):
    action: Literal["optimize_context_window"] = "optimize_context_window"
    description: Literal["Optimize context window by summarizing and compressing older entries"] = "Optimize context window by summarizing and compressing older entries"
    payload: OptimizeContextWindowArg
    payload_schema: str = """
    {"strategy": <string>: "Optimization strategy: 'aggressive', 'balanced', or 'conservative' (default: 'balanced')",
     "target_utilization": <float>: "Target utilization percentage (0.0-1.0) after optimization (default: 0.6)",
     "preserve_recent": <int>: "Number of recent chat entries to always preserve (default: 10)",
     "purpose": <Optional<string>>: "Short description of the intent"}
    """

    def execute(self, infra) -> None:
        try:
            strategy = self.payload.strategy.lower()
            target_util = self.payload.target_utilization
            preserve_recent = self.payload.preserve_recent
            if strategy not in ["aggressive", "balanced", "conservative"]:
                raise ValueError(f"Invalid strategy: {strategy}")
            if not 0.0 <= target_util <= 1.0:
                raise ValueError(f"target_utilization must be between 0.0 and 1.0")
            total_tokens = infra.FULL_CTX_TOKENS
            max_tokens = infra.context_manager.max_ctx_tokens
            current_util = total_tokens / max_tokens if max_tokens > 0 else 0
            ctx_msg = f"[CONTEXT OPTIMIZATION]\nCurrent utilization: {current_util*100:.1f}%\nTarget utilization: {target_util*100:.1f}%\nStrategy: {strategy}\n"
            if current_util <= target_util:
                ctx_msg += "\nNo optimization needed - utilization is already at or below target."
                infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
                return
            total_entries = len(infra.chat_manager.CHAT_HISTORY)
            if strategy == "aggressive":
                entries_to_summarize = total_entries - preserve_recent
            elif strategy == "balanced":
                entries_to_summarize = int((total_entries - preserve_recent) * 0.6)
            else:
                entries_to_summarize = int((total_entries - preserve_recent) * 0.3)
            if entries_to_summarize <= 0:
                ctx_msg += f"\nInsufficient entries to optimize (only {total_entries} entries, preserving {preserve_recent})."
                infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
                return
            start_idx = 0
            end_idx = entries_to_summarize
            summary_prompt = (
                f"Summarize the following chat history segment into a concise, information-dense summary. "
                f"Preserve key facts, decisions, user preferences, and important context. "
                f"Strategy: {strategy} compression.\n\n"
            )
            ctx_msg += f"\nSummarizing {entries_to_summarize} entries (from index {start_idx} to {end_idx})..."
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
            follow_up_msg = (
                f"[NEXT STEP] Use 'summarize_context' action with the following parameters:\n"
                f"  - summary_prompt: '{summary_prompt}'\n"
                f"  - This will compress the context window as requested."
            )
            infra.append_chat_history(actor="system", content=follow_up_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][CONTEXT] Failed to optimize context window: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)

class SetContextMonitoringArg(BaseModel):
    enabled: bool = Field(description="Enable or disable automatic context monitoring")
    warning_threshold: float = Field(
        default=0.75,
        description="Utilization threshold (0.0-1.0) at which to issue warnings"
    )
    critical_threshold: float = Field(
        default=0.90,
        description="Utilization threshold (0.0-1.0) at which to issue critical alerts"
    )
    purpose: Optional[str] = Field(default=None, description="Short description of the intent")

class SetContextMonitoring(AgentAction):
    action: Literal["set_context_monitoring"] = "set_context_monitoring"
    description: Literal["Configure automatic context window monitoring and alerts"] = "Configure automatic context window monitoring and alerts"
    payload: SetContextMonitoringArg
    payload_schema: str = """
    {"enabled": <bool>: "Enable or disable automatic context monitoring",
     "warning_threshold": <float>: "Utilization threshold (0.0-1.0) for warnings (default: 0.75)",
     "critical_threshold": <float>: "Utilization threshold (0.0-1.0) for critical alerts (default: 0.90)",
     "purpose": <Optional<string>>: "Short description of the intent"}
    """

    def execute(self, infra) -> None:
        try:
            if not hasattr(infra, 'context_monitoring'):
                infra.context_monitoring = {}
            infra.context_monitoring['enabled'] = self.payload.enabled
            infra.context_monitoring['warning_threshold'] = self.payload.warning_threshold
            infra.context_monitoring['critical_threshold'] = self.payload.critical_threshold
            status = "ENABLED" if self.payload.enabled else "DISABLED"
            ctx_msg = f"[CONTEXT MONITORING] Status: {status}\n"
            if self.payload.enabled:
                ctx_msg += f"Warning threshold: {self.payload.warning_threshold*100:.0f}%\n"
                ctx_msg += f"Critical threshold: {self.payload.critical_threshold*100:.0f}%\n"
                ctx_msg += "\nThe system will automatically alert when thresholds are exceeded."
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][CONTEXT] Failed to set context monitoring: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)


class SummarizeContextActionArgs(BaseModel):
    """Optional arguments for the summarisation action.
    *summary_prompt*: a custom prompt to guide the LLM summarisation (optional).
    """
    summary_prompt: str = "Summarize the older part of the chat history and store the result in memory."

class SummarizeContextAction(AgentAction):
    action: Literal["summarize_context"] = "summarize_context"
    description: Literal["Summarize older chat entries and store the summary in memory"] = "Summarize older chat entries and store the summary in memory"
    payload: SummarizeContextActionArgs
    payload_schema: str = """
    {"summary_prompt": <string>: "Custom prompt guiding the summarisation (optional)"}
    """

    def execute(self, infra: Any = None) -> Any:
        """Execute the summarisation action.
        It retrieves the recent chat history from the ContextManager, generates a summary using the agent's
        structured output capability (or falls back to a simple placeholder), and stores the summary in the
        MemoryManager under a dedicated category (e.g., 'summaries').
        """
        # Retrieve recent chat entries (placeholder – actual implementation depends on infra details)
        chat_history = infra.context_manager.build_context(
            chat_history=infra.chat_manager.CHAT_HISTORY,
            memory_manager=infra.memory_manager,
            recent_chat_budget=infra.context_manager.max_ctx_tokens,
            memory_budget=0,
            trace_budget=0,
        )[0]
        # Prepare a prompt for summarisation (could be refined later)
        prompt = f"{self.payload.summary_prompt}\n\nChat snippet:\n{chat_history}"
        # Use the agent to generate a summary
        if hasattr(infra.agent, "get_structured_output"):
            summary_response = infra.agent.get_structured_output(user_prompt=prompt, output_format=str)
        else:
            summary_response = infra.agent.get_chat_response(user_prompt=prompt)
        summary = summary_response if isinstance(summary_response, str) else json.dumps(summary_response)
        # Store the summary in memory
        infra.memory_manager.remember(key=f"summary_{infra.memory_manager._last_indexed_entry_idx}",
                                      value=summary,
                                      category="summaries")
        # Log the action
        infra.append_chat_history(
            actor="system",
            content=f"[SUMMARY] Generated summary and stored in memory: {summary}",
            action={"action": "system_info"},
            log_console=True,
        )
        return


# =============================================================================
# SEMANTIC SEARCH ACTIONS
# =============================================================================

class SemanticRecallArg(BaseModel):
    query: str = Field(description="Search query for semantic recall")
    source: str = Field(
        default="traces",
        description="Source to search: 'traces' (raw chat) or 'summaries' (compressed history)"
    )
    n_results: int = Field(default=5, description="Number of results to return")
    category: Optional[str] = Field(default=None, description="Optional category filter")
    purpose: Optional[str] = Field(default=None, description="Short description of the intent")

class SemanticRecall(AgentAction):
    action: Literal["semantic_recall"] = "semantic_recall"
    description: Literal["Perform semantic search on chat history or memory"] = "Perform semantic search on chat history or memory"
    payload: SemanticRecallArg
    payload_schema: str = """
    {"query": <string>: "Search query for semantic recall",
     "source": <string>: "Source to search: 'traces' or 'summaries' (default: 'traces')",
     "n_results": <int>: "Number of results to return (default: 5)",
     "category": <Optional<string>>: "Optional category filter",
     "purpose": <Optional<string>>: "Short description of the intent"}
    """

    def execute(self, infra) -> None:
        try:
            results = infra.memory_manager.semantic_recall(
                query=self.payload.query,
                category=self.payload.category,
                n_results=self.payload.n_results,
                source=self.payload.source,
                verbose=1
            )
            ctx_msg = f"[SEMANTIC RECALL]\nQuery: '{self.payload.query}'\nSource: {self.payload.source}\nResults found: {len(results)}\n\n"
            if results:
                for idx, result in enumerate(results, 1):
                    if isinstance(result, dict):
                        doc = result.get('document', str(result))
                        score = result.get('score', 'N/A')
                        ctx_msg += f"{idx}. [Score: {score}] {doc}\n"
                    else:
                        ctx_msg += f"{idx}. {result}\n"
            else:
                ctx_msg += "No results found."
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
        except Exception as e:
            error_msg = f"[ERROR][SEMANTIC RECALL] Failed to perform semantic recall: {e}"
            infra.append_chat_history(actor="system", content=error_msg, action={"action": "system_error"}, log_console=True)
