import copy
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import TypeAdapter
import json
import os
import glob

from framework.utils.io_tools import console, jsonfy, save_pickle_file, load_pickle_file
from framework.utils.json_parsing import robust_jsonfy
# Import the updated workflow models that provide action‑subset capability
from framework.workflows.workflow_models import (
    Actions as FullActions,
    SCHEMA_STRING as FULL_SCHEMA_STRING,
    AGENT_ROLE_PROMPT as FULL_AGENT_ROLE_PROMPT,
    get_actions_subset,
)
from framework.workflows.sessions_data_models import BaseSession
from framework.infrastructure.base_infrastructure import BaseInfrastructure
from framework.workflows.enhanced_input_v2 import interactive_input_line_wrapped


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
                 session: BaseSession|str|None      = None,
                 infra: BaseInfrastructure|None      = None, 
                 actions_union: Any                  = FullActions,
                 wf_rules_file: str|None             ="config/preferences/rules/workflow/basewf.md",
                 wf_agent_behaviour_file: str|None   ="config/preferences/behaviour/workflow/basewf.md",
                 wf_agent_sys_prompt_file: str|None  ="config/preferences/prompts/workflow/basewf_default_assistant_sys_prompt.md",
                 wf_user:str                         = "user",
                 wf_turn                             = None
                 ):
        self.WF_TAG = "BaseWorkflow"
        
        # Determine if we're starting new or resuming
        if session is None:
            # Starting a completely new session
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
            self.load_session_state()
        elif isinstance(session, str):
            # Resuming from a session identifier (path, date, keyword, etc.)
            print(f"[+] RESUMING SESSION FROM: {session}")
            session_path = self._resolve_session_path(session)
            self._load_session_snapshot(session_path)
        elif isinstance(session, BaseSession):
            # Using a provided BaseSession object
            print(f"[+] RESUMING SESSION FROM BaseSession OBJECT")
            self.session = session
            self.load_session_state()
        else:
            raise Exception(f"[!][ERROR] Invalid session type: {type(session)}. Expected BaseSession, str, or None")

    # -----------------------------------------------------------------
    # Session state management (NEW IMPLEMENTATION)
    # -----------------------------------------------------------------
    def _resolve_session_path(self, session_identifier: str) -> str:
        """Resolve a session identifier to an actual snapshot file path.
        
        Args:
            session_identifier: Can be:
                - Keywords: 'latest', 'last', 'recent', 'most_recent', 'newest'
                - Full session ID: 'session_YYYYMMDD_HHMMSS'
                - Date: 'YYYYMMDD' (8 digits)
                - Direct path ending with '.snapshot.json'
                - Path starting with 'wf_workspace'
                - Partial match string
                
        Returns:
            Full path to the session snapshot file
        """
        LATEST_KEYWORDS = ['latest', 'last', 'recent', 'most_recent', 'newest']
        session_lower = session_identifier.strip().lower()
        
        # Check if it's a keyword for loading the most recent session
        if session_lower in LATEST_KEYWORDS:
            pattern = "wf_workspace/session_*/session.snapshot.json"
            all_sessions = sorted(glob.glob(pattern), reverse=True)
            if all_sessions:
                console.print(f"[cyan]Loading most recent session: {os.path.dirname(all_sessions[0])}[/cyan]")
                return all_sessions[0]
            else:
                raise Exception(f"[!][ERROR] No sessions found in wf_workspace/")
        
        # Check if it's a direct path to a snapshot file
        if session_identifier.endswith('.snapshot.json') and os.path.exists(session_identifier):
            return session_identifier
        
        # Check if it's a path starting with wf_workspace
        if session_identifier.startswith('wf_workspace'):
            if session_identifier.endswith('.snapshot.json'):
                return session_identifier
            else:
                # Assume it's a directory, append session.snapshot.json
                return os.path.join(session_identifier, 'session.snapshot.json')
        
        # Check if it's a full session ID: session_YYYYMMDD_HHMMSS
        if session_identifier.startswith('session_'):
            snapshot_path = f"wf_workspace/{session_identifier}/session.snapshot.json"
            if os.path.exists(snapshot_path):
                return snapshot_path
        
        # Check if it's just a date: YYYYMMDD (8 digits starting with '20')
        if len(session_identifier) == 8 and session_identifier.isdigit() and session_identifier.startswith('20'):
            pattern = f"wf_workspace/session_{session_identifier}_*/session.snapshot.json"
            matching_sessions = sorted(glob.glob(pattern), reverse=True)
            if matching_sessions:
                console.print(f"[cyan]Loading latest session from {session_identifier}: {os.path.dirname(matching_sessions[0])}[/cyan]")
                return matching_sessions[0]
            else:
                raise Exception(f"[!][ERROR] No sessions found for date {session_identifier}")
        
        # Otherwise, treat as partial session ID
        pattern = f"wf_workspace/session_*{session_identifier}*/session.snapshot.json"
        matching_sessions = sorted(glob.glob(pattern), reverse=True)
        if matching_sessions:
            console.print(f"[cyan]Found matching session: {os.path.dirname(matching_sessions[0])}[/cyan]")
            return matching_sessions[0]
        else:
            raise Exception(f"[!][ERROR] Unable to find session matching '{session_identifier}'")

    def _load_session_snapshot(self, snapshot_path: str) -> None:
        """Load session state from a snapshot file and reconstruct infrastructure.
        
        Args:
            snapshot_path: Path to the session.snapshot.json file
        """
        try:
            with open(snapshot_path, 'r') as f:
                snapshot_data = json.load(f)
            
            console.print(f"[green]Successfully loaded snapshot from {snapshot_path}[/green]")
            
            # Reconstruct infrastructure from snapshot
            infra_snapshot = snapshot_data['infrastructure']
            
            # We need the original agent/workers/objects to reconstruct infrastructure
            # These should be stored in the snapshot as well
            agent = snapshot_data.get('agent_info')  # Need to implement agent serialization
            workers = snapshot_data.get('workers_info', [])
            objects = snapshot_data.get('objects_info', [])
            
            # Create new infrastructure with original parameters
            self.infra = BaseInfrastructure(
                agent=agent,
                workers=workers,
                objects=objects,
                max_ctx_tokens=infra_snapshot.get('max_ctx_tokens', 100),
                session_dir=infra_snapshot.get('session_dir', './'),
                chat_block_divider=infra_snapshot.get('chat_block_divider', '/'*120)
            )
            
            # Restore infrastructure state from snapshot
            self.infra.restore(infra_snapshot)
            
            # Restore session-level state
            self.session = BaseSession(
                infra=self.infra,
                actions_union=FullActions,  # Will be updated in load_session_state
                wf_rules_file=snapshot_data.get('wf_rules_file'),
                wf_agent_behaviour_file=snapshot_data.get('wf_agent_behaviour_file'),
                wf_agent_sys_prompt_file=snapshot_data.get('wf_agent_sys_prompt_file'),
                WF_TAG=snapshot_data.get('WF_TAG', 'BaseWorkflow'),
                WORKFLOW_USER=snapshot_data.get('WORKFLOW_USER', 'user'),
                WORKFLOW_TURN=snapshot_data.get('WORKFLOW_TURN'),
                agent_role_prompt=snapshot_data.get('agent_role_prompt', FULL_AGENT_ROLE_PROMPT),
                full_schema_string=snapshot_data.get('full_schema_string', FULL_SCHEMA_STRING),
                WF_RULES=snapshot_data.get('WF_RULES', '')
            )
            
            self.load_session_state()
            console.print(f"[green]Session restored successfully[/green]")
            
        except FileNotFoundError:
            raise Exception(f"[!][ERROR] Snapshot file not found: {snapshot_path}")
        except Exception as e:
            raise Exception(f"[!][ERROR] Failed to load session snapshot: {e}")

    def create_session_snapshot(self) -> Dict[str, Any]:
        """Create a complete session snapshot that can be serialized to JSON.
        
        Returns:
            Dictionary containing all session state
        """
        snapshot = {
            # Infrastructure snapshots (from managers)
            'infrastructure': self.infra.snapshot(),
            
            # Session-level state
            'WF_TAG': self.WF_TAG,
            'WORKFLOW_USER': self.WF_USER,
            'WORKFLOW_TURN': self.WORKFLOW_TURN,
            'wf_rules_file': self.wf_rules_file,
            'wf_agent_behaviour_file': self.wf_agent_behaviour_file,
            'wf_agent_sys_prompt_file': self.wf_agent_sys_prompt_file,
            'agent_role_prompt': self.agent_role_prompt,
            'full_schema_string': self.full_schema_string,
            'WF_RULES': self.WF_RULES,
            'AGENT_BEHAVIOUR': self.AGENT_BEHAVIOUR,
            
            # Metadata
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'session_dir': self.infra.session_dir,
            
            # TODO: Add serializable representations of agent/workers/objects
            # For now, store minimal info to identify them
            'agent_info': {
                'name': self.agent.name,
                'type': type(self.agent).__name__
            },
            'workers_info': [{'name': w.name, 'type': type(w).__name__} for w in self.workers.values()],
            'objects_info': []  # TODO: serialize object references
        }
        
        return snapshot

    def save_session_state(self) -> None:
        """Save session state to disk using JSON serialization.
        
        Creates a session.snapshot.json file in the session directory.
        """
        snapshot = self.create_session_snapshot()
        snapshot_path = f"{self.session.infra.chat_manager.session_dir}/session.snapshot.json"
        
        with open(snapshot_path, 'w') as f:
            json.dump(snapshot, f, indent=2)
        
        console.print(f"[green]Session snapshot saved to {snapshot_path}[/green]")

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
        """Load workflow session state from BaseSession object.
        
        Args:
            session: BaseSession object to load from (defaults to self.session)
        """
        if session is None: 
            session = self.session
        
        if not isinstance(session, BaseSession):
            raise Exception(f"[!][ERROR] Invalid session type: {type(session)}. Expected BaseSession")
        
        self.session = session
        infra = self.session.infra
        
        # Extract infrastructure components
        self.infra = infra
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
        
        # Workflow‑specific state
        self.WF_USER = self.session.WORKFLOW_USER
        if self.session.WORKFLOW_TURN is None:
            self.WORKFLOW_TURN = self.WF_USER
        else:
            self.WORKFLOW_TURN = self.session.WORKFLOW_TURN
            
        self.HEADER = copy.deepcopy(self.infra.CTX)
        self.HEADER_IDX = len(self.infra.chat_history)
        self.Actions = self.session.actions_union
        self.action_adapter = TypeAdapter(self.Actions)
        self.full_schema_string = self.session.full_schema_string
        self.schema_to_use = self.full_schema_string
        self.agent_role_prompt = self.session.agent_role_prompt
        
        # Load configuration files
        self.wf_agent_behaviour_file = self.session.wf_agent_behaviour_file
        self.AGENT_BEHAVIOUR = ""
        self.update_agent_behaviour(self.wf_agent_behaviour_file)
        
        self.wf_rules_file = self.session.wf_rules_file
        self.WF_RULES = ''
        self.update_workflow_rules(self.wf_rules_file)
        
        self.wf_agent_sys_prompt_file = self.session.wf_agent_sys_prompt_file

    def console_log(self, msg: str):
        self.infra.chat_manager.console_log(msg)

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
        # Auto-save session state after each update
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
        
        # Get list of available WOLF commands for autocompletion
        wolf_commands = ['show', 'clear', 'quit', 'exit', 'bye', 'cls']

        while True:
            turn = self.WORKFLOW_TURN.strip().lower()
            worker_names = [w.strip().lower() for w in self.workers]

            # ---------------------------------------------------------
            # USER TURN
            # ---------------------------------------------------------
            if turn in ["user", self.WF_USER.lower()]:
                self.infra.show_updated_history()
                raw_input = interactive_input_line_wrapped(
                    prompt_text=f"[{self.WF_USER}]> ",
                    wf_commands=wolf_commands
                )
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
