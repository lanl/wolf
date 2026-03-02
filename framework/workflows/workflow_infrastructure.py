import copy
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from framework.utils.io_tools import console, jsonfy, expand_dict
from framework.utils.tokenomics import (
    num_tokens_from_string,
    num_tokens_chat_entry,
)

from framework.data_store.data_models import BaseVectorStoreParams 
from framework.knowledgebase.data_models import KnowledgeBaseParams
from framework.knowledgebase.knowledge_base import  KBParams, KnowledgeBase
from framework.tooling.toolbox import ToolBox
from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams
from framework.universes.base_universe import BaseUniverse

class BaseInfrastructure:
    """Provides non-workflow-specific functionality with integrated managers."""

    def __init__(
        self,
        agent: Any,
        workers: List[Any] = [],
        objects: List[Any] = [],
        max_ctx_tokens: int = 20000,
        wf_log_dir: str = "wf_logs",
        session_dir: str = "./", #Optional[str] = None,
        chat_block_divider: str = "/" * 120,
        schema_string: str | None = None,
        chat_manager: Any = None,
        memory_manager: Any = None,
        context_manager: Any = None,
        traces_vector_store: Any = None,
        summaries_vector_store: Any = None,
    ):
        # Support session_dir as primary, fall back to wf_log_dir for backwards compatibility
        self.session_dir = session_dir.strip().rstrip("/")
        self.log_dir = f"{self.session_dir}/{session_dir.strip().rstrip('/')}"
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
            if ag.name == self.agent:
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
        for obj in self.objects:
            obj_type = None
            if isinstance(obj, BaseUniverse):
                self.UNIVs[obj.name] = obj
                obj_type = "universe"
            elif isinstance(obj, KnowledgeBase):
                self.KBs[obj.name] = obj
                obj_type = "knowledgebase"
            elif isinstance(obj, KBParams):
                self.KBs[obj.name] = KnowledgeBase(obj)
                obj_type = "knowledgebase"
            elif isinstance(obj, KnowledgeBase):
                self.KBs[obj.name] = KnowledgeBase(obj)
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
            from framework.workflows.chat_manager import BaseChatManager
            self.chat_manager = BaseChatManager(
                session_dir=self.session_dir, #self.log_dir,
                chat_block_divider=chat_block_divider
            )

        if memory_manager is not None:
            self.memory_manager = memory_manager
        else:
            from framework.workflows.memory_manager import MemoryManager
            self.memory_manager = MemoryManager(
                session_dir=self.session_dir, #self.log_dir,
                traces_vector_store=traces_vector_store,
                summaries_vector_store=summaries_vector_store
            )

        if context_manager is not None:
            self.context_manager = context_manager
        else:
            from framework.workflows.context_manager import ContextManager
            self.context_manager = ContextManager(
                max_ctx_tokens=max_ctx_tokens,
                traces_vector_store=traces_vector_store
            )

        # Logging setup
        self.log = self.chat_manager.log

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

    # ------ Helper / utility methods ------
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
        MSG = msg.lower()
        if "[error]" in MSG:
            self.log.error(MSG)
        elif "[warn]" in MSG:
            self.log.warning(MSG)
        elif "[debug]" in MSG:
            self.log.debug(MSG)
        elif "[critk]" in MSG:
            self.log.critical(MSG)
        else:
            self.log.info(MSG)

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
        role, alias = self.get_true_role_and_alias(actor, str(content))
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = {"role": role, "actor": actor, "content": content, "timestamp": timestamp, "action": action}
        self.FULL_CTX.append(line)
        self.FULL_CTX_TOKENS += num_tokens_chat_entry(line)

        if isinstance(content, dict):
            #ctx = f"|{role}('{actor}')> {self.expand_dict(content, dept=1)}"
            ctx = f"|{role}('{actor}')> {expand_dict(content, dept=1)}"
        else:
            ctx = f"|{role}('{actor}')> {content}"

        self.chat_history.append({"role": role, "content": f"[{timestamp}]{ctx}"})
        self.CTX += f"[{timestamp}]{ctx}\n"

        # Also persist in chat_manager
        self.chat_manager.CHAT_HISTORY.append({
            "sender": actor,
            "content": content,
            "timestamp": timestamp
        })

        if log_console:
            self.console_log(ctx)

    def update_history(self, actor: str, content: Any, action=None, log_console: bool = True):
        self.append_chat_history(actor, content, action, log_console=log_console)

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
        CTX += f"[Tokens(CTX) = {self.FULL_CTX_TOKENS}]"
        CTX += self.chat_block_divider + self.chat_block_divider[-5:]
        return CTX

    def show_partial_ctx(self, idx0: int | None = None, idx1: int | None = None):
        console.print(self.get_partial_ctx(idx0, idx1))

    def show_updated_history(self, console_head: int | None = None):
        head = self.CONSOLE_HEAD if console_head is None else console_head
        self.show_partial_ctx(idx0=head)
        self.CONSOLE_HEAD = len(self.chat_history)

    def process_user_input(self, user_prompt: str):
        BREAK, IS_CMD, ERROR, INTERLOCUTOR, PROMPT = False, False, False, "system", None
        prompt = user_prompt.strip().lower()
        if prompt.startswith(("exit", "quit", "/bye", "/exit", "/quit")):
            IS_CMD, BREAK = True, True
            PROMPT = f"[system]: Good Bye"
        elif prompt.startswith(("clear", "cls")):
            IS_CMD = True
            os.system('cls' if os.name == 'nt' else 'clear')
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
        elif prompt.startswith("@"):
            cmd = user_prompt.strip().split()
            INTERLOCUTOR = cmd[0][1:]
            if INTERLOCUTOR not in self.WF_MEMBERS:
                PROMPT = f"[system][INPUT ERROR]: BAD @interlocutor command:\n Interlocutor {INTERLOCUTOR} not in WF-MEMBERS: {self.WF_MEMBERS}"
                ERROR = True
            PROMPT = user_prompt.strip()[len(cmd[0]):]
        else:
            PROMPT = user_prompt.strip()
        return BREAK, IS_CMD, ERROR, INTERLOCUTOR, PROMPT
