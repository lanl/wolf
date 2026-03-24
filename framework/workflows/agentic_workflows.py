import copy
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import TypeAdapter

from framework.utils.io_tools import console, jsonfy
from framework.utils.json_parsing import robust_jsonfy
# Import the updated workflow models that provide action‑subset capability
from framework.workflows.workflow_models import (
    Actions as FullActions,
    SCHEMA_STRING as FULL_SCHEMA_STRING,
    AGENT_ROLE_PROMPT as FULL_AGENT_ROLE_PROMPT,
    get_actions_subset,
)
from framework.workflows.workflow_infrastructure import BaseInfrastructure
from framework.workflows.enhanced_input import interactive_input_line_wrapped


def normalize_payload(payload: Dict) -> Dict:
    """Normalize action payloads to ensure schema compliance.
    - Removes empty or falsy ``yield_motion_to`` fields
    - Removes keys with ``None`` values
    """
    normalized = {k: v for k, v in payload.items() if v is not None}
    if "yield_motion_to" in normalized and not normalized["yield_motion_to"]:
        del normalized["yield_motion_to"]
    return normalized


class BaseWorkflow:
    """Core workflow loop with integrated managers.

    The ``run`` method can optionally receive ``action_names`` – a list of
    action discriminator values – to restrict the available actions for the
    session.  When supplied, ``get_actions_subset`` builds a Pydantic union and
    a matching schema string; otherwise the full set of actions is used.
    """

    def __init__(self, infra: BaseInfrastructure, actions_union: Any = FullActions):
        self.infra = infra
        # expose frequently used components for convenience
        self.agent = infra.agent
        self.workers = infra.workers
        self.objects = infra.objects
        self.WF_MEMBERS = infra.WF_MEMBERS
        self.WF_ASSISTANTS = infra.WF_ASSISTANTS
        self.ROLEs = infra.ROLEs
        self.NON_SYS_ROLES = infra.NON_SYS_ROLES
        self.chat_manager = infra.chat_manager
        self.memory_manager = infra.memory_manager
        self.context_manager = infra.context_manager
        self.chat_block_divider = infra.chat_block_divider
        self.log = infra.log

        # workflow‑specific state
        self.WORKFLOW_TURN = "user"  # primary turn tracker
        self.WF_USER = "user"
        self.HEADER = copy.deepcopy(self.infra.CTX)  # retained for possible external use
        self.HEADER_IDX = len(self.infra.chat_history)
        self.Actions = actions_union
        self.action_adapter = TypeAdapter(self.Actions)
        # Full schema (fallback) and placeholder for the active schema string
        self.full_schema_string = FULL_SCHEMA_STRING
        self.schema_to_use = self.full_schema_string
        self.agent_role_prompt = FULL_AGENT_ROLE_PROMPT

        # -----------------------------------------------------------------
        # Initialise some default memory entries so that Facts, User Preferences,
        # and Task State sections are never empty in the context window.
        # -----------------------------------------------------------------
        #try:
        #    self.memory_manager.remember("app_name", "Cerberus", category="facts")
        #    self.memory_manager.remember("workflow_phase", {"phase": "init"}, category="task_state")
        #    self.memory_manager.remember("preferred_language", "en", category="user_prefs")
        #except Exception as exc:
        #    console.print(f"[MEMORY INIT] Failed to set default entries: {exc}")

    # -----------------------------------------------------------------
    # Helper forwarding methods (mostly thin wrappers around infra)
    # -----------------------------------------------------------------
    def console_log(self, msg: str):
        self.infra.console_log(msg)

    def append_chat_history(self, actor: str, content: Any, action=None, log_console: bool = True):
        self.infra.append_chat_history(actor, content, action, log_console)
        # inform memory manager about the new entry for possible summarisation
        new_entries = [self.chat_manager.CHAT_HISTORY[-1]] if self.chat_manager.CHAT_HISTORY else []
        if new_entries:
            self.memory_manager.process_new_entries(new_entries)

    def get_compated_context_and_diagnostics(self, verbose=0):
        recent_budget = int(self.context_manager.max_ctx_tokens * self.context_manager.recent_chat_ratio)
        memory_budget = int(self.context_manager.max_ctx_tokens * self.context_manager.memory_ratio)
        trace_budget = int(self.context_manager.max_ctx_tokens * self.context_manager.trace_ratio)
        context_str, diagnostics = self.context_manager.build_context(
            chat_history=self.chat_manager.CHAT_HISTORY,
            memory_manager=self.memory_manager,
            recent_chat_budget=recent_budget,
            memory_budget=memory_budget,
            trace_budget=trace_budget,
            user_query="",
            agent_plan="",
        )
        if verbose > 1: console.print(f"[CTX][COMPACPTION]: context_str = {context_str}")
        if verbose > 0:console.print(f"[CTX][COMPACPTION]: Diagnostics = {diagnostics}")
        return context_str, diagnostics 

    def update_history(self, actor: str, content: Any, action=None, log_console: bool = True):
        self.append_chat_history(actor, content, action, log_console)

    def normalize_and_validate_agent_response(self, response):
        try:
            normalized = normalize_payload(response)
        except Exception as exc:
            return True, f"[payload normalization error] {exc}", None, None
        try:
            action_obj = self.action_adapter.validate_python(normalized)
        except Exception as exc:
            return True, f"[Normalized payload validation error] {exc}", None, normalized
        return False, None, action_obj, normalized

    def format_agent_response(self, prompt, schema, agent, max_trial=5):
        ntrial = 0
        while ntrial < max_trial:
            raw = agent.get_chat_response(user_prompt=prompt + f"\n{schema}")
            result = robust_jsonfy(raw)
            if "parsed" in result:
                return False, result["parsed"], raw, result
            raw = agent.get_chat_response(
                user_prompt=f"Please fix the JSON format of the following response: {result}\n{schema}"
            )
            result = robust_jsonfy(raw)
            if "parsed" in result:
                return False, result["parsed"], raw, result
            return True, None, raw, result
            ntrial += 1

    # -----------------------------------------------------------------
    # Unified actor‑turn handling (used for both the main agent and workers)
    # -----------------------------------------------------------------
    def _handle_actor_turn(self, actor, name: str):
        """Process a turn for *actor* (either the main agent or a worker).

        *actor* – the agent/worker instance.
        *name*  – string identifier used for routing the next turn.
        """
        #self.show_updated_history() [IDB1]
        self.infra.show_updated_history()
        # Build context and diagnostics
        context_str, diagnostics = self.get_compated_context_and_diagnostics()
        AGENT_PROMPT = (
            f"{self.agent_role_prompt}\n\n"
            "Below is the context formed from the current chat history:\n"
            "*** Context Start ***\n"
            f"{context_str}\n"
            "*** Context End ***\n\n"
            "The following are the actions you can take in  response to the context\n"
            "*** List of allowed Actions Start *** \n"
            f"{self.schema_to_use}\n"
            "*** List of allowed Actions End *** \n\n"
        )
        # Obtain a response (structured or free‑form)
        if "structured_output" in getattr(actor, "capabilities", []):
            response = actor.get_structured_output(user_prompt=AGENT_PROMPT, output_format=self.Actions)
        else:
            bad_format, response, raw_response, result = actor.format_agent_response(AGENT_PROMPT, self.schema_to_use)
            if bad_format:
                # fallback to user turn on failure
                self.WORKFLOW_TURN = "user"
                self.update_history(
                    actor="system",
                    content=f"{actor.name} could not produce a valid response:\n {raw_response}",
                    action={"action": "system_info"},
                    log_console=True,
                )
                return
        # Validate payload
        if isinstance(response, dict) and "action" in response:
            bad_format, err_msg, action_obj, normalized = self.normalize_and_validate_agent_response(response)
            if bad_format:
                self.WORKFLOW_TURN = name
                self.update_history(
                    actor="system",
                    content=err_msg,
                    action={"action": "system_info"},
                    log_console=True,
                )
                return
            # Remember facts
            #self.memory_manager.generate_memory_fragments(context_str,  self.agent) # I.D.B: Frreezing for now
            # Show Actor's action
            self.update_history(
                actor=actor.name,
                content=normalized,
                action=normalized["action"],
                log_console=True,
            )
            # Execute the concrete action
            result = action_obj.execute(infra=self.infra)
            # Determine next turn
            if getattr(action_obj, "yield_motion_to", None):
                self.WORKFLOW_TURN = action_obj.yield_motion_to
            elif getattr(action_obj, "receiver", None):
                self.WORKFLOW_TURN = action_obj.receiver
            else:
                self.WORKFLOW_TURN = "system"
        else:
            self.update_history(
                actor="system",
                content=f"[ERROR] Invalid action payload: {response}",
                action={"action": "system_error"},
                log_console=True,
            )
            self.WORKFLOW_TURN = name

    # -----------------------------------------------------------------
    # Core workflow loop – now can optionally restrict actions
    # -----------------------------------------------------------------
    def run(self, user_name: str = "user", 
            wf_first_turn: str = "user", 
            action_names: Optional[List[str]] = None, 
            log_console: bool = True):
        """Execute the workflow.

        If *action_names* is provided, only those actions (by their ``action``
        discriminator value) will be accepted. The corresponding schema is
        generated via ``get_actions_subset``.
        """
        # Determine the actions union and schema for this run
        if action_names:
            SubsetActions, subset_schema = get_actions_subset(action_names)
            self.Actions = SubsetActions
            self.action_adapter = TypeAdapter(self.Actions)
            self.schema_to_use = subset_schema
        else:
            self.Actions = FullActions
            self.action_adapter = TypeAdapter(self.Actions)
            self.schema_to_use = self.full_schema_string

        self.WF_USER = user_name
        self.infra.ROLEs[user_name] = "user"
        self.WORKFLOW_TURN = wf_first_turn

        while True:
            turn = self.WORKFLOW_TURN.strip().lower()
            worker_names = [w.strip().lower() for w in self.workers]

            # ---------------------------------------------------------
            # USER TURN
            # ---------------------------------------------------------
            if turn in ["user", self.WF_USER.lower()]:
                #self.show_updated_history() [IDB1]
                self.infra.show_updated_history()
                raw_input = interactive_input_line_wrapped(prompt_text=f"[{self.WF_USER}]> ")
                if raw_input is None:
                    break
                user_prompt = raw_input.strip()
                BREAK, IS_CMD, ERROR, INTERLOCUTOR, WF_PROMPT = self.infra.process_user_input(user_prompt)
                if IS_CMD:
                    if WF_PROMPT:
                        console.print(WF_PROMPT)
                        self.console_log(WF_PROMPT)
                    if BREAK:
                        break
                else:
                    if ERROR and WF_PROMPT:
                        console.print(WF_PROMPT)
                        self.console_log(WF_PROMPT)
                    else:
                        self.update_history(
                            actor=self.WF_USER,
                            content=WF_PROMPT,
                            action={"action": "user_input"},
                            log_console=log_console,
                        )
                        #self.memory_manager.remember("last_user_input", user_prompt, category="facts")
                        self.WORKFLOW_TURN = INTERLOCUTOR
                # keep alias up‑to‑date
                continue

            # ---------------------------------------------------------
            # MAIN AGENT TURN
            # ---------------------------------------------------------
            if turn in ["system", "assistant", "agent", self.agent.name.strip().lower()]:
                self._handle_actor_turn(self.agent, self.agent.name)
                continue

            # ---------------------------------------------------------
            # WORKER TURN
            # ---------------------------------------------------------
            if turn in worker_names:
                worker = self.workers[self.WORKFLOW_TURN]
                self._handle_actor_turn(worker, worker.name)
                continue

            # ---------------------------------------------------------
            # FALLBACK – unknown turn, reset to user
            # ---------------------------------------------------------
            self.WORKFLOW_TURN = "user"
