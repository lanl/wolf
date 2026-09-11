from __future__ import annotations

import asyncio
import base64
import json
import shlex
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from framework.infrastructure.base_infrastructure import BaseInfrastructure
from framework.utils.io_tools import WOLF_PATH, console
from framework.utils.json_parsing import robust_jsonfy
from framework.utils.multimodal_input import combine_prompt_with_user_content
from framework.workflows.base_workflow import BaseWorkflow
from framework.workflows.sessions_data_models import BaseSession
from framework.workflows.workflow_models import Actions as FullActions
from framework.workflows.action_registry import get_default_action_registry
from framework.workflows.action_validation import ActionValidationError, validate_action_response


DEFAULT_GATEWAY_SAFE_ACTIONS = [
    "send_message",
    "read_file",
    "check_context_utilization",
    "list_memory_categories",
]


class GatewayActionWorkflow(BaseWorkflow):
    """Async, websocket-friendly WOLF action workflow.

    The gateway owns authentication, websocket transport, session lookup, and
    locking. This workflow owns WOLF orchestration semantics: prompt building,
    action generation, normalization, validation, execution, history updates,
    and turn policy.
    """

    def __init__(
        self,
        session: BaseSession | str | None = None,
        infra: BaseInfrastructure | None = None,
        actions_union: Any = FullActions,
        wf_rules_file: str | None = str(WOLF_PATH / "config/preferences/rules/workflow/basewf.md"),
        wf_agent_behaviour_file: str | None = str(WOLF_PATH / "config/preferences/behaviour/workflow/basewf.md"),
        wf_agent_sys_prompt_file: str | None = str(WOLF_PATH / "config/preferences/prompts/workflow/basewf_default_assistant_sys_prompt.md"),
        wf_user: str = "user",
        wf_turn: Any = None,
    ):
        super().__init__(
            session=session,
            infra=infra,
            actions_union=actions_union,
            wf_rules_file=wf_rules_file,
            wf_agent_behaviour_file=wf_agent_behaviour_file,
            wf_agent_sys_prompt_file=wf_agent_sys_prompt_file,
            wf_user=wf_user,
            wf_turn=wf_turn,
            WF_TAG="GatewayActionWorkflow",
        )
        self.WF_TAG = "GatewayActionWorkflow"
        self.action_registry = get_default_action_registry()
        self.gateway_action_policy: Dict[str, Any] = {
            "allow_write_file": False,
            "allow_run_syscall": False,
            "allow_create_universe": False,
            "allow_terminate_deployment": False,
            "allow_universe_tb_execute": False,
            "allow_universe_kb_purge": False,
            "syscall_allowed_commands": ["pwd", "ls", "cat", "head", "tail", "grep", "find", "wc", "echo", "git", "python", "python3"],
            "syscall_max_timeout": 900,
            "syscall_allow_shell": False,
            "syscall_request_approval_for_shell": True,
            "syscall_deny_patterns": ["rm", "sudo", "su", "chmod", "chown", "mkfs", "dd", "shutdown", "reboot", "kill", "pkill", "curl", "wget", "ssh", "scp", "nc", "pip", "uv"],
            "syscall_credential_exfiltration_patterns": ["aws_secret_access_key", "aws_access_key_id", "github_token", "ghp_", "authorization:", "bearer ", "id_rsa", "id_dsa", "id_ed25519", "private_key", "begin openssh private key", "begin rsa private key"],
            # GUI actions may execute directly from the gateway if the GUI API is
            # routable, or be deferred to the connected GUI client over websocket.
            "gui_action_route": "direct",
            "gui_api_reachable": None,
        }

    # ------------------------------------------------------------------
    # Public API used by gateway.py
    # ------------------------------------------------------------------
    async def process_user_message(
        self,
        user_text: str,
        user_name: str = "user",
        action_names: Optional[List[str]] = None,
        mode: str = "single_step",
        max_steps: int = 1,
        log_console: bool = False,
        execution_policy: Optional[Dict[str, Any]] = None,
        control_state: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Process one websocket user message and return transport-safe events.

        Modes:
        - single_step: exactly one validated actor step after the user message.
        - wolf_loop: continue while turn routing remains with an assistant/system
          actor, stopping on send_message, yield to user, max_steps, or error.
        """
        started = time.perf_counter()
        events: List[Dict[str, Any]] = []
        self.emit_event("workflow_received", "received", step=0, content="User message received by gateway workflow.", user_name=user_name)
        mode = (mode or "single_step").strip().lower()
        if mode not in {"single_step", "wolf_loop"}:
            mode = "single_step"
        if mode == "single_step":
            max_steps = 1
        max_steps = max(1, int(max_steps or 1))
        if execution_policy is not None:
            self.gateway_action_policy = {**self.gateway_action_policy, **execution_policy}

        allowed_actions = action_names or DEFAULT_GATEWAY_SAFE_ACTIONS
        self.set_wf_action_space(allowed_actions)
        self.WF_USER = user_name
        self.infra.ROLEs[user_name] = "user"

        events.append(self._event("workflow_status", "received", step=0, content="User message received."))
        self.emit_event("workflow_started", "running", step=0, content="Preparing user input and workflow state.", mode=mode, max_steps=max_steps)

        # Mirror TurnBasedWorkflow user-turn semantics: process multimodal input,
        # append compact history text, then route turn to the target actor.
        input_bundle = self.infra.prepare_user_input_for_agent(user_text, agent=self.agent)
        self.update_history(
            actor=self.WF_USER,
            content=input_bundle.history_text,
            action={"action": "user_input"},
            log_console=log_console,
        )
        self.WORKFLOW_TURN = self.agent.name

        step = 0
        stop_reason = "max_steps"
        while step < max_steps:
            control_events, control_stop = await self._checkpoint_control(control_state, step=step, phase="before_step", log_console=log_console)
            events.extend(control_events)
            if control_stop:
                stop_reason = control_stop
                break

            actor, actor_name = self._actor_for_current_turn()
            if actor is None:
                stop_reason = "yield_to_user" if self._turn_is_user() else "unknown_turn"
                break

            step += 1
            self.emit_event("workflow_step_started", "running", step=step, actor=actor_name, content=f"{actor_name} is starting workflow step {step}.")
            events.append(self._event("workflow_status", "thinking", step=step, content=f"{actor_name} is thinking."))
            step_events, outcome = await self._process_actor_step(actor, actor_name, step, log_console=log_console)
            events.extend(step_events)

            control_events, control_stop = await self._checkpoint_control(control_state, step=step, phase="after_step", log_console=log_console)
            events.extend(control_events)
            if control_stop:
                stop_reason = control_stop
                break

            if outcome.get("error"):
                self.emit_event("workflow_step_completed", "error", step=step, actor=actor_name, action=outcome.get("action"), content="Workflow step ended with an error.")
                stop_reason = "error"
                break
            self.emit_event("workflow_step_completed", "done", step=step, actor=actor_name, action=outcome.get("action"), next_turn=outcome.get("next_turn"), content="Workflow step completed.")
            if outcome.get("deferred_gui_action"):
                stop_reason = "waiting_for_gui_command_result"
                events.append(self._event(
                    "workflow_status",
                    "waiting",
                    step=step,
                    action=outcome.get("action"),
                    content="Deferred GUI command emitted; waiting for the connected VUI/browser to return gui_command_result before continuing.",
                    wait_reason="waiting_for_gui_command_result",
                ))
                break
            if outcome.get("action") == "send_message":
                stop_reason = "send_message"
                break
            if self._turn_is_user():
                if mode == "wolf_loop" and step < max_steps:
                    events.append(self._event(
                        "workflow_status",
                        "continuing",
                        step=step,
                        content="Tool/action result returned control to user; continuing agent loop so the agent can summarize/respond.",
                        previous_next_turn=outcome.get("next_turn"),
                    ))
                    self.WORKFLOW_TURN = self.agent.name
                    continue
                stop_reason = "yield_to_user"
                break
            if mode == "single_step":
                stop_reason = "single_step"
                break

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self.emit_event("workflow_completed", "done", step=step, content=f"Workflow turn complete ({stop_reason}).", stop_reason=stop_reason, elapsed_ms=elapsed_ms)
        events.append(
            self._event(
                "workflow_status",
                "done",
                step=step,
                content=f"Workflow turn complete ({stop_reason}).",
                elapsed_ms=elapsed_ms,
                stop_reason=stop_reason,
            )
        )
        return events

    async def _checkpoint_control(self, control_state: Optional[Dict[str, Any]], step: int, phase: str, log_console: bool = False):
        """Cooperative run-control checkpoint.

        The gateway updates ``control_state`` from websocket/REST control
        messages while this workflow is between model/tool steps.  This method
        never hard-kills an in-flight tool call; it only pauses, stops, or
        injects reassessment messages at safe boundaries.
        """
        events: List[Dict[str, Any]] = []
        if not isinstance(control_state, dict):
            return events, None

        async def _consume_pending() -> None:
            pending = list(control_state.get("pending_user_messages") or [])
            if not pending:
                return
            control_state["pending_user_messages"] = []
            control_state["reassess_requested"] = False
            for item in pending:
                if not isinstance(item, dict):
                    item = {"content": str(item)}
                content = str(item.get("content") or item.get("message") or "").strip()
                if not content:
                    continue
                note = (
                    "[USER INTERRUPT / REASSESSMENT REQUEST]\n"
                    "The user sent this while the agent was working. Reassess the current plan "
                    "and incorporate this instruction before continuing.\n"
                    f"{content}"
                )
                self.update_history(actor=self.WF_USER, content=note, action={"action": "user_interrupt", "phase": phase}, log_console=log_console)
                self.WORKFLOW_TURN = self.agent.name
                events.append(self._event("workflow_control", "reassessing", step=step, phase=phase, content="Queued user message applied; agent will reassess."))

        await _consume_pending()

        if control_state.get("stop_requested"):
            control_state["status"] = "stopped"
            events.append(self._event("workflow_control", "stopped", step=step, phase=phase, content="Agent stopped at a safe checkpoint."))
            return events, "stopped_by_user"

        if control_state.get("pause_requested"):
            control_state["status"] = "paused"
            events.append(self._event("workflow_control", "paused", step=step, phase=phase, content="Agent paused at a safe checkpoint."))
            while control_state.get("pause_requested") and not control_state.get("stop_requested"):
                await asyncio.sleep(0.2)
                await _consume_pending()
            if control_state.get("stop_requested"):
                control_state["status"] = "stopped"
                events.append(self._event("workflow_control", "stopped", step=step, phase=phase, content="Agent stopped while paused."))
                return events, "stopped_by_user"
            control_state["status"] = "running"
            events.append(self._event("workflow_control", "resumed", step=step, phase=phase, content="Agent resumed."))

        return events, None

    # ------------------------------------------------------------------
    # Actor-step implementation adapted from TurnBasedWorkflow._handle_actor_turn
    # ------------------------------------------------------------------
    async def _process_actor_step(self, actor: Any, name: str, step: int, log_console: bool = False):
        started = time.perf_counter()
        events: List[Dict[str, Any]] = []
        before_history_len = len(getattr(self.chat_manager, "CHAT_HISTORY", []))

        try:
            self.emit_event("context_build_started", "running", step=step, actor=name, content="Building compacted context for model call.")
            context_str = self.context_manager.get_compacted_context()
            _diagnostics = self.context_manager.get_context_diagnostics()
            self.emit_event(
                "context_build_completed",
                "done",
                step=step,
                actor=name,
                content="Compacted context ready.",
                context_tokens=_diagnostics.get("current_ctx_tokens") if isinstance(_diagnostics, dict) else None,
                max_ctx_tokens=_diagnostics.get("max_ctx_tokens") if isinstance(_diagnostics, dict) else None,
                utilization_pct=_diagnostics.get("utilization_pct") if isinstance(_diagnostics, dict) else None,
            )

            self.emit_event("prompt_assembly_started", "running", step=step, actor=name, content="Assembling agent prompt.")
            agent_prompt = (
                f"{self.agent_role_prompt}\n\n"
                "Below is the context formed from the current chat history:\n"
                "*** Context Start ***\n"
                f"{context_str}\n"
                "*** Context End ***\n\n"
                "The following are the actions you can take in response to the context\n"
                "*** List of allowed Actions Start *** \n"
                f"{self.schema_to_use}\n"
                "*** List of allowed Actions End *** \n"
                "*** Description of the infrastructure Start *** \n"
                f"{self.infra.INFRA_DESCRIPTION}\n"
                "*** Description of the infrastructure End *** \n"
                "*** Best Practices Start *** \n"
                f"{self.AGENT_BEHAVIOUR}\n"
                "*** Best Practices End *** \n"
                "*** WORKFLOW RULES Start *** \n"
                f"{self.WF_RULES}\n"
                "*** WORKFLOW RULES End *** \n\n"
            )
            pending_agent_content = self.infra.consume_pending_agent_content()
            agent_input = combine_prompt_with_user_content(agent_prompt, pending_agent_content)
            self.emit_event(
                "prompt_assembly_completed",
                "done",
                step=step,
                actor=name,
                content="Agent prompt assembled.",
                prompt_tokens=None,
                has_multimodal_content=bool(pending_agent_content),
                active_action_count=len(getattr(self, "action_names_to_use", []) or []),
            )

            self.emit_event("model_request_started", "running", step=step, actor=name, content=f"Calling model for {name}.", structured_output="structured_output" in getattr(actor, "capabilities", []))
            if "structured_output" in getattr(actor, "capabilities", []):
                response = await actor.get_structured_output_async(user_prompt=agent_input, output_format=self.Actions)
            else:
                # The gateway prompt already contains the selected action schema.
                # Do not call actor.format_agent_response(prompt, schema) here because
                # that appends the schema again and can leak/duplicate the full action
                # schema in fallback paths. Keep schema ownership in the workflow.
                bad_format, response, raw_response, result = await asyncio.to_thread(
                    self._format_actor_response_no_duplicate,
                    actor,
                    agent_input,
                )
                if bad_format:
                    err = f"{getattr(actor, 'name', name)} could not produce a valid response: {raw_response}"
                    self.WORKFLOW_TURN = "user"
                    self.update_history(actor="system", content=err, action={"action": "system_info"}, log_console=log_console)
                    self.emit_event("model_request_failed", "error", step=step, actor=name, content=err, error=err)
                    events.append(self._event("workflow_error", "error", step=step, content=err, error=err))
                    return events, {"error": True}

            self.emit_event("model_request_completed", "done", step=step, actor=name, content="Model response received.")
            response_dict = self._response_to_dict(response)
            if not isinstance(response_dict, dict) or "action" not in response_dict:
                summary = self._invalid_response_summary(response)
                err = f"[ERROR] Invalid action payload from model; expected one JSON action object. {summary}"
                self.WORKFLOW_TURN = "user"
                self.update_history(actor="system", content=err, action={"action": "system_error"}, log_console=log_console)
                events.append(self._event("workflow_error", "error", step=step, content=err, error=err, invalid_response_summary=summary))
                return events, {"error": True}

            self.emit_event("action_validation_started", "running", step=step, actor=name, action=response_dict.get("action"), content="Validating model action response.")
            allowed_for_validation = getattr(self, "action_names_to_use", None)
            validated = validate_action_response(
                response_dict,
                registry=self.action_registry,
                allowed_actions=allowed_for_validation,
                actor=actor,
            )
            if isinstance(validated, ActionValidationError):
                self.WORKFLOW_TURN = "user"
                err_msg = f"[Action validation {validated.stage}] {validated.message}"
                normalized = response_dict if isinstance(response_dict, dict) else None
                err_details = self._augment_validation_error_details(validated.model_dump(mode="json"), normalized)
                self.update_history(actor="system", content=err_details, action={"action": "action_validation_error"}, log_console=log_console)
                self.emit_event("action_validation_failed", "error", step=step, actor=name, action=validated.action, content=err_msg, validation_stage=validated.stage)
                events.append(
                    self._event(
                        "workflow_error",
                        "error",
                        step=step,
                        content=err_msg,
                        error=err_msg,
                        validation_stage=validated.stage,
                        action=validated.action,
                        details=err_details,
                        normalized=normalized,
                    )
                )
                return events, {"error": True}

            action_obj = validated.action_obj
            normalized = validated.normalized
            action_name = normalized.get("action")
            self.emit_event("action_validation_completed", "done", step=step, actor=name, action=action_name, content=f"Validated action {action_name}.")
            guard_ok, guard_msg = self._guard_action_execution(action_obj, normalized)
            if not guard_ok:
                self.WORKFLOW_TURN = "user"
                self.update_history(actor="system", content=guard_msg, action={"action": "system_info"}, log_console=log_console)
                blocked_event = self.emit_event(
                    "action_execution_failed",
                    "blocked",
                    step=step,
                    actor=name,
                    action=action_name,
                    content=guard_msg,
                    error=guard_msg,
                    payload=normalized,
                    recoverable_by_replan=True,
                    repair_hint=(
                        "This action was blocked by gateway execution policy. "
                        "A higher-level workflow may retry with a safer allowed action."
                    ),
                )
                events.append(blocked_event)
                events.append(self._event(
                    "workflow_error",
                    "error",
                    step=step,
                    action=action_name,
                    content=guard_msg,
                    error=guard_msg,
                    payload=normalized,
                    recoverable_by_replan=True,
                ))
                return events, {"error": True, "action": action_name, "recoverable_by_replan": True}

            self.emit_event("action_execution_started", "running", step=step, actor=name, action=action_name, content=f"Executing {action_name}.")
            events.append(
                self._event(
                    "workflow_action",
                    "executing",
                    step=step,
                    action=action_name,
                    content=f"Executing {action_name}",
                    payload=normalized,
                )
            )

            self.update_history(actor=actor.name, content=normalized, action=action_name, log_console=log_console)
            deferred_gui_action = False
            if self._should_defer_gui_action(action_name):
                deferred_gui_action = True
                result = self._deferred_gui_result(action_name, normalized)
                try:
                    self.infra.append_chat_history(
                        actor="system",
                        content=f"[GUI] {action_name} deferred to connected GUI client over websocket. Waiting for gui_command_result before continuing.",
                        action={"action": "system_info", "deferred_gui_action": True},
                        log_console=log_console,
                    )
                except Exception:
                    pass
            else:
                if str(action_name or "").startswith("gui_"):
                    prepared = self._prepare_gui_direct_action(normalized)
                    if prepared is not normalized:
                        allowed_for_validation = getattr(self, "action_names_to_use", None)
                        prepared_validated = validate_action_response(
                            prepared,
                            registry=self.action_registry,
                            allowed_actions=allowed_for_validation,
                            actor=actor,
                        )
                        if isinstance(prepared_validated, ActionValidationError):
                            self.WORKFLOW_TURN = "user"
                            err_msg = f"[Action validation {prepared_validated.stage}] {prepared_validated.message}"
                            err_details = self._augment_validation_error_details(prepared_validated.model_dump(mode="json"), prepared)
                            self.update_history(actor="system", content=err_details, action={"action": "action_validation_error"}, log_console=log_console)
                            events.append(
                                self._event(
                                    "workflow_error",
                                    "error",
                                    step=step,
                                    content=err_msg,
                                    error=err_msg,
                                    validation_stage=prepared_validated.stage,
                                    action=prepared_validated.action,
                                    details=err_details,
                                    normalized=prepared,
                                )
                            )
                            return events, {"error": True, "action": action_name}
                        action_obj = prepared_validated.action_obj
                        normalized = prepared_validated.normalized
                result = await asyncio.to_thread(action_obj.execute, infra=self.infra)

            # Existing action execute() methods often append directly to infra;
            # save after execution so gateway side effects are snapshotted too.
            try:
                self.save_session_state()
            except Exception as save_exc:
                console.print(f"[!][GatewayActionWorkflow] snapshot after action failed: {save_exc}")

            if getattr(action_obj, "yield_motion_to", None):
                self.WORKFLOW_TURN = action_obj.yield_motion_to
            elif getattr(action_obj, "receiver", None):
                self.WORKFLOW_TURN = action_obj.receiver
            else:
                self.WORKFLOW_TURN = "system"

            history_delta = self._history_delta(before_history_len)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            content = self._result_content(action_name, normalized, result, history_delta)
            public_history_delta = self._transport_history_delta(action_name, history_delta)
            public_result = self._transport_result(action_name, result)
            self.emit_event("action_execution_completed", "done", step=step, actor=name, action=action_name, content=f"Action {action_name} completed.", elapsed_ms=elapsed_ms, next_turn=self.WORKFLOW_TURN)
            events.append(
                self._event(
                    "workflow_result",
                    "done",
                    step=step,
                    action=action_name,
                    content=content,
                    result=public_result,
                    history_delta=public_history_delta,
                    raw_result_omitted=action_name != "send_message",
                    elapsed_ms=elapsed_ms,
                    next_turn=self.WORKFLOW_TURN,
                )
            )
            return events, {"error": False, "action": action_name, "next_turn": self.WORKFLOW_TURN, "deferred_gui_action": deferred_gui_action}
        except Exception as exc:
            err = f"Gateway workflow step failed: {type(exc).__name__}: {exc}"
            self.WORKFLOW_TURN = "user"
            try:
                self.update_history(actor="system", content=err, action={"action": "system_error"}, log_console=log_console)
            except Exception:
                pass
            self.emit_event("workflow_error", "error", step=step, actor=name, content=err, error=str(exc))
            events.append(self._event("workflow_error", "error", step=step, content=err, error=err))
            return events, {"error": True}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _actor_for_current_turn(self):
        turn = (self.WORKFLOW_TURN or "").strip().lower()
        if turn in {"system", "assistant", "agent", self.agent.name.strip().lower()}:
            return self.agent, self.agent.name
        for worker_name, worker in self.workers.items():
            if turn == worker_name.strip().lower() or turn == getattr(worker, "name", "").strip().lower():
                return worker, getattr(worker, "name", worker_name)
        return None, None

    def _turn_is_user(self) -> bool:
        turn = (self.WORKFLOW_TURN or "").strip().lower()
        return turn in {"user", (self.WF_USER or "user").strip().lower()}

    def _format_actor_response_no_duplicate(self, actor: Any, agent_input: Any, n_max_trials: int = 3):
        """Fallback JSON parser for models without structured-output support.

        `agent_input` already includes the effective gateway action schema.
        This helper intentionally does not append `self.schema_to_use` to the
        first model call, avoiding schema duplication and accidental leakage of
        a broader schema. If the response is malformed, the repair prompt uses
        the effective restricted schema only.
        """
        raw = None
        result: Dict[str, Any] = {}
        for _ in range(max(1, int(n_max_trials or 1))):
            raw = actor.get_chat_response(user_prompt=agent_input)
            result = robust_jsonfy(raw)
            if "parsed" in result:
                return False, result["parsed"], raw, result

            repair_prompt = (
                "Please fix the following response so it is exactly one valid JSON "
                "action object matching this action schema. Return JSON only.\n\n"
                f"Bad response parse result:\n{result}\n\n"
                f"Effective allowed action schema:\n{self.schema_to_use}"
            )
            raw = actor.get_chat_response(user_prompt=repair_prompt)
            result = robust_jsonfy(raw)
            if "parsed" in result:
                return False, result["parsed"], raw, result
        return True, None, raw, result

    def _invalid_response_summary(self, response: Any) -> str:
        """Return a bounded, prompt-safe summary of malformed model output."""
        try:
            if isinstance(response, dict):
                keys = sorted(str(k) for k in response.keys())
                if "jsonify_error" in response:
                    return f"Malformed JSON response contained jsonify_error; keys={keys}; content redacted to avoid prompt leakage."
                if "raw" in response:
                    return f"Malformed JSON response contained raw field; keys={keys}; raw content redacted."
                compact = {k: (str(v)[:300] + ("..." if len(str(v)) > 300 else "")) for k, v in response.items()}
                return f"Response dict keys={keys}; preview={compact}"
            text = str(response)
            if len(text) > 1000:
                text = text[:1000] + "..."
            return f"Response type={type(response).__name__}; preview={text}"
        except Exception as exc:
            return f"Response type={type(response).__name__}; summary_failed={exc}"

    def _response_to_dict(self, response: Any) -> Optional[Dict[str, Any]]:
        """Normalize possible structured-output return shapes to an action dict.

        Depending on provider/backend, structured output may arrive as:
        - a plain dict
        - a Pydantic action model
        - an OpenAI parsed message with `.parsed`
        - a Pydantic dump containing a `parsed` field
        """
        if isinstance(response, dict):
            parsed = response.get("parsed")
            if isinstance(parsed, BaseModel):
                return parsed.model_dump(mode="json", by_alias=True)
            if isinstance(parsed, dict):
                return parsed
            return response

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, BaseModel):
            return parsed.model_dump(mode="json", by_alias=True)
        if isinstance(parsed, dict):
            return parsed

        if isinstance(response, BaseModel):
            dumped = response.model_dump(mode="json", by_alias=True)
            parsed_dump = dumped.get("parsed") if isinstance(dumped, dict) else None
            if isinstance(parsed_dump, dict):
                return parsed_dump
            return dumped

        if hasattr(response, "model_dump"):
            dumped = response.model_dump(mode="json", by_alias=True)
            if isinstance(dumped, dict):
                parsed_dump = dumped.get("parsed")
                if isinstance(parsed_dump, dict):
                    return parsed_dump
                return dumped
        return None


    def _augment_validation_error_details(self, details: Dict[str, Any], normalized: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Add action-specific repair guidance to validation errors surfaced to the GUI/user."""
        try:
            out = dict(details or {})
            action = out.get("action") or ((normalized or {}).get("action") if isinstance(normalized, dict) else None)
            payload = (normalized or {}).get("payload") if isinstance(normalized, dict) else {}
            if action == "gui_capture_workspace":
                bad_scope = payload.get("capture_scope") if isinstance(payload, dict) else None
                out["repair_hint"] = (
                    "For gui_capture_workspace use a canonical capture_scope. "
                    "Use 'annotation_regions' for selected/boxed annotation regions, "
                    "active_dashboard instead of dashboard for the whole dashboard, "
                    "or active_dashboard_panels/selected_panels for panel URL captures. "
                    f"Rejected capture_scope={bad_scope!r}."
                )
            return out
        except Exception:
            return details

    def _should_defer_gui_action(self, action_name: Optional[str]) -> bool:
        action = str(action_name or "")
        if not action.startswith("gui_"):
            return False
        # Pulling live visual workspace context must always be executed by the
        # connected browser GUI client. Only that client knows the current user
        # permission toggle state and live DOM/layout state.
        if action in {"gui_get_visual_context", "gui_capture_url", "gui_capture_workspace"}:
            return True
        policy = getattr(self, "gateway_action_policy", {}) or {}
        route = str(policy.get("gui_action_route") or "direct").strip().lower()
        return route in {"client_event", "client", "websocket"}

    def _bridge_payload_tag(self, type_name: str, payload: Dict[str, Any], *, fmt: str = "json", version: int = 1, max_chars: int = 65536) -> Optional[str]:
        """Encode a structured bridge payload in a bounded, explicit text envelope.

        This is a resilience path for layers that may stringify structured
        transport fields.  It is not an execution mechanism by itself: gateway.py
        must still parse only trusted fields, validate type/action allowlists, and
        enforce normal permission gates before forwarding anything to clients.
        """
        safe_type = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(type_name or "")).strip("_")
        safe_fmt = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(fmt or "json")).strip("_") or "json"
        if not safe_type or safe_fmt != "json":
            return None
        try:
            body = json.dumps(self._safe_jsonish(payload), separators=(",", ":"), sort_keys=True)
        except Exception:
            return None
        if len(body) > max_chars:
            return None
        return f'<wolf_bridge_payload type="{safe_type}" format="{safe_fmt}" version="{int(version)}">{body}</wolf_bridge_payload>'

    def _deferred_gui_result(self, action_name: str, normalized: Dict[str, Any]) -> Dict[str, Any]:
        payload = normalized.get("payload", {}) if isinstance(normalized, dict) else {}
        gui_command = {
            "action": action_name,
            "payload": payload if isinstance(payload, dict) else {},
        }
        bridge_payload = self._bridge_payload_tag("gui_command", gui_command)
        result = {
            "ok": True,
            "deferred_to_gui_client": True,
            "route": "client_event",
            "gui_command": gui_command,
            "message": f"{action_name} was forwarded to the connected GUI client for local execution.",
        }
        if bridge_payload:
            result["bridge_payloads"] = [bridge_payload]
        else:
            result["bridge_payload_omitted"] = True
        return result

    def _prepare_gui_direct_action(self, normalized: Dict[str, Any]) -> Dict[str, Any]:
        """Inject the resolved GUI API URL into direct-route gui_* actions.

        Gateway GUI actions can run in two modes:
        - client_event: return a gui_command for the browser client to execute.
        - direct: execute the Python gui_* AgentAction in the gateway process.

        Direct execution needs a routable GUI server URL. The browser announces
        its local GUI origin with gui_client_hello; gateway.py resolves/stores it
        in the execution policy as ``gui_url``. Agent-produced payloads usually
        omit that internal transport detail, so inject it here before revalidating
        the action model. If no URL is resolved, leave the payload unchanged so
        gui_actions.py falls back to WOLF_GUI_URL / http://127.0.0.1:8765.
        """
        if not isinstance(normalized, dict):
            return normalized
        action_name = str(normalized.get("action") or "")
        if not action_name.startswith("gui_"):
            return normalized

        payload = normalized.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        raw_payload_gui_url = str(payload.get("gui_url") or "").strip()
        normalized_payload_gui_url = raw_payload_gui_url.lower().rstrip("/")
        sentinel_gui_urls = {
            "default",
            "auto",
            "browser",
            "gui",
            "local",
            "none",
            "null",
            "current",
            "connected",
            "active",
            "this",
            "same",
            "workspace",
            "wolf_gui",
            "wolf-gui",
        }
        payload_gui_url_is_sentinel = normalized_payload_gui_url in sentinel_gui_urls

        policy = getattr(self, "gateway_action_policy", {}) or {}
        gui_url = str(policy.get("gui_url") or "").strip().rstrip("/")

        prepared = dict(normalized)
        prepared_payload = dict(payload)

        # Direct GUI actions execute inside the gateway process, so gui_url is an
        # internal transport endpoint, not an agent-authored semantic parameter.
        # Prefer the connected/resolved GUI URL whenever available. This prevents
        # model placeholders like "current" and invented localhost ports such as
        # http://127.0.0.1:3000 from being used literally.
        if gui_url:
            prepared_payload["gui_url"] = gui_url
        elif payload_gui_url_is_sentinel:
            # No resolved GUI URL is available. Remove the sentinel so
            # gui_actions.py can use WOLF_GUI_URL / DEFAULT_GUI_URL fallback.
            prepared_payload.pop("gui_url", None)
        elif raw_payload_gui_url:
            # No resolved GUI URL exists and the model supplied an actual URL.
            # Preserve it as a last-resort explicit endpoint.
            return normalized
        else:
            return normalized
        prepared["payload"] = prepared_payload
        return prepared

    def _guard_action_execution(self, action_obj: Any, normalized: Dict[str, Any]) -> tuple[bool, str]:
        """Fail-closed guardrails for risky gateway-enabled actions.

        This is a gateway-side defense-in-depth layer. The action schema allowlist
        controls what the model can emit; this method controls whether risky
        emitted actions are actually executed.
        """
        action_name = normalized.get("action")
        policy = getattr(self, "gateway_action_policy", {}) or {}

        if action_name == "write_file" and not policy.get("allow_write_file", False):
            return False, "write_file is disabled by the current gateway action policy."

        risky_policy_flags = {
            "create_universe": "allow_create_universe",
            "terminate_deployment": "allow_terminate_deployment",
            "universe_tb_execute": "allow_universe_tb_execute",
            "universe_kb_purge": "allow_universe_kb_purge",
        }
        required_flag = risky_policy_flags.get(str(action_name or ""))
        if required_flag and not policy.get(required_flag, False):
            return False, f"{action_name} is disabled by the current gateway action policy ({required_flag}=false)."

        # gui_capture_* is always deferred to the connected browser client,
        # where the live user toggle is enforced. Do not block it here based
        # on stale server-side policy; otherwise the browser cannot approve or
        # deny the request at command time.

        if action_name != "run_syscall":
            return True, "ok"

        if not policy.get("allow_run_syscall", False):
            return False, "run_syscall is disabled by the current gateway action policy."

        payload = getattr(action_obj, "payload", None)
        timeout = int(getattr(payload, "timeout", 30) or 30)

        max_timeout = int(policy.get("syscall_max_timeout", 10) or 10)
        if timeout > max_timeout:
            try:
                payload.timeout = max_timeout
            except Exception:
                pass

        # The run_syscall action now supports three mutually-exclusive command
        # transports: legacy command, command_args, and script_lines.  Gateway
        # guardrails must inspect the resolved subprocess form rather than only
        # payload.command, otherwise valid command_args payloads look empty.
        try:
            if hasattr(payload, "subprocess_args"):
                command_obj, shell = payload.subprocess_args()
            else:
                command_obj = getattr(payload, "command", None)
                shell = bool(getattr(payload, "shell", False))
        except Exception as exc:
            return False, f"run_syscall command could not be resolved safely: {exc}"

        shell = bool(shell)

        argv_form = isinstance(command_obj, list)
        if argv_form:
            parts = [str(p) for p in command_obj]
            try:
                command_text = shlex.join(parts)
            except Exception:
                command_text = " ".join(parts)
        else:
            command_text = str(command_obj or "")
            try:
                parts = shlex.split(command_text) if not shell else [command_text]
            except Exception as exc:
                return False, f"run_syscall command could not be parsed safely: {exc}"

        if not parts or not str(parts[0]).strip():
            return False, "run_syscall command is empty."

        base = Path(parts[0]).name
        base_lower = base.lower()
        lowered_text = command_text.lower()
        lowered_parts = [p.lower() for p in parts]
        shell_wrappers = {"bash", "sh", "zsh", "fish", "ksh"}
        is_shell_wrapper = base_lower in shell_wrappers and any(str(p).lower() in {"-c", "-lc", "-ic"} for p in parts[1:3])
        forbidden_chars = [";", "&&", "||", "|", "`", "$", ">", "<", "\n", "\r"]
        # In argv/shell=False mode, semicolons/newlines inside arguments are data
        # for the executable, not shell composition. Only classify metacharacters
        # as elevated shell risk for real shell strings or shell wrappers.
        has_shell_metacharacters = any(ch in command_text for ch in forbidden_chars) if (shell or is_shell_wrapper or not argv_form) else False
        elevated_shell_risk = bool(shell or is_shell_wrapper or has_shell_metacharacters)

        # Explicit deny checks happen before allow/ask. These remain hard blocks.
        deny = {str(x).strip().lower() for x in (policy.get("syscall_deny_patterns") or []) if str(x).strip()}
        command_tokens = [Path(tok).name.lower() for tok in re.findall(r"[A-Za-z0-9_.+-]+", lowered_text)]
        if base_lower in deny or any(Path(p).name.lower() in deny or p.lower() in deny for p in lowered_parts):
            return False, "run_syscall command contains a denied executable/pattern."
        if any(tok in deny for tok in command_tokens):
            return False, "run_syscall command contains a denied executable/pattern."

        credential_patterns = [str(x).lower() for x in (policy.get("syscall_credential_exfiltration_patterns") or [])]
        if any(pattern and pattern in lowered_text for pattern in credential_patterns):
            return False, "run_syscall command appears to reference credential/secret exfiltration patterns blocked by gateway policy."

        allowed = {str(x).strip().lower() for x in (policy.get("syscall_allowed_commands") or []) if str(x).strip()}
        explicitly_allowed = base_lower in allowed

        # Explicit allowlist auto-runs only simple non-shell argv/string commands.
        # Shell/script forms remain elevated and route to user approval unless
        # explicitly denied above, even when their base command is allowlisted.
        if explicitly_allowed and not elevated_shell_risk:
            try:
                setattr(action_obj, "_gateway_syscall_approval_mode", "allow")
                setattr(action_obj, "_gateway_syscall_approval_reason", f"command '{base}' matched syscall allowlist")
            except Exception:
                pass
            return True, "ok"

        # Unknown commands and elevated shell/script forms are not silently blocked.
        # Let SysCallAction.execute() route them through the unified user approval bridge.
        # route them through the unified user approval bridge. Shell/script forms
        # are included here unless explicitly denied above.
        if elevated_shell_risk and shell and not policy.get("syscall_allow_shell", False) and not policy.get("syscall_request_approval_for_shell", True):
            return False, "run_syscall with shell=True is disabled by the current gateway action policy. Add the command to the allowlist, enable shell approval, or use command_args with shell=false."
        try:
            setattr(action_obj, "_gateway_syscall_approval_mode", "ask")
            setattr(action_obj, "_gateway_syscall_approval_reason", f"command '{base}' is neither explicitly allowed nor denied; requesting user approval")
        except Exception:
            pass
        return True, "ok"

    def _history_delta(self, before_len: int) -> List[Dict[str, Any]]:
        entries = getattr(self.chat_manager, "CHAT_HISTORY", [])[before_len:]
        out: List[Dict[str, Any]] = []
        for entry in entries:
            if isinstance(entry, dict):
                out.append(self._safe_jsonish(entry))
            else:
                out.append({"entry": str(entry)})
        return out

    def _message_content_from_payload(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        message = payload.get("message")
        if isinstance(message, str) and message:
            return message
        lines = payload.get("message_lines")
        if isinstance(lines, list):
            return "\n".join(str(line) for line in lines)
        encoded = payload.get("message_base64")
        if isinstance(encoded, str) and encoded:
            try:
                return base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-8")
            except Exception:
                return ""
        return ""

    def _bounded_text(self, value: Any, limit: int = 1000) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        return text[:limit] + f"... [omitted {len(text) - limit} chars]"

    def _action_target_summary(self, action_name: str, normalized: Dict[str, Any]) -> str:
        payload = normalized.get("payload", {}) if isinstance(normalized, dict) else {}
        if not isinstance(payload, dict):
            return ""
        if action_name in {"read_file", "write_file"}:
            return str(payload.get("file_path") or payload.get("path") or "").strip()
        if action_name == "run_syscall":
            if payload.get("command_args"):
                return " ".join(str(x) for x in payload.get("command_args") or [])
            if payload.get("command"):
                return str(payload.get("command"))
            if payload.get("script_lines"):
                return "script_lines"
        for key in ("query", "category", "key", "universe", "toolbox", "tool", "dashboard_id", "capture_scope", "url"):
            if payload.get(key):
                return f"{key}={payload.get(key)}"
        return ""

    def _result_content(self, action_name: str, normalized: Dict[str, Any], result: Any, history_delta: List[Dict[str, Any]]) -> str:
        """Return user/transport-facing content for a workflow_result.

        Only send_message is normal assistant prose. Other actions are tool
        observations and may contain large files/logs/secrets, so expose only a
        compact status line over websocket while preserving raw data in internal
        workflow history/context for the agent.
        """
        if action_name == "send_message":
            payload = normalized.get("payload", {})
            message = self._message_content_from_payload(payload)
            if message:
                return message
            return "Message sent."

        target = self._action_target_summary(action_name, normalized)
        if action_name == "read_file":
            return f"Read file {target or '<unknown>'} into workflow context. Raw file content is available internally to the agent."
        if action_name == "write_file":
            return f"write_file completed for {target or '<unknown>'}."
        if action_name == "run_syscall":
            if isinstance(result, dict):
                rc = result.get("returncode")
                approved = result.get("approved")
                return f"run_syscall completed for {target or '<command>'} with returncode={rc}, approved={approved}."
            return f"run_syscall completed for {target or '<command>'}."

        suffix = f" ({target})" if target else ""
        return f"Action {action_name}{suffix} completed. See action/result details for compact metadata."

    def _transport_result(self, action_name: str, result: Any) -> Any:
        """Return a transport-safe/bounded result for websocket events.

        Most non-message action results are compacted before websocket fanout.
        Deferred GUI actions are an exception: their bridge envelope must stay
        structured so gateway.py can forward the command to the browser client.
        """
        safe = self._safe_jsonish(result)
        if action_name == "send_message" or safe is None:
            return safe
        if isinstance(safe, dict):
            out: Dict[str, Any] = {}
            structured_bridge_keys = {"gui_command", "bridge_payload", "bridge_payloads"}
            for key, value in safe.items():
                if key in structured_bridge_keys:
                    # Keep strict bridge fields JSON-shaped instead of degrading
                    # nested dict/list values to Python repr strings.  The
                    # gateway still validates/allowlists before forwarding.
                    out[key] = value
                    continue
                if isinstance(value, str):
                    out[key] = self._bounded_text(value, 1200)
                    if len(value) > 1200:
                        out[f"{key}_omitted_chars"] = len(value) - 1200
                elif isinstance(value, (int, float, bool)) or value is None:
                    out[key] = value
                else:
                    text = str(value)
                    out[key] = self._bounded_text(text, 1200)
                    if len(text) > 1200:
                        out[f"{key}_omitted_chars"] = len(text) - 1200
            out.setdefault("raw_omitted", True)
            return out
        if isinstance(safe, str):
            return {"preview": self._bounded_text(safe, 1200), "omitted_chars": max(0, len(safe) - 1200), "raw_omitted": True}
        return safe

    def _transport_history_delta(self, action_name: str, history_delta: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return bounded history deltas for transport; internal history remains full."""
        if action_name == "send_message":
            return history_delta
        out: List[Dict[str, Any]] = []
        for entry in history_delta or []:
            if not isinstance(entry, dict):
                out.append({"entry_summary": self._bounded_text(entry, 500), "raw_omitted": True})
                continue
            compact: Dict[str, Any] = {}
            for key in ("sender", "timestamp", "action", "history_index"):
                if key in entry:
                    compact[key] = entry.get(key)
            content = entry.get("content")
            text = str(content or "")
            compact["content_summary"] = self._bounded_text(text, 700)
            compact["content_omitted"] = len(text) > 700
            compact["raw_omitted"] = True
            out.append(compact)
        return out

    def _safe_jsonish(self, value: Any):
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json", by_alias=True)
        if isinstance(value, dict):
            return {str(k): self._safe_jsonish(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._safe_jsonish(v) for v in value]
        if isinstance(value, tuple):
            return [self._safe_jsonish(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _event(self, event_type: str, status: str, step: int = 0, **extra: Any) -> Dict[str, Any]:
        evt: Dict[str, Any] = {
            "type": event_type,
            "status": status,
            "step": step,
            "workflow": self.WF_TAG,
            "timestamp": datetime.now().isoformat(),
        }
        evt.update(extra)
        return evt
