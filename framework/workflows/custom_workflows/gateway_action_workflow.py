from __future__ import annotations

import asyncio
import shlex
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from framework.infrastructure.base_infrastructure import BaseInfrastructure
from framework.utils.io_tools import console
from framework.utils.json_parsing import robust_jsonfy
from framework.utils.multimodal_input import combine_prompt_with_user_content
from framework.workflows.base_workflow import BaseWorkflow
from framework.workflows.sessions_data_models import BaseSession
from framework.workflows.workflow_models import Actions as FullActions


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
        wf_rules_file: str | None = "config/preferences/rules/workflow/basewf.md",
        wf_agent_behaviour_file: str | None = "config/preferences/behaviour/workflow/basewf.md",
        wf_agent_sys_prompt_file: str | None = "config/preferences/prompts/workflow/basewf_default_assistant_sys_prompt.md",
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
        self.gateway_action_policy: Dict[str, Any] = {
            "allow_write_file": False,
            "allow_run_syscall": False,
            "syscall_allowed_commands": ["pwd", "ls", "cat", "head", "tail", "grep", "find", "wc", "echo"],
            "syscall_max_timeout": 10,
            "syscall_allow_shell": False,
            "syscall_deny_patterns": ["rm", "sudo", "su", "chmod", "chown", "mkfs", "dd", "shutdown", "reboot", "kill", "pkill", "curl", "wget", "ssh", "scp", "nc", "python", "python3", "pip", "uv", "git"],
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
    ) -> List[Dict[str, Any]]:
        """Process one websocket user message and return transport-safe events.

        Modes:
        - single_step: exactly one validated actor step after the user message.
        - wolf_loop: continue while turn routing remains with an assistant/system
          actor, stopping on send_message, yield to user, max_steps, or error.
        """
        started = time.perf_counter()
        events: List[Dict[str, Any]] = []
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
            actor, actor_name = self._actor_for_current_turn()
            if actor is None:
                stop_reason = "yield_to_user" if self._turn_is_user() else "unknown_turn"
                break

            step += 1
            events.append(self._event("workflow_status", "thinking", step=step, content=f"{actor_name} is thinking."))
            step_events, outcome = await self._process_actor_step(actor, actor_name, step, log_console=log_console)
            events.extend(step_events)

            if outcome.get("error"):
                stop_reason = "error"
                break
            if outcome.get("action") == "send_message":
                stop_reason = "send_message"
                break
            if self._turn_is_user():
                stop_reason = "yield_to_user"
                break
            if mode == "single_step":
                stop_reason = "single_step"
                break

        elapsed_ms = int((time.perf_counter() - started) * 1000)
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

    # ------------------------------------------------------------------
    # Actor-step implementation adapted from TurnBasedWorkflow._handle_actor_turn
    # ------------------------------------------------------------------
    async def _process_actor_step(self, actor: Any, name: str, step: int, log_console: bool = False):
        started = time.perf_counter()
        events: List[Dict[str, Any]] = []
        before_history_len = len(getattr(self.chat_manager, "CHAT_HISTORY", []))

        try:
            context_str = self.context_manager.get_compacted_context()
            _diagnostics = self.context_manager.get_context_diagnostics()

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
                    events.append(self._event("workflow_error", "error", step=step, content=err, error=err))
                    return events, {"error": True}

            response_dict = self._response_to_dict(response)
            if not isinstance(response_dict, dict) or "action" not in response_dict:
                err = f"[ERROR] Invalid action payload: {response}"
                self.WORKFLOW_TURN = "user"
                self.update_history(actor="system", content=err, action={"action": "system_error"}, log_console=log_console)
                events.append(self._event("workflow_error", "error", step=step, content=err, error=err))
                return events, {"error": True}

            bad_format, err_msg, action_obj, normalized = self.normalize_and_validate_agent_response(response_dict, actor)
            if bad_format:
                self.WORKFLOW_TURN = "user"
                self.update_history(actor="system", content=err_msg, action={"action": "system_info"}, log_console=log_console)
                events.append(self._event("workflow_error", "error", step=step, content=err_msg, error=err_msg, normalized=normalized))
                return events, {"error": True}

            action_name = normalized.get("action")
            guard_ok, guard_msg = self._guard_action_execution(action_obj, normalized)
            if not guard_ok:
                self.WORKFLOW_TURN = "user"
                self.update_history(actor="system", content=guard_msg, action={"action": "system_info"}, log_console=log_console)
                events.append(self._event("workflow_error", "error", step=step, action=action_name, content=guard_msg, error=guard_msg, payload=normalized))
                return events, {"error": True, "action": action_name}

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
            if self._should_defer_gui_action(action_name):
                result = self._deferred_gui_result(action_name, normalized)
                try:
                    self.infra.append_chat_history(
                        actor="system",
                        content=f"[GUI] {action_name} deferred to connected GUI client over websocket.",
                        action={"action": "system_info"},
                        log_console=log_console,
                    )
                except Exception:
                    pass
            else:
                if str(action_name or "").startswith("gui_"):
                    prepared = self._prepare_gui_direct_action(normalized)
                    if prepared is not normalized:
                        bad_format, err_msg, action_obj, normalized = self.normalize_and_validate_agent_response(prepared, actor)
                        if bad_format:
                            self.WORKFLOW_TURN = "user"
                            self.update_history(actor="system", content=err_msg, action={"action": "system_info"}, log_console=log_console)
                            events.append(self._event("workflow_error", "error", step=step, content=err_msg, error=err_msg, normalized=normalized))
                            return events, {"error": True, "action": action_name}
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
            events.append(
                self._event(
                    "workflow_result",
                    "done",
                    step=step,
                    action=action_name,
                    content=content,
                    result=self._safe_jsonish(result),
                    history_delta=history_delta,
                    elapsed_ms=elapsed_ms,
                    next_turn=self.WORKFLOW_TURN,
                )
            )
            return events, {"error": False, "action": action_name, "next_turn": self.WORKFLOW_TURN}
        except Exception as exc:
            err = f"Gateway workflow step failed: {type(exc).__name__}: {exc}"
            self.WORKFLOW_TURN = "user"
            try:
                self.update_history(actor="system", content=err, action={"action": "system_error"}, log_console=log_console)
            except Exception:
                pass
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

    def _deferred_gui_result(self, action_name: str, normalized: Dict[str, Any]) -> Dict[str, Any]:
        payload = normalized.get("payload", {}) if isinstance(normalized, dict) else {}
        return {
            "ok": True,
            "deferred_to_gui_client": True,
            "route": "client_event",
            "gui_command": {
                "action": action_name,
                "payload": payload if isinstance(payload, dict) else {},
            },
            "message": f"{action_name} was forwarded to the connected GUI client for local execution.",
        }

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

        policy = getattr(self, "gateway_action_policy", {}) or {}
        gui_url = str(policy.get("gui_url") or "").strip().rstrip("/")
        if not gui_url:
            return normalized

        payload = normalized.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        # Preserve an explicit model-provided gui_url, but inject the route
        # resolved by the gateway when the model did not provide one.
        if str(payload.get("gui_url") or "").strip():
            return normalized

        prepared = dict(normalized)
        prepared["payload"] = {**payload, "gui_url": gui_url}
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

        # gui_capture_* is always deferred to the connected browser client,
        # where the live user toggle is enforced. Do not block it here based
        # on stale server-side policy; otherwise the browser cannot approve or
        # deny the request at command time.

        if action_name != "run_syscall":
            return True, "ok"

        if not policy.get("allow_run_syscall", False):
            return False, "run_syscall is disabled by the current gateway action policy."

        payload = getattr(action_obj, "payload", None)
        command = getattr(payload, "command", None)
        timeout = int(getattr(payload, "timeout", 30) or 30)
        shell = bool(getattr(payload, "shell", False))

        max_timeout = int(policy.get("syscall_max_timeout", 10) or 10)
        if timeout > max_timeout:
            try:
                payload.timeout = max_timeout
            except Exception:
                pass

        if shell and not policy.get("syscall_allow_shell", False):
            return False, "run_syscall with shell=True is disabled by the current gateway action policy. Use a simple command/list form."

        if isinstance(command, list):
            parts = [str(p) for p in command]
            command_text = " ".join(parts)
        else:
            command_text = str(command or "")
            try:
                parts = shlex.split(command_text)
            except Exception as exc:
                return False, f"run_syscall command could not be parsed safely: {exc}"

        if not parts:
            return False, "run_syscall command is empty."

        # Reject shell composition/metacharacters even when shell=False; this
        # keeps the first dev-mode rollout intentionally narrow.
        forbidden_chars = [";", "&&", "||", "|", "`", "$", ">", "<", "\n", "\r"]
        if any(ch in command_text for ch in forbidden_chars):
            return False, "run_syscall command contains shell composition/metacharacters blocked by gateway policy."

        base = Path(parts[0]).name
        allowed = set(policy.get("syscall_allowed_commands") or [])
        if base not in allowed:
            return False, f"run_syscall command '{base}' is not in the gateway allowlist: {sorted(allowed)}"

        deny = set(policy.get("syscall_deny_patterns") or [])
        lowered_parts = [p.lower() for p in parts]
        if any(Path(p).name.lower() in deny or p.lower() in deny for p in lowered_parts):
            return False, "run_syscall command contains a denied executable/pattern."

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

    def _result_content(self, action_name: str, normalized: Dict[str, Any], result: Any, history_delta: List[Dict[str, Any]]) -> str:
        if action_name == "send_message":
            payload = normalized.get("payload", {})
            if isinstance(payload, dict):
                return str(payload.get("message", ""))
        if result is not None:
            return str(result)
        if history_delta:
            last = history_delta[-1]
            for key in ("content", "message", "text"):
                if key in last:
                    return str(last[key])
            return str(last)
        return f"{action_name} completed."

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
