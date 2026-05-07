import copy
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import TypeAdapter

from framework.utils.io_tools import console, jsonfy, save_pickle_file, load_pickle_file
from framework.utils.io_tools import save_dill_file, load_dill_file
from framework.utils.json_parsing import robust_jsonfy
# Import the updated workflow models that provide action‑subset capability
from framework.workflows.workflow_models import (
    Actions as FullActions,
    SCHEMA_STRING as FULL_SCHEMA_STRING,
    AGENT_ROLE_PROMPT as FULL_AGENT_ROLE_PROMPT,
    get_actions_subset,
)
from framework.workflows.sessions_data_models import BaseSession
#from framework.workflows.workflow_infrastructure import BaseInfrastructure
from framework.infrastructure.base_infrastructure import BaseInfrastructure
from framework.workflows.enhanced_input import interactive_input_line_wrapped


def normalize_payload(payload: Dict, actor:str) -> Dict:
    """Normalize action payloads to ensure schema compliance.
    - Removes empty or falsy ``yield_motion_to`` fields
    - Removes keys with ``None`` values
    - Adds defaults for ``send_message`` action when ``sender`` or ``receiver`` are missing
    """
    normalized = {k: v for k, v in payload.items() if v is not None}
    if "yield_motion_to" in normalized and not normalized["yield_motion_to"]:
        del normalized["yield_motion_to"]
    # Ensure ``send_message`` always includes required sender/receiver fields
    if normalized.get("action") == "send_message":
        payload_section = normalized.get("payload", {})
        if isinstance(payload_section, dict):
            if "sender" not in payload_section:
                payload_section["sender"] = actor #"assistant"
            if "receiver" not in payload_section:
                payload_section["receiver"] = actor #"user"
            normalized["payload"] = payload_section
    return normalized


