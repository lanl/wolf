from typing import Any, List, Optional

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
from framework.workflows.enhanced_input import interactive_input_line_wrapped
from framework.workflows.base_workflow import BaseWorkflow
from framework.utils.multimodal_input import combine_prompt_with_user_content


class TurnBasedWorkflow(BaseWorkflow):
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
        # Inherit and init all the methods and properties from BaseWF 
        super().__init__(session,
                         infra,
                         actions_union,
                         wf_rules_file,
                         wf_agent_behaviour_file,
                         wf_agent_sys_prompt_file,
                         wf_user,
                         wf_turn )

        # Overide WF Tag
        self.WF_TAG = "TurnBasedWorkflow"
        
    # -----------------------------------------------------------------
    # Unified actor‑turn handling (used for both the main agent and workers)
    # -----------------------------------------------------------------
    def _handle_actor_turn(self, actor, name: str):
        """Process a turn for *actor* (either the main agent or a worker).
        *actor* – the agent/worker instance.
        *name*  – string identifier used for routing the next turn.
        """
        self.infra.show_updated_history()
        self.emit_event("workflow_step_started", "running", actor=name, content=f"{name} is starting an interactive workflow step.")
        
        # Get the current context buffer (no rebuild unless threshold exceeded)
        self.emit_event("context_build_started", "running", actor=name, content="Building compacted context for model call.")
        context_str = self.context_manager.get_compacted_context()
        diagnostics = self.context_manager.get_context_diagnostics()
        self.emit_event(
            "context_build_completed",
            "done",
            actor=name,
            content="Compacted context ready.",
            context_tokens=diagnostics.get("current_ctx_tokens") if isinstance(diagnostics, dict) else None,
            max_ctx_tokens=diagnostics.get("max_ctx_tokens") if isinstance(diagnostics, dict) else None,
            utilization_pct=diagnostics.get("utilization_pct") if isinstance(diagnostics, dict) else None,
        )
        
        # Build agent prompt with the current context
        self.emit_event("prompt_assembly_started", "running", actor=name, content="Assembling agent prompt.")
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
        pending_agent_content = self.infra.consume_pending_agent_content()
        AGENT_INPUT = combine_prompt_with_user_content(AGENT_PROMPT, pending_agent_content)
        self.emit_event(
            "prompt_assembly_completed",
            "done",
            actor=name,
            content="Agent prompt assembled.",
            has_multimodal_content=bool(pending_agent_content),
            active_action_count=len(getattr(self, "action_names_to_use", []) or []),
        )
        
        # Obtain a response (structured or free‑form)
        self.emit_event("model_request_started", "running", actor=name, content=f"Calling model for {name}.", structured_output="structured_output" in getattr(actor, "capabilities", []))
        if "structured_output" in getattr(actor, "capabilities", []):
            response = actor.get_structured_output(user_prompt=AGENT_INPUT, output_format=self.Actions)
        else:
            bad_format, response, raw_response, result = actor.format_agent_response(AGENT_INPUT, self.schema_to_use)
            if bad_format:
                # fallback to user turn on failure
                self.WORKFLOW_TURN = "user"
                err = f"{actor.name} could not produce a valid response:\n {raw_response}"
                self.emit_event("model_request_failed", "error", actor=name, content=err, error=err)
                self.update_history(
                    actor="system",
                    content=err,
                    action={"action": "system_info"},
                    log_console=True,
                )
                return
        self.emit_event("model_request_completed", "done", actor=name, content="Model response received.")
        
        # Validate payload
        if isinstance(response, dict) and "action" in response:
            self.emit_event("action_validation_started", "running", actor=name, action=response.get("action"), content="Validating model action response.")
            bad_format, err_msg, action_obj, normalized = self.normalize_and_validate_agent_response(response, actor)
            if bad_format:
                # Validation failures should fail closed to the user rather than
                # handing the turn back to the same actor. Returning to ``name``
                # here can create a response-validation loop: the same actor sees
                # the same context plus the validation error and may emit the same
                # invalid action repeatedly.
                self.WORKFLOW_TURN = "user"
                self.emit_event("action_validation_failed", "error", actor=name, action=response.get("action"), content=err_msg)
                self.update_history(
                    actor="system",
                    content=err_msg,
                    action={"action": "system_info"},
                    log_console=True,
                )
                return
            
            self.emit_event("action_validation_completed", "done", actor=name, action=normalized.get("action"), content=f"Validated action {normalized.get('action')}.")
            # Show Actor's action
            self.update_history(
                actor=actor.name,
                content=normalized,
                action=normalized["action"],
                log_console=True,
            )
            
            # Execute the concrete action
            self.emit_event("action_execution_started", "running", actor=name, action=normalized.get("action"), content=f"Executing {normalized.get('action')}.")
            result = action_obj.execute(infra=self.infra)
            
            # Determine next turn
            if getattr(action_obj, "yield_motion_to", None):
                self.WORKFLOW_TURN = action_obj.yield_motion_to
            elif getattr(action_obj, "receiver", None):
                self.WORKFLOW_TURN = action_obj.receiver
            else:
                self.WORKFLOW_TURN = "system"
            self.emit_event("action_execution_completed", "done", actor=name, action=normalized.get("action"), content=f"Action {normalized.get('action')} completed.", next_turn=self.WORKFLOW_TURN)
            self.emit_event("workflow_step_completed", "done", actor=name, action=normalized.get("action"), content="Interactive workflow step completed.", next_turn=self.WORKFLOW_TURN)
        else:
            err = f"[ERROR] Invalid action payload: {response}"
            self.emit_event("workflow_error", "error", actor=name, content=err, error=err)
            self.update_history(
                actor="system",
                content=err,
                action={"action": "system_error"},
                log_console=True,
            )
            # Invalid non-action payloads should also fail closed to the user,
            # not immediately re-enter the same actor turn.
            self.WORKFLOW_TURN = "user"

    # -----------------------------------------------------------------
    # Core workflow loop – now can optionally restrict actions
    # -----------------------------------------------------------------


    def run(self, user_name: str = "user",
            action_names: Optional[List[str]] = None,
            wolf_commands = ['help', 'show', 'set', 'reload', 'actions', 'clear', 'quit', 'exit', 'bye', 'cls'],
            wf_first_turn = "user",
            log_console: bool = True):
        """Execute the workflow. If *action_names* is provided, only those 
           actions (by their ``action`` discriminator value) will be accepted. 
           The corresponding schema is generated via ``get_actions_subset``.
        """
        # Determine the actions union and schema for this run
        self.set_wf_action_space(action_names)
        self.infra.cli_workflow = self
        self.WF_USER = user_name
        self.infra.ROLEs[user_name] = "user"
        self.WORKFLOW_TURN = wf_first_turn
        self.emit_event("workflow_started", "running", content="Interactive TurnBasedWorkflow started.", user_name=user_name, first_turn=wf_first_turn)

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
                        # Normal user input is converted through the reusable
                        # infrastructure input processor. History receives only
                        # a compact text representation; rich content is kept
                        # pending for the next immediate agent turn.
                        target_actor = self.workers.get(INTERLOCUTOR, self.agent)
                        input_bundle = self.infra.prepare_user_input_for_agent(WF_PROMPT, agent=target_actor)
                        self.update_history(
                            actor=self.WF_USER,
                            content=input_bundle.history_text,
                            action={"action": "user_input"},
                            log_console=log_console,
                        )
                        self.WORKFLOW_TURN = INTERLOCUTOR
                continue

            # ---------------------------------------------------------
            # MAIN AGENT TURN
            # ---------------------------------------------------------
            if turn in ["system", "assistant", "agent", self.agent.name.strip().lower(), None]:
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
