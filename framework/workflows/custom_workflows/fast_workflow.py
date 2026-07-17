from typing import Any, List, Optional

from framework.utils.io_tools import console, jsonfy, save_pickle_file, load_pickle_file
from framework.utils.json_parsing import robust_jsonfy
# Import the updated workflow models that provide action‑subset capability
from framework.workflows.workflow_models import (
    Actions as FullActions,
    SCHEMA_STRING as FULL_SCHEMA_STRING,
    AGENT_ROLE_PROMPT as FULL_AGENT_ROLE_PROMPT,
    get_actions_subset,
    ACTION_SPACE_PROMPT, ACTIONS, ACTION_NAMES,
)
from framework.workflows.sessions_data_models import BaseSession
from framework.infrastructure.base_infrastructure import BaseInfrastructure
from framework.workflows.enhanced_input import interactive_input_line_wrapped
from framework.workflows.base_workflow import BaseWorkflow


class FastTurnBasedWorkflow(BaseWorkflow):
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
                 wf_turn: str|None                   = None,
                 WF_TAG : str|None                   = "FastTurnBasedWF",
                 WF_VERBOSE: int                     = 0
                 ):
        # Inherit and init all the methods and properties from BaseWF 
        super().__init__(session,
                         infra,
                         actions_union,
                         wf_rules_file,
                         wf_agent_behaviour_file,
                         wf_agent_sys_prompt_file,
                         wf_user,
                         wf_turn,
                         WF_TAG,
                         WF_VERBOSE)

    # -----------------------------------------------------------------
    # Unified actor‑turn handling (used for both the main agent and workers)
    # -----------------------------------------------------------------
    def _handle_actor_turn(self, actor, name: str, verbose=0):
        """Process a turn for *actor* (either the main agent or a worker).
        *actor* – the agent/worker instance.
        *name*  – string identifier used for routing the next turn.
        """
        self.infra.show_updated_history()
        
        # Get the current context buffer (no rebuild unless threshold exceeded)
        context_str = self.context_manager.get_compacted_context()
        diagnostics = self.context_manager.get_context_diagnostics()
        
        AGENT_PROMPT = (
            f"{self.agent_role_prompt}\n\n"
            "Below is the context formed from the current chat history:\n"
            "*** Context Start ***\n"
            f"{context_str}\n"
            "*** Context End ***\n\n"
            "*** Description of the infrastructure Start *** \n"
            f"{self.infra.INFRA_DESCRIPTION}\n"
            "*** Description of the infrastructure End *** \n"
            f"*** Best Practices Start *** \n"
            f"{self.AGENT_BEHAVIOUR}\n"
            f"*** Best Practices End *** \n"
            f"*** WORKFLOW RULES Start *** \n"
            f"{self.WF_RULES}\n"
            "*** WORKFLOW RULES End *** \n\n"
            f"{ACTION_SPACE_PROMPT}\n"
            "In this step you select the name of the action you would like to take next. Your response MUST be a STRING and should be ONE of the following: {ACTION_NAMES}.\n If required,, the system action processor will later request the payload for the action that you have selected. For now, just select the name of the action"
        )
        if verbose>0:  console.print(f"[++] AGENT_PROMPT = {AGENT_PROMPT}")
        response = actor.get_chat_response(AGENT_PROMPT)
        if verbose>0:  console.print(f"[!][!] AGENT RESPONSE = {response} | type = {type(response)}")
        nTrial, nMAX = 0, 5
        while( (nTrial<nMAX) and not isinstance(response, str)):
            FIX_ERR_PROMT = f"[!!][FORMAT ERR]: Agent response {response} is of type {type(response)}, A response of type 'string' is expected"
            if verbose>0:  console.print(f"[!!][FORMAT ERR][%01d/%01d]: {FIX_ERR_PROMT}" %(nTrial, nMAX))
            ERROR_PROMPT = f"{AGENT_PROMPT}\n {FIX_ERR_PROMT}"
            if verbose>0:  console.print(f"[!!] [ERROR_PROMPT]: {ERROR_PROMPT}")
            response = actor.get_chat_response(f"{ERROR_PROMPT}")
            nTrial +=1
        next_action = ""
        try:
            next_action_name = response.strip()
        except Exception as strip_err:
            if verbose>0:  console.print(f"[!][!] Problem cleaning up Agent response: {strip_err}")
            next_action_name = response
        nTrial, nMAX = 0, 5
        while (next_action_name not in ACTION_NAMES):
            FIX_ERR_PROMT = f"[!!][FORMAT ERR]: Agent response {response} is not in the list of names of allowed actions: {ACTION_NAMES}. This step is just to selecting the action, responde with the exact name of the action you would like to take next"
            if verbose>0:  console.print(f"[!!][FORMAT ERR][%01d/%01d]: {FIX_ERR_PROMT}" %(nTrial, nMAX))
            ERROR_PROMPT = f"{AGENT_PROMPT}\n {FIX_ERR_PROMT}"
            if verbose>0:  console.print(f"[!!] [ERROR_PROMPT]: {ERROR_PROMPT}")
            response = actor.get_chat_response(f"{ERROR_PROMPT}")
            try:        
                next_action_name = response.strip()
            except Exception as strip_err:
                if verbose>0:  console.print(f"[!][!] Problem cleaning up Agent response: {strip_err}")
                next_action_name = response
            nTrial +=1  
        try:
            NEXT_ACTION = ACTIONS[next_action_name]
        except Exception as action_select_err:
            if verbose>0:  console.print(f"[!][!] Problem selecting Action: {action_select_err}")
            NEXT_ACTION = ACTIONS[str(next_action_name)]
  
        ## Validate payload
        if response in ACTION_NAMES: 
            # Get Action Payload
            AGENT_PROMPT = (
                f"{AGENT_PROMPT}\n"
                f"You have chosen '{next_action_name}' as your next action.\n"
                f"Now provide the payload for {next_action_name} STRICTLY following the following format:\n"
                f"{NEXT_ACTION.model_fields['payload_schema']}"
                )
            #response = actor.get_structured_output(user_prompt=AGENT_PROMPT, output_format=self.Actions)
            #if verbose>0:  console.print(f"[!][!] AGENT RESPONSE2 = {response} | type = {type(response)}")
            #if not isinstance(response, dict):
            #    payload = robust_jsonfy(response)
            #else:
            #    payload = response
            if "structured_output" in getattr(actor, "capabilities", []):
                if verbose>0:  console.print(f"[++] AGENT Supports structured_output: Gettting response")
                response = actor.get_structured_output(user_prompt=AGENT_PROMPT, output_format=self.Actions)
                if verbose>0:  console.print(f"[!][!] AGENT RESPONSE2 = {response} | type = {type(response)}")
            else:
                if verbose>0:  console.print(f"[++] AGENT does NOT Supports structured_output: Gettting response")
                bad_format, response, raw_response, result = actor.format_agent_response(AGENT_PROMPT, NEXT_ACTION.model_fields['payload_schema'])
                if verbose>0:  console.print(f"[!][!] AGENT RESPONSE2 = {response} | type = {type(response)}, BAD = {bad_format}")
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
            if not isinstance(response, dict):
                payload = robust_jsonfy(response)
            else:
                payload = response
            action_args = {}
            if 'payload' not in payload.keys():
                action_args = {'action':next_action_name, 'payload':payload}
            else:
                action_args = payload
            if 'action' not in action_args.keys():
                action_args['action'] = next_action_name
             
            # Show Actor's action
            self.update_history(
                actor=actor.name,
                content=action_args,
                action=action_args["action"],
                log_console=True,
            )
            
            # Execute the concrete action
            action_obj = self.action_adapter.validate_python(action_args)
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
            action_names: Optional[List[str]] = None,
            wolf_commands = ['show', 'clear', 'quit', 'exit', 'bye', 'cls'],
            wf_first_turn = "user",
            log_console: bool = True,
            verbose: int = 0):
        """Execute the workflow. If *action_names* is provided, only those 
           actions (by their ``action`` discriminator value) will be accepted. 
           The corresponding schema is generated via ``get_actions_subset``.
        """
        # Determine the actions union and schema for this run
        self.set_wf_action_space(action_names)
        self.WF_USER = user_name
        self.infra.ROLEs[user_name] = "user"
        self.WORKFLOW_TURN = wf_first_turn

        # Orchestrate turn-base interactions
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