class BaseWorkflow:
    """Core workflow loop with integrated managers.

    The ``run`` method can optionally receive ``action_names`` – a list of
    action discriminator values – to restrict the available actions for the
    session.  When supplied, ``get_actions_subset`` builds a Pydantic union and
    a matching schema string; otherwise the full set of actions is used.
    """

    def __init__(self,
                 session: BaseSession|None           = None,
                 infra: BaseInfrastructure|None      = None, 
                 actions_union: Any                  = FullActions,
                 wf_rules_file: str|None             ="config/preferences/rules/workflow/basewf.md",
                 wf_agent_behaviour_file: str|None   ="config/preferences/behaviour/workflow/basewf.md",
                 wf_agent_sys_prompt_file: str|None  ="config/preferences/prompts/workflow/basewf_default_assistant_sys_prompt.md",
                 wf_user:str                         = "user",
                 wf_turn                             = None
                 ):
        self.WF_TAG = "BaseWorkflow"
        if session is None:
            print(f"[+] STARTING NEW SESSION")
            self.session = BaseSession(
                infra = infra,
                actions_union = actions_union,
                wf_rules_file = wf_rules_file,
                wf_agent_behaviour_file = wf_agent_behaviour_file,
                wf_agent_sys_prompt_file = wf_agent_sys_prompt_file,
                WF_TAG = self.WF_TAG,
                WORKFLOW_USER = wf_user,
                WORKFLOW_TURN = wf_turn,
                agent_role_prompt = FULL_AGENT_ROLE_PROMPT,
                full_schema_string = FULL_SCHEMA_STRING,
                WF_RULES = ""
            )
        else:
            self.session = session
            print(f"[+] RESUMING SESSION")
        self.load_session_state()
        print(f"[+] SESSION STATE LOADED OK")

    # -----------------------------------------------------------------
    # Helper forwarding methods (mostly thin wrappers around infra)
    # -----------------------------------------------------------------
    def update_session_state(self):
        self.session = BaseSession(
                infra = self.infra,
                actions_union = self.Actions,
                wf_rules_file = self.wf_rules_file,
                wf_agent_behaviour_file = self.wf_agent_behaviour_file,
                wf_agent_sys_prompt_file = self.wf_agent_sys_prompt_file,
                WF_TAG = self.WF_TAG,
                WORKFLOW_USER = self.WF_USER,
                WORKFLOW_TURN = self.WORKFLOW_TURN,
                agent_role_prompt = self.agent_role_prompt,
                WF_RULES = self.WF_RULES
            )

    def load_session_state(self, session=None):
        """Load workflow session state from various input formats.
        
        Args:
            session: Can be:
                - None: Do nothing (caller should create new session in __init__)
                - BaseSession object: Use directly
                - String: Session identifier in various formats:
                    - Keywords: 'latest', 'last', 'recent', 'most_recent', 'newest' - loads most recent session
                    - 'session_YYYYMMDD_HHMMSS': Full session ID
                    - 'YYYYMMDD': Date only (8 digits) - loads latest session from that date
                    - Path ending with '.state': Direct path to state file
                    - Path starting with 'wf_workspace': Path to session directory or file
                    - Partial match: Any string that matches part of a session name
        """
        import os
        import glob
        from pathlib import Path
        
        # Keywords that mean "load the most recent session"
        LATEST_KEYWORDS = ['latest', 'last', 'recent', 'most_recent', 'newest']

        if session is None: session = self.session
        
        # Case 1: None - Should be impossible
        if session is None:
            raise Exception(f"[!][ERROR] A 'None' session should be impossible at this stage. How did you get here? contact devs")
        
        # Case 2: Direct BaseSession object provided
        if isinstance(session, BaseSession):
            self.session = session
        
        # Case 3: String identifier provided
        elif isinstance(session, str):
            session_path = None
            session_lower = session.strip().lower()
            
            # Check if it's a keyword for loading the most recent session
            if session_lower in LATEST_KEYWORDS:
                pattern = "wf_workspace/session_*/session.state"
                all_sessions = sorted(glob.glob(pattern), reverse=True)
                if all_sessions:
                    session_path = all_sessions[0]
                    console.print(f"[cyan]Loading most recent session: {os.path.dirname(session_path)}[/cyan]")
                else:
                    raise Exception(f"[!][ERROR] No sessions found in wf_workspace/")
            
            # Check if it's a direct path to a state file
            elif session.endswith('.state') and os.path.exists(session):
                session_path = session
            
            # Check if it's a path starting with wf_workspace
            elif session.startswith('wf_workspace'):
                if session.endswith('.state'):
                    session_path = session
                else:
                    # Assume it's a directory, append session.state
                    session_path = os.path.join(session, 'session.state')
            
            # Check if it's a full session ID: session_YYYYMMDD_HHMMSS
            elif session.startswith('session_'):
                session_path = f"wf_workspace/{session}/session.state"
            
            # Check if it's just a date: YYYYMMDD (8 digits starting with '20')
            elif len(session) == 8 and session.isdigit() and session.startswith('20'):
                # Find all sessions from that date
                pattern = f"wf_workspace/session_{session}_*/session.state"
                matching_sessions = sorted(glob.glob(pattern), reverse=True)
                if matching_sessions:
                    session_path = matching_sessions[0]  # Get the latest
                    console.print(f"[cyan]Loading latest session from {session}: {os.path.dirname(session_path)}[/cyan]")
                else:
                    raise Exception(f"[!][ERROR] No sessions found for date {session}")
            
            # Otherwise, treat as partial session ID (might be timestamp part or any partial match)
            else:
                # Try to find matching session
                pattern = f"wf_workspace/session_*{session}*/session.state"
                matching_sessions = sorted(glob.glob(pattern), reverse=True)
                if matching_sessions:
                    session_path = matching_sessions[0]
                    console.print(f"[cyan]Found matching session: {os.path.dirname(session_path)}[/cyan]")
                else:
                    raise Exception(f"[!][ERROR] Unable to find session matching '{session}'")
            
            # Load the session from the determined path
            if session_path and os.path.exists(session_path):
                try:
                    #self.session = load_pickle_file(session_path)
                    self.session = load_dill_file(session_path)
                    console.print(f"[green]Successfully loaded session from {session_path}[/green]")
                except Exception as load_err:
                    raise Exception(f"[!][ERROR] Unable to load session from {session_path}: {load_err}")
            else:
                raise Exception(f"[!][ERROR] Session path does not exist: {session_path}")
        
        else:
            raise Exception(f"[!][ERROR] Invalid session type: {type(session)}. Expected BaseSession, str, or None")

        # Extract and restore infrastructure components from loaded session
        infra                    = self.session.infra
        wf_agent_behaviour_file  = self.session.wf_agent_behaviour_file
        wf_rules_file            = self.session.wf_rules_file
        wf_agent_sys_prompt_file = self.session.wf_agent_sys_prompt_file
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
        self.WF_USER = self.session.WORKFLOW_USER
        if self.session.WORKFLOW_TURN is None:
            self.WORKFLOW_TURN = self.WF_USER
        else:
            self.WORKFLOW_TURN = self.session.WORKFLOW_TURN  # primary turn tracker
        self.HEADER = copy.deepcopy(self.infra.CTX)  # retained for possible external use
        self.HEADER_IDX = len(self.infra.chat_history)
        self.Actions = self.session.actions_union
        self.action_adapter = TypeAdapter(self.Actions)
        # Full schema (fallback) and placeholder for the active schema string
        self.full_schema_string = self.session.full_schema_string
        self.schema_to_use = self.full_schema_string
        self.agent_role_prompt = self.session.agent_role_prompt
        ## AGENT BEHAVIOUR
        self.wf_agent_behaviour_file  = wf_agent_behaviour_file
        self.AGENT_BEHAVIOUR = ""
        self.update_agent_behaviour(wf_agent_behaviour_file)
        ## WORKFLOW RULES
        self.wf_rules_file = wf_rules_file
        self.WF_RULES = ''
        self.update_workflow_rules(wf_rules_file)
        self.wf_agent_sys_prompt_file = wf_agent_sys_prompt_file


    def save_session_state(self):
        #save_pickle_file(f"{self.session.infra.chat_manager.session_dir}/session.state", self.session)
        #save_dill_file(f"{self.session.infra.chat_manager.session_dir}/session.state", self.session)
        pass

    def console_log(self, msg: str):
        self.infra.console_log(msg)

    def update_workflow_rules(self, wf_rules_file: str|None =None):
        if wf_rules_file is not None:
            self.wf_rules_file = wf_rules_file
        else:
            if self.wf_rules_file is None:
                self.WF_RULES = ''
                return
        with open(self.wf_rules_file, "r") as f: self.WF_RULES = f.read()

    def update_agent_behaviour(self, agent_behaviour_file: str|None =None):
        if agent_behaviour_file is not None:
           self.wf_agent_behaviour_file = agent_behaviour_file
        else:
            if self.wf_agent_behaviour_file is None:
                self.AGENT_BEHAVIOUR = ''
                return
        with open(self.wf_agent_behaviour_file, "r") as f: self.AGENT_BEHAVIOUR = f.read()

    def update_history(self, actor: str, content: Any, action=None, log_console: bool = True):
        self.infra.append_chat_history(actor, content, action, log_console)
        # inform memory manager about the new entry for possible summarisation
        new_entries = [self.chat_manager.CHAT_HISTORY[-1]] if self.chat_manager.CHAT_HISTORY else []
        if new_entries:
            self.memory_manager.process_new_entries(new_entries)
        self.save_session_state()

    def normalize_and_validate_agent_response(self, response, actor:str):
        try:
            normalized = normalize_payload(response, actor)
        except Exception as exc:
            print(f"[!][ERROR][normalize_and_validate_agent_response()] Unable to normalize agent_response:\n type(response) = {type(response)} \n response = {response}")
            return True, f"[payload normalization error] {exc}", None, None
        try:
            action_obj = self.action_adapter.validate_python(normalized)
        except Exception as exc:
            print(f"[!][ERROR][normalize_and_validate_agent_response()] Unable to validate agent_response:\n type(response) = {type(response)} \n response = {response}")
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
        self.infra.show_updated_history()
        
        # Get the current context buffer (no rebuild unless threshold exceeded)
        context_str = self.context_manager.get_compacted_context()
        diagnostics = self.context_manager.get_context_diagnostics()
        
        # Build agent prompt with the current context
        AGENT_PROMPT = (
            f"{self.agent_role_prompt}\n\n"
            "Below is the context formed from the current chat history:\n"
            "*** Context Start ***\n"
            f"{context_str}\n"
            "*** Context End ***\n\n"
            "The following are the actions you can take in  response to the context\n"
            "*** List of allowed Actions Start *** \n"
            f"{self.schema_to_use}\n"
            "*** List of allowed Actions End *** \n"
            "*** Description of the infrastructure Start *** \n"
            f"{self.infra.INFRA_DESCRIPTION}\n"
            "*** Description of the infrastructure End *** \n"
            f"*** Best Practices Start *** \n"
            f"{self.AGENT_BEHAVIOUR}\n"
            f"*** Best Practices End *** \n"
            f"*** WORKFLOW RULES Start *** \n"
            f"{self.WF_RULES}\n"
            "*** WORKFLOW RULES End *** \n\n"
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
            bad_format, err_msg, action_obj, normalized = self.normalize_and_validate_agent_response(response, actor)
            if bad_format:
                self.WORKFLOW_TURN = name
                self.update_history(
                    actor="system",
                    content=err_msg,
                    action={"action": "system_info"},
                    log_console=True,
                )
                return
            
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
                        self.WORKFLOW_TURN = INTERLOCUTOR
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
