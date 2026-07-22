import copy
import os
import logging
import pickle
import subprocess
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
            "timestamp": timestamp
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

    def process_user_input(self, user_prompt: str):
        """Process user input with support for \>, !>, and @ commands."""
        BREAK, IS_CMD, ERROR, INTERLOCUTOR, PROMPT = False, False, False, "system", None
        prompt = user_prompt.strip().lower()
        original_prompt = user_prompt.strip()
        
        # Handle exit commands
        if prompt.startswith(("exit", "quit", "/bye", "/exit", "/quit")):
            IS_CMD, BREAK = True, True
            PROMPT = f"[system]: Good Bye"
        
        # Handle clear commands
        elif prompt.startswith(("clear", "cls")):
            IS_CMD = True
            os.system('cls' if os.name == 'nt' else 'clear')
        
        # Handle WOLF function commands (\>)
        elif original_prompt.startswith("\\>"):
            IS_CMD = True
            cmd = prompt[2:].strip().split()
            if not cmd:
                PROMPT = f"[system][ERROR][CMD FORMAT]: Empty \\> command"
                ERROR = True
            elif cmd[0] in ["quit", "exit", "bye"]:
                PROMPT = f"[system]: Good Bye"
                BREAK = True
            elif cmd[0] in ["clear", "cls", "clean"]:
                os.system('cls' if os.name == 'nt' else 'clear')
            elif cmd[0] == "show":
                if len(cmd) > 1:
                    if cmd[1] in ["chat", "history", "context", "hist", "ctx", "chat_history", "chat-history"]:
                        self.show_ctx()
                        if len(cmd) > 2:
                            PROMPT = f"[system][WARN][CMD FORMAT]: BAD \\>show {cmd[1]} command does not take extra arguments: {cmd[2:]}"
                    elif cmd[1] in ["updated-chat", "updated-history", "updated-context", "updated-hist", "updated-ctx", "updated-chat_history", "updated-chat-history"]:
                        if len(cmd) >= 2:
                            try:
                                cmd2, console_head = cmd[1].strip().split("=")
                                cmd2 = cmd2.strip()
                                if cmd2 in ["head", "idx", "console", "console_head", "console-head", "console_idx"]:
                                    try:
                                        head = int(console_head)
                                        self.show_updated_history(head)
                                    except Exception as cmd_int_err:
                                        PROMPT = f"[system][ERROR][CMD FORMAT]: BAD \\>{cmd[0]} {cmd[1]} command: {cmd[2]}=??:\n {console_head} is not an integer: {cmd_int_err}"
                                        ERROR = True
                            except Exception as cmd2_err:
                                PROMPT = f"[system][ERROR][CMD FORMAT]: BAD \\>{cmd[0]} {cmd[1]} command format: {cmd2_err}"
                                ERROR = True
                    else:
                        PROMPT = f"[system][ERROR][CMD FORMAT]: BAD \\>{cmd[0]} command format: Extra arguments {cmd[1:]}"
                        ERROR = True
                else:
                    PROMPT = f"[system][ERROR][CMD FORMAT]: BAD \\>{cmd[0]} command format: Missing extra arguments"
                    ERROR = True
            else:
                PROMPT = f"[system][ERROR][CMD FORMAT]: Unknown WOLF command: {cmd[0]}"
                ERROR = True
        
        # Handle terminal commands (!>)
        elif original_prompt.startswith("!>"):
            IS_CMD = True
            terminal_cmd = original_prompt[2:].strip()
            if not terminal_cmd:
                PROMPT = f"[system][ERROR][CMD FORMAT]: Empty !> terminal command"
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
                    PROMPT = f"[system][ERROR]: Terminal command timed out after 30 seconds"
                    ERROR = True
                except Exception as e:
                    PROMPT = f"[system][ERROR]: Failed to execute terminal command: {e}"
                    ERROR = True
        
        # Handle old-style / commands for backward compatibility
        elif prompt.startswith("/"):
            IS_CMD = True
            cmd = prompt[1:].strip().split()
            if cmd[0] in ["quit", "exit", "bye"]:
                PROMPT = f"[system]: Good Bye"
                BREAK = True
            elif cmd[0] in ["clear", "cls", "clean"]:
                os.system('cls' if os.name == 'nt' else 'clear')
            elif cmd[0] == "show":
                if len(cmd) > 1:
                    if cmd[1] in ["chat", "history", "context", "hist", "ctx", "chat_history", "chat-history"]:
                        self.show_ctx()
                        if len(cmd) > 2:
                            PROMPT = f"[system][WARN][CMD FORMAT]: BAD />show {cmd[1]} command does not take extra arguments: {cmd[2:]}"
                    elif cmd[1] in ["updated-chat", "updated-history", "updated-context", "updated-hist", "updated-ctx", "updated-chat_history", "updated-chat-history"]:
                        if len(cmd) >= 2:
                            try:
                                cmd2, console_head = cmd[1].strip().split("=")
                                cmd2 = cmd2.strip()
                                if cmd2 in ["head", "idx", "console", "console_head", "console-head", "console_idx"]:
                                    try:
                                        head = int(console_head)
                                        self.show_updated_history(head)
                                    except Exception as cmd_int_err:
                                        PROMPT = f"[system][ERROR][CMD FORMAT]: BAD />{cmd[0]} {cmd[1]} command: {cmd[2]}=??:\n {console_head} is not an integer: {cmd_int_err}"
                                        ERROR = True
                            except Exception as cmd2_err:
                                PROMPT = f"[system][ERROR][CMD FORMAT]: BAD />{cmd[0]} {cmd[1]} command format: {cmd2_err}"
                                ERROR = True
                    else:
                        PROMPT = f"[system][ERROR][CMD FORMAT]: BAD />{cmd[0]} command format: Extra arguments {cmd[1:]}"
                        ERROR = True
                else:
                    PROMPT = f"[system][ERROR][CMD FORMAT]: BAD />{cmd[0]} command format: Missing extra arguments"
                    ERROR = True
        
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
