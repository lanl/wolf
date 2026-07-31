import copy
import os
import logging
import pickle
import subprocess
import shlex
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from framework.utils.io_tools import console, jsonfy, expand_dict
from framework.utils.tokenomics import (
    num_tokens_from_string,
    num_tokens_chat_entry,
)

from framework.data_store.data_models import BaseVectorStoreParams 
from framework.knowledgebase.data_models import KnowledgeBaseParams, MultimodalKnowledgeBaseParams
from framework.knowledgebase.knowledge_base import  KnowledgeBase
from framework.knowledgebase.base_multimodal_knowledgebase import MultimodalKnowledgeBase
from framework.tooling.toolbox import ToolBox
from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams
from framework.universes.base_universe import BaseUniverse
from framework.utils.multimodal_input import MultimodalInputProcessor, MultimodalInputConfig

class BaseInfrastructure:
    """Provides non-workflow-specific functionality with integrated managers."""

    def __init__(
        self,
        agent: Any,
        workers: List[Any] = [],
        objects: List[Any] = [],
        max_ctx_tokens: int = 100,
        wf_log_dir: str = "wf_logs",
        session_dir: str = "./",
        chat_block_divider: str = "/" * 120,
        schema_string: str | None = None,
        chat_manager: Any = None,
        memory_manager: Any = None,
        context_manager: Any = None,
        traces_vector_store: Any = None,
        summaries_vector_store: Any = None,
        db_client: Any = None,
        infra_description_file = "framework/infrastructure/config/base_infra_description.md",
        input_processor: Any = None,
        input_processor_config: Any = None
    ):
        # Support session_dir as primary, fall back to wf_log_dir for backwards compatibility
        self.session_dir = session_dir.strip().rstrip("/")
        self.log_dir = f"{self.session_dir}/{wf_log_dir.strip().rstrip('/')}"
        self.db_client = db_client
        # Reusable user-input processing service. Workflows can opt in without
        # owning parsing/classification/provider-adapter logic.
        if input_processor is not None:
            self.input_processor = input_processor
        else:
            if input_processor_config is None:
                input_processor_config = MultimodalInputConfig(root_dir=str(Path.cwd()))
            self.input_processor = MultimodalInputProcessor(config=input_processor_config)
        self.pending_user_input_bundle = None
        self.pending_agent_content = None
        # Store basic parameters
        self.agent = agent
        self.max_ctx_tokens = max_ctx_tokens
        self.chat_block_divider = chat_block_divider
        if schema_string is None:
            from framework.workflows.workflow_models import SCHEMA_STRING
            self.SCHEMA_STRING = copy.deepcopy(SCHEMA_STRING)
        else:
            self.SCHEMA_STRING = copy.deepcopy(schema_string)
        print(f"[+] Tokens(SCHEMA_STRING) = {num_tokens_from_string(self.SCHEMA_STRING)}")

        # Initialize objects attribute early to avoid AttributeError
        self.objects = objects

        # Role handling
        self.ROLEs = {"system": "system", "sys": "system", f"{self.agent.name}": "assistant"}
        self.WF_MEMBERS = ["system", self.agent.name]
        self.WF_ASSISTANTS = [self.agent.name]
        self.workers = {}
        self.workers_names = []
        for ag in workers:
            if ag.name == self.agent.name:
                raise Exception(
                    f"[!][BaseInfrastructure][__init__]: worker agent {ag.name} has the same name as main agent"
                )
            if ag.name in self.workers_names:
                raise Exception(
                    f"[!][BaseInfrastructure][__init__]: Duplicate worker agent name {ag.name} found. Workers must have unique names"
                )
            self.workers_names.append(ag.name)
            self.WF_MEMBERS.append(ag.name)
            self.WF_ASSISTANTS.append(ag.name)
            self.workers[ag.name] = ag
            self.ROLEs[ag.name] = "assistant"
        self.KBs, self.TBs, self.UNIVs = {},{},{}
        # Initialize managed_deployments to track universe, KB, TB deployments
        self.managed_deployments = {}

        # Initialize Objects
        for obj in self.objects:
            obj_type = None
            if isinstance(obj, BaseUniverse):
                self.UNIVs[obj.name] = BaseUniverseParams(info=obj)
                obj_type = "universe"
            elif isinstance(obj, BaseUniverseParams):
                univ_info = obj.info
                self.UNIVs[univ_info.name] = obj
                obj_type = "universe"
            elif isinstance(obj, KnowledgeBase):
                self.KBs[obj.name] = obj
                obj_type = "knowledgebase"
            elif isinstance(obj, KnowledgeBaseParams):
                self.KBs[obj.name] = KnowledgeBase(obj)
                obj_type = "knowledgebase"
            elif isinstance(obj, MultimodalKnowledgeBase):
                self.KBs[obj.name] = obj
                obj_type = "knowledgebase"
            elif isinstance(obj, MultimodalKnowledgeBaseParams):
                self.KBs[obj.name] = MultimodalKnowledgeBase(obj, db_client=self.db_client)
                obj_type = "knowledgebase"
            elif isinstance(obj, ToolBox):
                self.TBs[obj.name] = obj
                obj_type = "toolbox"
            if obj_type is None:
                try:
                    obj_type = obj.type.lower()
                except Exception as ee:
                    print(f"type obj = {type(obj_type)}: ee = {ee}")
                    obj_type = "undefined"
            mapping = {
                "knowledge": "knowledgebase",
                "knowledgebase": "knowledgebase",
                "tool": "tool",
                "toolbox": "tool",
                "playbook": "playbook",
                "playbook_archive": "playbook_archive",
                "note": "note",
                "model": "model",
                "indexer": "indexer",
                "actionbox": "actionbox",
                "universe": "universe",
                "frame": "frame",
                "undefined":"undefined",
            }
            if obj_type in mapping:
                try:
                    self.ROLEs[obj.name] = mapping[obj_type]
                except:
                    pass
            else:
                raise Exception(
                    f"[!][BaseInfrastructure][__init__]: Object of type {obj_type} is not supported."
                )
        self.NON_SYS_ROLES = ["user", "assistant"]

        # Initialize managers (or use defaults if not provided)
        if chat_manager is not None:
            self.chat_manager = chat_manager
        else:
            from framework.infrastructure.base_chat_manager import BaseChatManager 
            self.chat_manager = BaseChatManager(
                session_dir=self.session_dir,
                chat_block_divider=chat_block_divider
            )

        if memory_manager is not None:
            self.memory_manager = memory_manager
        else:
            from framework.infrastructure.base_memory_manager import MemoryManager 
            self.memory_manager = MemoryManager(
                session_dir=self.session_dir,
                traces_vector_store=traces_vector_store,
                summaries_vector_store=summaries_vector_store
            )

        if context_manager is not None:
            self.context_manager = context_manager
        else:
            from framework.infrastructure.base_context_manager import ContextManager 
            self.context_manager = ContextManager(
                max_ctx_tokens=max_ctx_tokens,
                traces_vector_store=traces_vector_store,
                session_dir=self.session_dir
            )

        # Internal state
        self.FULL_CTX: List[dict] = []
        self.FULL_CTX_TOKENS = 0
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.FULL_CTX = [
            {"role": "system", "actor": "system", "content": self.chat_block_divider, "timestamp": "+", "action": None},
            {"role": "system", "actor": "system", "content": "----- Begining of Worflow (WF) Chat history ----", "timestamp": timestamp, "action": None},
            {"role": "system", "actor": "system", "content": "You are helpful agent(s), and your role is to help users accomplish their tasks", "timestamp": timestamp, "action": None},
            {"role": "system", "actor": "system", "content": f"Agent {self.agent.name}, is the main agent and in charge of managing this workflow", "timestamp": timestamp, "action": None},
        ]
        if self.workers_names:
            self.FULL_CTX.append(
                {
                    "role": "system",
                    "actor": "system",
                    "content": f"Agent(s) {self.workers_names} are workers,\n and their role is to support the user and agent {self.agent.name} orchestrate the workflow",
                    "timestamp": timestamp,
                    "action": None,
                }
            )
            self.FULL_CTX.append({"role": "system", "actor": "system", "content": self.chat_block_divider, "timestamp": "+", "action": None})

        # Build chat_history and CTX from chat_manager and FULL_CTX
        self.chat_history: List[dict] = []
        self.CTX = ""
        self.rebuild_chat_history()

        self.HEADER = copy.deepcopy(self.CTX)
        self.HEADER_IDX = len(self.chat_history)
        self.CONSOLE_HEAD = 0
        self.infra_description_file = infra_description_file
        self.INFRA_DESCRIPTION = ''
        self.update_infra_description(infra_description_file)

    # ------ Helper / utility methods ------

    def update_infra_description(self, infra_description_file: str|None =None):
        if infra_description_file is not None:
            self.infra_description_file = infra_description_file
        else:
            if self.infra_description_file is None:
                self.INFRA_DESCRIPTION = ''
                return
        with open(self.infra_description_file, "r") as f: self.INFRA_DESCRIPTION = f.read()


    def rebuild_chat_history(self, starting_from_line: int = 0):
        """Rebuild chat_history and CTX from FULL_CTX and chat_manager."""
        if starting_from_line > 0:
            chat_history = self.chat_history[:starting_from_line]
            CTX = ""
            for line in chat_history:
                CTX += f"{line['content']}\n"
        else:
            chat_history = []
            CTX = ""

        for line in self.FULL_CTX[starting_from_line:]:
            content = f"[{line['timestamp']}][{line['role']}| {line['actor']}]: {line['content']}"
            chat_history.append({"role": line["role"], "content": content})
            CTX += f"{content}\n"

        self.chat_history = chat_history
        self.CTX = CTX
        self.HEADER = copy.deepcopy(self.CTX)
        self.HEADER_IDX = len(self.chat_history)

    def console_log(self, msg: str):
        self.chat_manager.console_log(msg)

    def get_true_role_and_alias(self, actor: str, content: str) -> Tuple[str, str]:
        role = self.ROLEs.get(actor, "system")
        if role in ["system", "sys"]:
            alias = "['system']"
        else:
            alias = f"['{role}'| {actor}]"
        if role not in self.NON_SYS_ROLES:
            role = "system"
        return role, alias
    
    def append_chat_history(self, actor: str, content: Any, action=None, log_console: bool = True):
        """Append a new entry to chat history and update context manager.
        """
        role, alias = self.get_true_role_and_alias(actor, str(content))
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = {"role": role, "actor": actor, "content": content, "timestamp": timestamp, "action": action}
        
        # Update FULL_CTX (complete history)
        self.FULL_CTX.append(line)
        self.FULL_CTX_TOKENS += num_tokens_chat_entry(line)

        if isinstance(content, dict):
            ctx = f"|{role}('{actor}')> {expand_dict(content, dept=1)}"
        else:
            ctx = f"|{role}('{actor}')> {content}"

        self.chat_history.append({"role": role, "content": f"[{timestamp}]{ctx}"})
        self.CTX += f"[{timestamp}]{ctx}\n"

        # Also persist in chat_manager
        chat_entry = {
            "sender": actor,
            "content": content,
            "timestamp": timestamp,
            "action": action,
            "history_index": len(self.chat_manager.CHAT_HISTORY),
        }
        self.chat_manager.CHAT_HISTORY.append(chat_entry)

        # Incrementally update context manager's current_ctx
        self.context_manager.append_to_current_ctx(chat_entry)
        
        # Check if rebuild is needed
        if self.context_manager.should_rebuild():
            diagnostics = self.context_manager.get_context_diagnostics()
            console.print(
                f"[CONTEXT] Threshold exceeded: {diagnostics['utilization_pct']:.1f}% "
                f"(threshold: {diagnostics['rebuild_threshold']*100:.0f}%). Triggering rebuild..."
            )
            self.context_manager.rebuild_current_ctx(
                chat_history=self.chat_manager.CHAT_HISTORY,
                memory_manager=self.memory_manager,
                target_utilization=0.6,
                verbose=1
            )

        if log_console:
            self.console_log(ctx)

    def show_ctx(self):
        console.print(self.CTX)

    def get_partial_ctx(self, idx0: int | None = None, idx1: int | None = None) -> str:
        i0 = 0 if idx0 is None else idx0
        i1 = len(self.chat_history) if idx1 is None else idx1
        CTX = ""
        if i1 < 0 and idx0 is None:
            for line in self.chat_history[idx1:]:
                CTX += f"{line['content']}\n"
        else:
            for line in self.chat_history[i0:i1]:
                CTX += f"{line['content']}\n"
        #CTX += f"[Tokens(CTX) = {self.FULL_CTX_TOKENS}]"
        ctx_diagnostics = self.context_manager.get_context_diagnostics()
        #[HINT] ctx_diagnostics = {
        #    "current_ctx_tokens": self.current_ctx_tokens,
        #    "max_ctx_tokens": self.max_ctx_tokens,
        #    "utilization": utilization,
        #    "utilization_pct": utilization * 100,
        #    "should_rebuild": self.should_rebuild(),
        #    "rebuild_threshold": self.rebuild_threshold,
        #    "num_entries": len(self.current_ctx),
        #    "avg_tokens_per_entry": avg_tokens,
        #    "rebuild_count": self.rebuild_count,
        #    "total_appends": self.total_appends,
        #    "context_version": self.context_version,
        #    "last_rebuild": self.last_rebuild_timestamp,
        #    "snapshots_available": len(self.context_history)
        #}
        CTX += f"[CTX({round(ctx_diagnostics['utilization_pct'], 2)}%): {ctx_diagnostics['current_ctx_tokens']}/{ctx_diagnostics['max_ctx_tokens']} tks | Forced rebuild @{100.0*ctx_diagnostics['rebuild_threshold']}%] "

        CTX += self.chat_block_divider + self.chat_block_divider[-5:]
        return CTX

    def show_partial_ctx(self, idx0: int | None = None, idx1: int | None = None):
        console.print(self.get_partial_ctx(idx0, idx1))

    def show_updated_history(self, console_head: int | None = None):
        head = self.CONSOLE_HEAD if console_head is None else console_head
        self.show_partial_ctx(idx0=head)
        self.CONSOLE_HEAD = len(self.chat_history)


    # ------ Operator CLI command helpers ------

    def _cli_get_workflow(self):
        """Return the active workflow registered by BaseWorkflow/TurnBasedWorkflow."""
        return getattr(self, "cli_workflow", None) or getattr(self, "workflow", None)

    @staticmethod
    def _cli_parse_kv_pairs(parts: List[str]) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        for part in parts:
            if "=" not in part:
                raise ValueError(f"Expected key=value token, got: {part}")
            key, value = part.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                raise ValueError(f"Empty key in token: {part}")
            if value.lower() in {"true", "false"}:
                value = value.lower() == "true"
            elif value.lower() in {"none", "null"}:
                value = None
            else:
                try:
                    value = int(value)
                except Exception:
                    try:
                        value = float(value)
                    except Exception:
                        pass
            updates[key] = value
        return updates

    @staticmethod
    def _cli_redact(value: Any, key: str = "") -> Any:
        sensitive = ("api_key", "apikey", "token", "password", "secret", "authorization")
        if any(s in key.lower() for s in sensitive):
            return "***REDACTED***" if value not in (None, "") else value
        if isinstance(value, dict):
            return {k: BaseInfrastructure._cli_redact(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [BaseInfrastructure._cli_redact(v, key) for v in value]
        return value

    @staticmethod
    def _cli_render(value: Any) -> str:
        """Render operator CLI output as stable pretty JSON when possible."""
        try:
            return json.dumps(value, indent=2, sort_keys=True, default=str)
        except Exception:
            return str(value)

    def _cli_all_agents(self) -> Dict[str, Any]:
        agents = {self.agent.name: self.agent}
        agents.update(self.workers)
        return agents

    def _cli_agent_summary(self, agent: Any) -> Dict[str, Any]:
        fields = [
            "name", "model", "host_address", "host_port", "api_version", "base_url",
            "verbose", "capabilities", "ctx_window_length", "cache_history", "sys_prompt",
        ]
        out = {field: getattr(agent, field, None) for field in fields if hasattr(agent, field)}
        out["type"] = type(agent).__name__
        out["workflow_role"] = "main" if agent is self.agent else "worker"
        out["has_sync_client"] = hasattr(agent, "llm")
        out["has_async_client"] = hasattr(agent, "async_llm")
        return self._cli_redact(out)

    def _cli_help_text(self) -> str:
        return r"""------------------------ [CLI HELP] ---------------------------------------------------
Start you input with '\>' or with '\' to run WOLF system commands 
and with '!>' to run terminal commands. 
[+] [Display]:
  \> help 
  \> show agents
  \> show agent <name|main>
  \> show workflow
  \> show prompt system|behavior|rules|infra
  \> show actions [limit=N]
  \> show action <action_name>
  \> show fast-workflow|hot-actions|action-usage|action-buffer|action-validation-errors
  \> show ctx|context|history|chat
----------------------------------------------------------------------------------------------------------
[+] [Modify live session]:
  \> set agent <name|main> model=<model> host=<url> port=<port> api_version=<v> capabilities=a,b
  \> set main-agent <worker_name>
  \> switch main <worker_name>
  \> switch agent <worker_name>
  \> set prompt system file=<path>
  \> set prompt behavior file=<path>
  \> set prompt rules file=<path>
  \> set prompt infra file=<path>
  \> set fast-workflow hot_action_buffer_max=12 prompt_schema_token_budget=2500
  \> reload prompts
  \> reload prompt system|behavior|rules|infra
  \> actions use all|safe|write|dev|action1,action2,...
----------------------------------------------------------------------------------------------------------
[!] Slash aliases such as /show and /set also work.
----------------------------------------------------------------------------------------------------------
[+] [Run terminal command]:
  !> cmd. i.e !> pwd 
----------------------------------------------------------------------------------------------------------
[!] [FastWorkflow ONLY]:
  \> show fast-workflow`
  \> show hot-actions`
  \> show action-usage`
  \> show action-buffer`
  \> show action-validation-errors`
  \> set fast-workflow hot_action_buffer_max=12 prompt_schema_token_budget=2500`
----------------------------------------------------------------------------------------------------------
[+] [Inline input controls]: Use  <input> and </input> containers i.e <input> ./figure2.png </input> 
    will include image modality for './figure2.png' to the context
[+] [Screen clearing controls]: clear, cls or /clear to clear the screen 
[+] [Exit commands]: exit, quit, /exit, /quit or /bye 
---------------------------------------------------------------------------------------------------------- 
"""

    def _cli_show_prompt(self, which: str) -> str:
        wf = self._cli_get_workflow()
        key = which.lower().strip()
        if key in ["system", "sys", "prompt", "agent_sys", "agent-system"]:
            if wf is None:
                return "[system][ERROR] No active workflow registered; cannot show workflow system prompt."
            return f"[system][PROMPT system file={getattr(wf, 'wf_agent_sys_prompt_file', None)}]\n{getattr(wf, 'WF_AGENT_SYS_PROMPT', '')}"
        if key in ["behavior", "behaviour", "heavior", "best-practices", "best_practices"]:
            if wf is None:
                return "[system][ERROR] No active workflow registered; cannot show behavior prompt."
            return f"[system][PROMPT behavior file={getattr(wf, 'wf_agent_behaviour_file', None)}]\n{getattr(wf, 'AGENT_BEHAVIOUR', '')}"
        if key in ["rules", "rule"]:
            if wf is None:
                return "[system][ERROR] No active workflow registered; cannot show workflow rules."
            return f"[system][PROMPT rules file={getattr(wf, 'wf_rules_file', None)}]\n{getattr(wf, 'WF_RULES', '')}"
        if key in ["infra", "infrastructure"]:
            return f"[system][PROMPT infra file={self.infra_description_file}]\n{self.INFRA_DESCRIPTION}"
        return f"[system][ERROR] Unknown prompt target: {which}"

    def _cli_show_actions(self, parts: List[str]) -> str:
        from framework.workflows.workflow_models import ACTION_NAMES
        wf = self._cli_get_workflow()
        limit = None
        for part in parts:
            if part.startswith("limit="):
                try:
                    limit = int(part.split("=", 1)[1])
                except Exception:
                    pass
        all_names = list(ACTION_NAMES)
        active_names = getattr(wf, "action_names_to_use", None) if wf is not None else None
        if not active_names:
            active_names = all_names
        shown = active_names[:limit] if limit else active_names
        schema = getattr(wf, "schema_to_use", self.SCHEMA_STRING) if wf is not None else self.SCHEMA_STRING
        data = {
            "known_count": len(all_names),
            "active_count": len(active_names),
            "active_names": shown,
            "schema_tokens": num_tokens_from_string(schema),
        }
        return f"[system][ACTIONS]\n{self._cli_render(data)}"

    def _cli_show_action(self, name: str) -> str:
        from framework.workflows.workflow_models import ACTIONS
        cls = ACTIONS.get(name)
        if cls is None:
            return f"[system][ERROR] Unknown action '{name}'."
        desc = getattr(cls.model_fields.get("description"), "default", "")
        try:
            schema = cls.model_json_schema()
        except Exception as exc:
            schema = {"error": str(exc)}
        return f"[system][ACTION {name}]\nDescription: {desc}\nSchema:\n{self._cli_render(schema)}"

    def _cli_show_workflow(self) -> str:
        wf = self._cli_get_workflow()
        if wf is None:
            return "[system][ERROR] No active workflow registered."
        data = {
            "type": type(wf).__name__,
            "WF_TAG": getattr(wf, "WF_TAG", None),
            "WF_USER": getattr(wf, "WF_USER", None),
            "WORKFLOW_TURN": getattr(wf, "WORKFLOW_TURN", None),
            "session_dir": self.session_dir,
            "main_agent": getattr(self.agent, "name", None),
            "worker_agents": list(getattr(self, "workers", {}).keys()),
            "wf_agent_sys_prompt_file": getattr(wf, "wf_agent_sys_prompt_file", None),
            "wf_agent_behaviour_file": getattr(wf, "wf_agent_behaviour_file", None),
            "wf_rules_file": getattr(wf, "wf_rules_file", None),
            "active_action_count": len(getattr(wf, "action_names_to_use", []) or []),
            "schema_tokens": num_tokens_from_string(getattr(wf, "schema_to_use", "")),
            "ctx_tokens": self.FULL_CTX_TOKENS,
        }
        return f"[system][WORKFLOW]\n{self._cli_render(data)}"

    def _cli_show_fast_workflow(self, target: str = "fast-workflow") -> str:
        """Render FastTurnBasedWorkflow adaptive-buffer diagnostics when available."""
        wf = self._cli_get_workflow()
        if wf is None:
            return "[system][ERROR] No active workflow registered."
        if not hasattr(wf, "get_fast_workflow_observability"):
            return f"[system][ERROR] Active workflow {type(wf).__name__} does not expose FastTurnBasedWorkflow diagnostics."
        try:
            data = wf.get_fast_workflow_observability()
        except Exception as exc:
            return f"[system][ERROR] Failed to collect fast workflow diagnostics: {exc}"

        key = (target or "fast-workflow").lower().strip()
        if key in ["hot-actions", "hot_actions"]:
            data = {
                "hot_actions": data.get("hot_actions"),
                "scores": {name: data.get("scores", {}).get(name) for name in data.get("hot_actions", [])},
                "config": data.get("config"),
            }
        elif key in ["action-usage", "action_usage", "usage"]:
            data = {
                "usage_counts": data.get("usage_counts"),
                "success_counts": data.get("success_counts"),
                "failure_counts": data.get("failure_counts"),
                "recent_actions": data.get("recent_actions"),
            }
        elif key in ["action-buffer", "action_buffer", "buffer"]:
            data = {
                "hot_actions": data.get("hot_actions"),
                "cold_action_count": data.get("cold_action_count"),
                "cold_aliases": data.get("cold_aliases"),
                "last_agent_prompt_size": data.get("last_agent_prompt_size"),
                "last_fast_path": data.get("last_fast_path"),
            }
        elif key in ["action-validation-errors", "action_validation_errors", "validation-errors", "validation_errors"]:
            data = {"recent_validation_errors": data.get("recent_validation_errors")}
        return f"[system][FAST WORKFLOW {key}]\n{self._cli_render(data)}"

    def _cli_switch_main_agent(self, target_name: str) -> Tuple[bool, str]:
        """Promote a worker to main agent and demote the old main to worker.

        Returns (ERROR, PROMPT). This is intended to run from the user/operator
        command path between actor turns.
        """
        target_name = (target_name or "").strip()
        if not target_name:
            return True, "[system][ERROR] Usage: \\>set main-agent <worker_name>"

        current_main = self.agent
        current_main_name = getattr(current_main, "name", None)
        if target_name in ["main", "primary", current_main_name]:
            data = {
                "status": "no-op",
                "main_agent": current_main_name,
                "worker_agents": list(getattr(self, "workers", {}).keys()),
                "message": f"Agent '{target_name}' is already the main agent." if target_name == current_main_name else "Use a worker agent name to switch main agent.",
            }
            return False, f"[system][MAIN AGENT SWITCH]\n{self._cli_render(data)}"

        if target_name not in getattr(self, "workers", {}):
            data = {
                "error": "unknown target agent or target is not a worker",
                "requested": target_name,
                "main_agent": current_main_name,
                "worker_agents": list(getattr(self, "workers", {}).keys()),
            }
            return True, f"[system][ERROR][MAIN AGENT SWITCH]\n{self._cli_render(data)}"

        before = {
            "main_agent": current_main_name,
            "worker_agents": list(self.workers.keys()),
            "WF_MEMBERS": list(getattr(self, "WF_MEMBERS", [])),
            "WF_ASSISTANTS": list(getattr(self, "WF_ASSISTANTS", [])),
        }

        promoted = self.workers.pop(target_name)
        if current_main_name:
            self.workers[current_main_name] = current_main
        self.agent = promoted

        old_assistants = set(before["WF_ASSISTANTS"]) | {current_main_name, target_name}
        assistant_names = [self.agent.name] + list(self.workers.keys())
        preserved_members = []
        for member in getattr(self, "WF_MEMBERS", []):
            if member == "system" or member in old_assistants or member in assistant_names:
                continue
            if member not in preserved_members:
                preserved_members.append(member)

        self.workers_names = list(self.workers.keys())
        self.WF_ASSISTANTS = assistant_names
        self.WF_MEMBERS = []
        for member in ["system"] + assistant_names + preserved_members:
            if member and member not in self.WF_MEMBERS:
                self.WF_MEMBERS.append(member)
        self.ROLEs["system"] = "system"
        self.ROLEs["sys"] = "system"
        for name in assistant_names:
            self.ROLEs[name] = "assistant"

        wf = self._cli_get_workflow()
        if wf is not None:
            wf.agent = self.agent
            wf.workers = self.workers
            wf.WF_MEMBERS = self.WF_MEMBERS
            wf.WF_ASSISTANTS = self.WF_ASSISTANTS
            wf.ROLEs = self.ROLEs
            if getattr(wf, "WORKFLOW_TURN", None) == target_name:
                wf.WORKFLOW_TURN = self.agent.name
            if hasattr(wf, "save_session_state"):
                wf.save_session_state()

        after = {
            "main_agent": getattr(self.agent, "name", None),
            "worker_agents": list(self.workers.keys()),
            "WF_MEMBERS": list(getattr(self, "WF_MEMBERS", [])),
            "WF_ASSISTANTS": list(getattr(self, "WF_ASSISTANTS", [])),
            "workflow_synced": wf is not None,
        }
        data = {"status": "switched", "before": before, "after": after}
        return False, f"[system][MAIN AGENT SWITCH]\n{self._cli_render(data)}"

    def _cli_handle_wolf_command(self, command_text: str) -> Tuple[bool, str, bool]:
        """Return (ERROR, PROMPT, BREAK) for a WOLF CLI command."""
        try:
            parts = shlex.split(command_text)
        except Exception as exc:
            return True, f"[system][ERROR][CMD FORMAT] {exc}", False
        if not parts:
            return True, "[system][ERROR][CMD FORMAT]: Empty command", False
        cmd = parts[0].lower()
        args = parts[1:]
        wf = self._cli_get_workflow()

        if cmd in ["help", "?"]:
            return False, self._cli_help_text(), False
        if cmd in ["quit", "exit", "bye"]:
            return False, "[system]: Good Bye", True
        if cmd in ["clear", "cls", "clean"]:
            os.system('cls' if os.name == 'nt' else 'clear')
            return False, "", False

        if cmd == "show":
            if not args:
                return True, "[system][ERROR] Missing show target. Try \\>help.", False
            target = args[0].lower()
            if target in ["chat", "history", "context", "hist", "ctx", "chat_history", "chat-history"]:
                self.show_ctx()
                return False, "", False
            if target in ["agents", "agent-list", "agent_list"]:
                data = {name: self._cli_agent_summary(agent) for name, agent in self._cli_all_agents().items()}
                return False, f"[system][AGENTS]\n{self._cli_render(data)}", False
            if target == "agent":
                if len(args) < 2:
                    return True, "[system][ERROR] Usage: \\>show agent <name|main>", False
                name = self.agent.name if args[1] in ["main", "primary"] else args[1]
                agent = self._cli_all_agents().get(name)
                if agent is None:
                    return True, f"[system][ERROR] Unknown agent '{name}'. Known: {list(self._cli_all_agents())}", False
                return False, f"[system][AGENT {name}]\n{self._cli_render(self._cli_agent_summary(agent))}", False
            if target in ["workflow", "wf"]:
                return False, self._cli_show_workflow(), False
            if target in [
                "fast-workflow", "fast_workflow", "hot-actions", "hot_actions",
                "action-usage", "action_usage", "action-buffer", "action_buffer",
                "action-validation-errors", "action_validation_errors",
                "validation-errors", "validation_errors"
            ]:
                return False, self._cli_show_fast_workflow(target), False
            if target == "prompt":
                if len(args) < 2:
                    return True, "[system][ERROR] Usage: \\>show prompt system|behavior|rules|infra", False
                return False, self._cli_show_prompt(args[1]), False
            if target in ["system", "sys", "behavior", "behaviour", "heavior", "rules", "infra", "infrastructure"]:
                return False, self._cli_show_prompt(target), False
            if target in ["actions", "action-space", "action_space"]:
                return False, self._cli_show_actions(args[1:]), False
            if target == "action":
                if len(args) < 2:
                    return True, "[system][ERROR] Usage: \\>show action <action_name>", False
                return False, self._cli_show_action(args[1]), False
            return True, f"[system][ERROR] Unknown show target '{target}'. Try \\>help.", False

        if cmd == "set":
            if not args:
                return True, "[system][ERROR] Missing set target. Try \\>help.", False
            target = args[0].lower()
            if target in ["main-agent", "main_agent", "main"]:
                if len(args) < 2:
                    return True, "[system][ERROR] Usage: \\>set main-agent <worker_name>", False
                err, msg = self._cli_switch_main_agent(args[1])
                return err, msg, False
            if target == "agent":
                if len(args) < 3:
                    return True, "[system][ERROR] Usage: \\>set agent <name|main> key=value ...", False
                name = self.agent.name if args[1] in ["main", "primary"] else args[1]
                agent = self._cli_all_agents().get(name)
                if agent is None:
                    return True, f"[system][ERROR] Unknown agent '{name}'. Known: {list(self._cli_all_agents())}", False
                try:
                    updates = self._cli_parse_kv_pairs(args[2:])
                    changed = agent.reconfigure(**updates) if hasattr(agent, "reconfigure") else updates
                    if not hasattr(agent, "reconfigure"):
                        for k, v in updates.items():
                            setattr(agent, k, v)
                    return False, f"[system][AGENT UPDATED {name}]\n{self._cli_render(self._cli_redact(changed))}", False
                except Exception as exc:
                    return True, f"[system][ERROR] Failed to update agent '{name}': {exc}", False
            if target == "prompt":
                if wf is None:
                    return True, "[system][ERROR] No active workflow registered.", False
                if len(args) < 3:
                    return True, "[system][ERROR] Usage: \\>set prompt system|behavior|rules|infra file=<path>", False
                which = args[1].lower()
                try:
                    updates = self._cli_parse_kv_pairs(args[2:])
                    file_path = updates.get("file") or updates.get("path")
                    if not file_path:
                        return True, "[system][ERROR] Prompt set requires file=<path>.", False
                    if which in ["system", "sys"]:
                        wf.update_workflow_agent_sys_prompt(str(file_path), log_console=False)
                    elif which in ["behavior", "behaviour", "heavior"]:
                        wf.update_agent_behaviour(str(file_path), log_console=False)
                    elif which in ["rules", "rule"]:
                        wf.update_workflow_rules(str(file_path), log_console=False)
                    elif which in ["infra", "infrastructure"]:
                        self.update_infra_description(str(file_path))
                    else:
                        return True, f"[system][ERROR] Unknown prompt target '{which}'.", False
                    if hasattr(wf, "save_session_state"):
                        wf.save_session_state()
                    return False, f"[system][PROMPT UPDATED] {which} file={file_path}", False
                except Exception as exc:
                    return True, f"[system][ERROR] Failed to update prompt: {exc}", False
            if target in ["fast-workflow", "fast_workflow", "fast"]:
                if wf is None:
                    return True, "[system][ERROR] No active workflow registered.", False
                if not hasattr(wf, "configure_fast_workflow"):
                    return True, f"[system][ERROR] Active workflow {type(wf).__name__} does not support fast-workflow configuration.", False
                if len(args) < 2:
                    return True, r"[system][ERROR] Usage: \>set fast-workflow key=value ...", False
                try:
                    updates = self._cli_parse_kv_pairs(args[1:])
                    changed = wf.configure_fast_workflow(**updates)
                    if hasattr(wf, "save_session_state"):
                        wf.save_session_state()
                    return False, f"[system][FAST WORKFLOW UPDATED]\n{self._cli_render(changed)}", False
                except Exception as exc:
                    return True, f"[system][ERROR] Failed to update fast workflow config: {exc}", False
            return True, f"[system][ERROR] Unknown set target '{target}'. Try \\>help.", False

        if cmd == "reload":
            if wf is None:
                return True, "[system][ERROR] No active workflow registered.", False
            target = args[0].lower() if args else "prompts"
            try:
                reloaded = []
                if target in ["prompts", "all"]:
                    wf.update_workflow_agent_sys_prompt(log_console=False); reloaded.append("system")
                    wf.update_agent_behaviour(log_console=False); reloaded.append("behavior")
                    wf.update_workflow_rules(log_console=False); reloaded.append("rules")
                    self.update_infra_description(); reloaded.append("infra")
                elif target == "prompt" and len(args) > 1:
                    sub = args[1].lower()
                    if sub in ["system", "sys"]:
                        wf.update_workflow_agent_sys_prompt(log_console=False); reloaded.append("system")
                    elif sub in ["behavior", "behaviour", "heavior"]:
                        wf.update_agent_behaviour(log_console=False); reloaded.append("behavior")
                    elif sub in ["rules", "rule"]:
                        wf.update_workflow_rules(log_console=False); reloaded.append("rules")
                    elif sub in ["infra", "infrastructure"]:
                        self.update_infra_description(); reloaded.append("infra")
                    else:
                        return True, f"[system][ERROR] Unknown reload prompt target '{sub}'.", False
                else:
                    return True, "[system][ERROR] Usage: \\>reload prompts OR \\>reload prompt system|behavior|rules|infra", False
                if hasattr(wf, "save_session_state"):
                    wf.save_session_state()
                return False, f"[system][RELOADED] {reloaded}", False
            except Exception as exc:
                return True, f"[system][ERROR] Reload failed: {exc}", False

        if cmd in ["switch", "promote"]:
            if not args:
                return True, "[system][ERROR] Usage: \\>switch main <worker_name> OR \\>switch agent <worker_name>", False
            target = args[0].lower()
            if target in ["main", "main-agent", "main_agent", "agent"]:
                if len(args) < 2:
                    return True, "[system][ERROR] Usage: \\>switch main <worker_name>", False
                err, msg = self._cli_switch_main_agent(args[1])
                return err, msg, False
            err, msg = self._cli_switch_main_agent(args[0])
            return err, msg, False

        if cmd in ["actions", "action-space", "action_space"]:
            if wf is None:
                return True, "[system][ERROR] No active workflow registered.", False
            if not args or args[0].lower() in ["show", "list"]:
                return False, self._cli_show_actions(args[1:] if args else []), False
            if args[0].lower() != "use" or len(args) < 2:
                return True, "[system][ERROR] Usage: \\>actions use all|safe|write|dev|action1,action2", False
            policy = " ".join(args[1:]).strip()
            presets = {
                "safe": ["send_message", "read_file", "check_context_utilization", "list_memory_categories"],
                "write": ["send_message", "read_file", "check_context_utilization", "list_memory_categories", "write_file"],
                "dev": ["send_message", "read_file", "check_context_utilization", "list_memory_categories", "write_file", "run_syscall"],
            }
            try:
                if policy.lower() == "all":
                    wf.set_wf_action_space(None)
                else:
                    names = presets.get(policy.lower()) or [x.strip() for x in policy.replace(" ", ",").split(",") if x.strip()]
                    wf.set_wf_action_space(names)
                if hasattr(wf, "save_session_state"):
                    wf.save_session_state()
                return False, self._cli_show_actions([]), False
            except Exception as exc:
                return True, f"[system][ERROR] Failed to set action space: {exc}", False

        return True, f"[system][ERROR][CMD FORMAT]: Unknown WOLF command: {cmd}. Try \\>help.", False

    def process_user_input(self, user_prompt: str):
        r"""Process user input with support for \>, !>, / commands, and @ routing."""
        BREAK, IS_CMD, ERROR, INTERLOCUTOR, PROMPT = False, False, False, "system", None
        prompt = user_prompt.strip().lower()
        original_prompt = user_prompt.strip()

        # Handle exit commands
        if prompt.startswith(("exit", "quit", "/bye", "/exit", "/quit")):
            IS_CMD, BREAK = True, True
            PROMPT = "[system]: Good Bye"

        # Handle clear commands
        elif prompt.startswith(("clear", "cls")):
            IS_CMD = True
            os.system('cls' if os.name == 'nt' else 'clear')

        # Handle WOLF function commands (\>)
        elif original_prompt.startswith("\\>"):
            IS_CMD = True
            ERROR, PROMPT, BREAK = self._cli_handle_wolf_command(original_prompt[2:].strip())

        # Handle terminal commands (!>)
        elif original_prompt.startswith("!>"):
            IS_CMD = True
            terminal_cmd = original_prompt[2:].strip()
            if not terminal_cmd:
                PROMPT = "[system][ERROR][CMD FORMAT]: Empty !> terminal command"
                ERROR = True
            else:
                try:
                    result = subprocess.run(
                        terminal_cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    output = result.stdout if result.stdout else result.stderr
                    PROMPT = f"[system][TERMINAL OUTPUT]:\n{output}"
                except subprocess.TimeoutExpired:
                    PROMPT = "[system][ERROR]: Terminal command timed out after 30 seconds"
                    ERROR = True
                except Exception as e:
                    PROMPT = f"[system][ERROR]: Failed to execute terminal command: {e}"
                    ERROR = True

        # Handle old-style / commands for backward compatibility
        elif prompt.startswith("/"):
            IS_CMD = True
            ERROR, PROMPT, BREAK = self._cli_handle_wolf_command(original_prompt[1:].strip())

        # Handle @ interlocutor commands
        elif prompt.startswith("@"):
            cmd = user_prompt.strip().split()
            INTERLOCUTOR = cmd[0][1:]
            if INTERLOCUTOR not in self.WF_MEMBERS:
                PROMPT = f"[system][INPUT ERROR]: BAD @interlocutor command:\n Interlocutor {INTERLOCUTOR} not in WF-MEMBERS: {self.WF_MEMBERS}"
                ERROR = True
            PROMPT = user_prompt.strip()[len(cmd[0]):]

        # Regular user input
        else:
            PROMPT = user_prompt.strip()

        return BREAK, IS_CMD, ERROR, INTERLOCUTOR, PROMPT


    # ------ Reusable multimodal user-input preparation ------
    def prepare_user_input_for_agent(self, user_prompt: str, agent: Any = None):
        """Normalize a regular user prompt and prepare optional attachments.

        This workflow-independent service parses inline ``<input> ... <input/>``
        tags, classifies referenced files, prepares provider-ready content for
        the next immediate agent call, and returns a compact history-safe bundle.
        Heavy payloads (e.g. base64 images) are intentionally kept out of chat
        history and stored only as pending in-memory content.
        """
        if agent is None:
            agent = self.agent
        bundle = self.input_processor.process(user_prompt, agent=agent)
        self.pending_user_input_bundle = bundle
        self.pending_agent_content = bundle.agent_content if bundle.has_attachments else None
        return bundle

    def consume_pending_agent_content(self):
        """Return and clear pending rich multimodal content for one agent turn."""
        content = self.pending_agent_content
        self.pending_agent_content = None
        self.pending_user_input_bundle = None
        return content

    # ------ Snapshot and Restore methods ------
    def snapshot(self) -> Dict[str, Any]:
        """Create a snapshot of the current infrastructure state.
        
        Returns:
            Dict containing all state information needed to restore the instance.
        """
        snapshot_data = {
            # Manager snapshots
            "chat_manager": self.chat_manager.snapshot(),
            "context_manager": self.context_manager.snapshot(),
            "memory_manager": self.memory_manager.snapshot(),
            
            # Infrastructure-specific state
            "FULL_CTX": self.FULL_CTX,
            "FULL_CTX_TOKENS": self.FULL_CTX_TOKENS,
            "chat_history": self.chat_history,
            "CTX": self.CTX,
            "HEADER": self.HEADER,
            "HEADER_IDX": self.HEADER_IDX,
            "CONSOLE_HEAD": self.CONSOLE_HEAD,
            
            # Configuration state
            "max_ctx_tokens": self.max_ctx_tokens,
            "chat_block_divider": self.chat_block_divider,
            "session_dir": self.session_dir,
            "log_dir": self.log_dir,
            
            # Role and member tracking
            "ROLEs": self.ROLEs,
            "WF_MEMBERS": self.WF_MEMBERS,
            "WF_ASSISTANTS": self.WF_ASSISTANTS,
            "workers_names": self.workers_names,
            "NON_SYS_ROLES": self.NON_SYS_ROLES,
            
            # Metadata
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "infra_description_file": self.infra_description_file,
        }
        return snapshot_data

    def restore(self, snapshot_data: Dict[str, Any]) -> None:
        """Restore the infrastructure state from a snapshot.
        
        Args:
            snapshot_data: Dictionary containing state information from a previous snapshot.
        """
        # Restore manager states
        if "chat_manager" in snapshot_data:
            self.chat_manager.restore(snapshot_data["chat_manager"])
        if "context_manager" in snapshot_data:
            self.context_manager.restore(snapshot_data["context_manager"])
        if "memory_manager" in snapshot_data:
            self.memory_manager.restore(snapshot_data["memory_manager"])
        
        # Restore infrastructure-specific state
        self.FULL_CTX = snapshot_data.get("FULL_CTX", [])
        self.FULL_CTX_TOKENS = snapshot_data.get("FULL_CTX_TOKENS", 0)
        self.chat_history = snapshot_data.get("chat_history", [])
        self.CTX = snapshot_data.get("CTX", "")
        self.HEADER = snapshot_data.get("HEADER", "")
        self.HEADER_IDX = snapshot_data.get("HEADER_IDX", 0)
        self.CONSOLE_HEAD = snapshot_data.get("CONSOLE_HEAD", 0)
        
        # Restore configuration (if present)
        if "max_ctx_tokens" in snapshot_data:
            self.max_ctx_tokens = snapshot_data["max_ctx_tokens"]
        if "chat_block_divider" in snapshot_data:
            self.chat_block_divider = snapshot_data["chat_block_divider"]
        if "session_dir" in snapshot_data:
            self.session_dir = snapshot_data["session_dir"]
        if "log_dir" in snapshot_data:
            self.log_dir = snapshot_data["log_dir"]
        
        # Restore role and member tracking
        if "ROLEs" in snapshot_data:
            self.ROLEs = snapshot_data["ROLEs"]
        if "WF_MEMBERS" in snapshot_data:
            self.WF_MEMBERS = snapshot_data["WF_MEMBERS"]
        if "WF_ASSISTANTS" in snapshot_data:
            self.WF_ASSISTANTS = snapshot_data["WF_ASSISTANTS"]
        if "workers_names" in snapshot_data:
            self.workers_names = snapshot_data["workers_names"]
        if "NON_SYS_ROLES" in snapshot_data:
            self.NON_SYS_ROLES = snapshot_data["NON_SYS_ROLES"]
        
        # Restore metadata
        if "infra_description_file" in snapshot_data:
            self.infra_description_file = snapshot_data["infra_description_file"]
            self.update_infra_description()
        
        console.print("[INFRASTRUCTURE] State restored from snapshot")

    def save_snapshot(self, file_path: str) -> None:
        """Save a snapshot to disk.
        
        Args:
            file_path: Path where the snapshot should be saved.
        """
        snapshot_data = self.snapshot()
        
        # Use pickle for backward compatibility with other components
        with open(file_path, 'wb') as f:
            pickle.dump(snapshot_data, f)
        
        console.print(f"[INFRASTRUCTURE] Snapshot saved to {file_path}")
        self.console_log(f"[INFO] Infrastructure snapshot saved to {file_path}")

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
                console.print(f"[INFRASTRUCTURE] Snapshot loaded from {file_path}")
                self.console_log(f"[INFO] Infrastructure snapshot loaded from {file_path}")
                return True
            else:
                console.print(f"[INFRASTRUCTURE] Failed to load snapshot from {file_path}")
                self.console_log(f"[ERROR] Failed to load infrastructure snapshot from {file_path}")
                return False
        except FileNotFoundError:
            console.print(f"[INFRASTRUCTURE] Snapshot file not found: {file_path}")
            self.console_log(f"[ERROR] Infrastructure snapshot file not found: {file_path}")
            return False
        except Exception as e:
            console.print(f"[INFRASTRUCTURE] Error loading snapshot: {e}")
            self.console_log(f"[ERROR] Error loading infrastructure snapshot: {e}")
            return False
