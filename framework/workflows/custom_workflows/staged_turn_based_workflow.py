from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, Optional

from framework.utils.io_tools import console
from framework.utils.json_parsing import robust_jsonfy
from framework.utils.multimodal_input import combine_prompt_with_user_content
from framework.workflows.action_validation import ActionValidationError
from framework.workflows.custom_workflows.turn_based_workflow import TurnBasedWorkflow
from framework.workflows.sessions_data_models import BaseSession
from framework.infrastructure.base_infrastructure import BaseInfrastructure
from framework.workflows.workflow_models import Actions as FullActions
from framework.workflows.staged_actions import StagedActionBuilder


class StagedTurnBasedWorkflow(TurnBasedWorkflow):
    """Experimental CLI workflow using staged action construction.

    This workflow keeps the normal TurnBasedWorkflow user loop but replaces the
    agent turn with a staged protocol:

    1. select action from a compact catalog;
    2. collect lightweight JSON for only the selected action;
    3. collect heavy text fields outside JSON when declared by the action spec;
    4. assemble and validate the final canonical action;
    5. record the final validated action and execute normally.

    The implementation is intentionally CLI-first and non-destructive to the
    existing TurnBasedWorkflow path.
    """

    WF_TAG = "StagedTurnBasedWorkflow"

    def __init__(
        self,
        session: BaseSession | str | None = None,
        infra: BaseInfrastructure | None = None,
        actions_union: Any = FullActions,
        wf_rules_file: str | None = "config/preferences/rules/workflow/basewf.md",
        wf_agent_behaviour_file: str | None = "config/preferences/behaviour/workflow/basewf.md",
        wf_agent_sys_prompt_file: str | None = "config/preferences/prompts/workflow/basewf_default_assistant_sys_prompt.md",
        wf_user: str = "user",
        wf_turn=None,
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
        )
        self.WF_TAG = "StagedTurnBasedWorkflow"
        try:
            self.infra.WF_TAG = self.WF_TAG
        except Exception:
            pass
        # Non-durable CLI streaming state.  Heavy fields are rendered as an
        # indented block and are not appended to durable history until the final
        # action validates.
        self._stream_block_active = False
        self._stream_block_line_start = True
        self._stream_block_prefix = "    │   │ "
        self._stream_envelope_active = False
        self._last_heavy_field_streamed = False
        self._last_staged_action_previewed = False
        self.staged_action_verbose = int(os.environ.get("WOLF_STAGED_ACTION_VERBOSE", "1") or 1)
        self.action_verbose = self.staged_action_verbose

    # ------------------------------------------------------------------
    # Ephemeral CLI status display
    # ------------------------------------------------------------------
    def _get_action_verbose(self) -> int:
        try:
            return int(getattr(self, "staged_action_verbose", getattr(self, "action_verbose", 1)) or 0)
        except Exception:
            return 1

    def _stream_status(
        self,
        message: str,
        *,
        event_type: str = "staged_status",
        status: str = "running",
        verbose_level: int = 2,
        **metadata: Any,
    ) -> None:
        """Display non-durable CLI progress and emit a workflow event.

        ``verbose_level`` controls terminal visibility only. Events remain
        available to observability listeners, but routine CLI status noise is
        hidden unless ``staged_action_verbose`` is high enough.

        Suggested levels:
          0: hide status; show only streamed heavy content and errors
          1: compact staged UX
          2: normal stage transitions
          3: model-call details
          4: repair/debug progress
          5: very verbose diagnostics
        """
        should_print = self._get_action_verbose() >= int(verbose_level) or status in {"error", "warning"}
        if should_print:
            try:
                console.print(f"[dim][staged][/dim] {message}")
            except Exception:
                print(f"[staged] {message}")
        try:
            self.emit_event(event_type, status, content=message, verbose_level=verbose_level, **metadata)
        except Exception:
            pass

    def _stream_json_line(self, line: str = "") -> None:
        try:
            sys.stdout.write(f"    │ {line}\n")
            sys.stdout.flush()
        except Exception:
            print(f"    │ {line}")

    def _preview_json_value(self, value: Any, *, max_chars: int = 500) -> Any:
        """Return a CLI-friendly preview value without changing durable data."""
        if isinstance(value, dict):
            return {str(k): self._preview_json_value(v, max_chars=max_chars) for k, v in value.items()}
        if isinstance(value, list):
            return [self._preview_json_value(v, max_chars=max_chars) for v in value]
        if isinstance(value, str) and len(value) > max_chars:
            return f"<<{len(value)} chars>> " + value[:max_chars] + "..."
        return value

    def _render_staged_action_preview(self, action_name: str, normalized: Dict[str, Any]) -> bool:
        """Render a compact non-durable final action preview for any action.

        This generalizes the good send_message UX to small-json actions such as
        read_file/run_syscall. The preview is only terminal display; the full
        validated action is still written to durable history/action metadata.
        """
        if self._get_action_verbose() < 1 or not isinstance(normalized, dict):
            return False
        preview = self._preview_json_value(normalized)
        try:
            sys.stdout.write(f"    ┌─ staged {action_name}\n")
            for line in json.dumps(preview, indent=2, ensure_ascii=False).splitlines():
                sys.stdout.write(f"    │ {line}\n")
            sys.stdout.write("    └─ end staged action\n")
            sys.stdout.flush()
            return True
        except Exception:
            try:
                print(f"    ┌─ staged {action_name}")
                print(json.dumps(preview, indent=2, ensure_ascii=False))
                print("    └─ end staged action")
                return True
            except Exception:
                return False

    def _render_heavy_json_prefix(self, action_name: str, small_json: Dict[str, Any], heavy_field: Any) -> None:
        """Render a non-durable partial action envelope before heavy text."""
        self._stream_envelope_active = self._get_action_verbose() >= 1
        if not self._stream_envelope_active:
            return
        payload = small_json.get("payload") if isinstance(small_json.get("payload"), dict) else {}
        field_name = getattr(heavy_field, "field_name", "content")
        try:
            sys.stdout.write(f"    ┌─ staged {action_name}\n")
            sys.stdout.flush()
        except Exception:
            print(f"    ┌─ staged {action_name}")
        self._stream_json_line('{')
        self._stream_json_line(f'  "action": {json.dumps(action_name)},')
        self._stream_json_line('  "payload": {')
        for key, value in payload.items():
            # The heavy field is shown as the upcoming streamed block instead.
            if key == field_name:
                continue
            self._stream_json_line(f'    "{key}": {json.dumps(value, ensure_ascii=False)},')
        self._stream_json_line(f'    "{field_name}": <<stream>>')

    def _render_heavy_json_suffix(self, small_json: Dict[str, Any]) -> None:
        """Render remaining universal fields after heavy text completes."""
        if not getattr(self, "_stream_envelope_active", False):
            return
        self._stream_json_line('  },')
        self._stream_json_line(f'  "purpose": {json.dumps(small_json.get("purpose", ""), ensure_ascii=False)},')
        self._stream_json_line(f'  "expectations": {json.dumps(small_json.get("expectations", ""), ensure_ascii=False)},')
        self._stream_json_line(f'  "yield_motion_to": {json.dumps(small_json.get("yield_motion_to", "user"), ensure_ascii=False)}')
        self._stream_json_line('}')
        try:
            sys.stdout.write("    └─ end staged action\n")
            sys.stdout.flush()
        except Exception:
            print("    └─ end staged action")

    def _begin_stream_block(self, *, action_name: Optional[str] = None, field: Optional[str] = None) -> None:
        """Start a non-durable indented CLI block for streamed heavy text."""
        self._stream_block_active = True
        self._stream_block_line_start = True
        nested = bool(getattr(self, "_stream_envelope_active", False))
        self._stream_block_prefix = "    │   │ " if nested else "    │ "
        prefix = "    │   ┌─" if nested else "    ┌─"
        try:
            label = f"streaming {action_name or 'action'}" + (f".{field}" if field else "")
            console.print(f"[dim]{prefix} {label}[/dim]")
        except Exception:
            print(f"{prefix} streaming {action_name or 'action'}" + (f".{field}" if field else ""))

    def _end_stream_block(self) -> None:
        """End the non-durable streamed CLI block cleanly."""
        if self._stream_block_active:
            nested = bool(getattr(self, "_stream_envelope_active", False))
            end_line = "    │   └─ end stream" if nested else "    └─ end stream"
            try:
                if not self._stream_block_line_start:
                    sys.stdout.write("\n")
                sys.stdout.write(end_line + "\n")
                sys.stdout.flush()
            except Exception:
                print("\n" + end_line)
        self._stream_block_active = False
        self._stream_block_line_start = True

    def _stream_text_delta(self, delta: str, *, event_type: str = "staged_text_delta", **metadata: Any) -> None:
        """Render a provisional text chunk to CLI without updating durable history.

        This intentionally uses raw stdout rather than Rich ``console.print`` for
        every token/chunk. Rich-per-chunk rendering is noticeably slow for long
        streamed messages. Per-delta workflow events are also disabled by default
        because recording thousands of token events can dominate latency.
        """
        if not delta:
            return

        # Avoid runaway vertical whitespace from provider chunk boundaries while
        # preserving normal paragraph breaks.
        text = str(delta).replace("\r\n", "\n").replace("\r", "\n")

        try:
            for ch in text:
                if self._stream_block_line_start:
                    sys.stdout.write(self._stream_block_prefix)
                    self._stream_block_line_start = False
                sys.stdout.write(ch)
                if ch == "\n":
                    self._stream_block_line_start = True
            sys.stdout.flush()
        except Exception:
            print(text, end="")

        if os.environ.get("WOLF_STAGED_STREAM_DELTA_EVENTS", "").lower() in {"1", "true", "yes"}:
            try:
                self.emit_event(event_type, "delta", delta=delta, **metadata)
            except Exception:
                pass

    def _call_text_model(
        self,
        actor: Any,
        prompt: Any,
        *,
        stage: str,
        action_name: Optional[str] = None,
        stream: bool = False,
        stream_field: Optional[str] = None,
    ) -> str:
        self._stream_status(
            f"model call started: {stage}" + (f" ({action_name})" if action_name else ""),
            event_type="staged_model_request_started",
            verbose_level=3,
            stage=stage,
            action=action_name,
            streaming=stream,
            field=stream_field,
        )

        if stream and hasattr(actor, "stream_chat_response"):
            chunks_seen = 0
            self._last_heavy_field_streamed = False
            self._begin_stream_block(action_name=action_name, field=stream_field)

            def _on_delta(delta: str) -> None:
                nonlocal chunks_seen
                chunks_seen += 1
                self._last_heavy_field_streamed = True
                self._stream_text_delta(
                    delta,
                    event_type="staged_heavy_field_delta",
                    stage=stage,
                    action=action_name,
                    field=stream_field,
                    chunk_index=chunks_seen,
                )

            try:
                raw = actor.stream_chat_response(
                    user_prompt=prompt,
                    on_delta=_on_delta,
                    print_deltas=False,
                )
                self._end_stream_block()
            except TypeError:
                self._end_stream_block()
                # Backward compatibility with older agent implementations that
                # expose stream_chat_response(prompt) but no callback controls.
                raw = actor.stream_chat_response(user_prompt=prompt)
            except Exception as exc:
                self._end_stream_block()
                self._stream_status(
                    f"streaming model call failed during {stage}; falling back to blocking call: {type(exc).__name__}: {exc}",
                    event_type="staged_model_stream_failed",
                    status="warning",
                    stage=stage,
                    action=action_name,
                    field=stream_field,
                )
                raw = actor.get_chat_response(user_prompt=prompt)
        else:
            raw = actor.get_chat_response(user_prompt=prompt)

        self._stream_status(
            f"model response received: {stage}" + (f" ({action_name})" if action_name else ""),
            event_type="staged_model_request_completed",
            status="done",
            verbose_level=3,
            stage=stage,
            action=action_name,
            streaming=stream,
            field=stream_field,
        )
        return raw

    def _parse_json_with_repair(self, actor: Any, raw: str, repair_context: str, *, stage: str, action_name: Optional[str] = None, max_repairs: int = 2) -> Optional[Dict[str, Any]]:
        candidate = raw
        for attempt in range(max(1, max_repairs + 1)):
            parsed = robust_jsonfy(candidate)
            if isinstance(parsed, dict) and "jsonify_error" not in parsed:
                return parsed
            if isinstance(parsed, dict) and isinstance(parsed.get("parsed"), dict):
                return parsed["parsed"]

            if attempt >= max_repairs:
                break

            self._stream_status(
                f"JSON parse failed during {stage}; requesting repair attempt {attempt + 1}/{max_repairs}",
                event_type="staged_json_repair_started",
                stage=stage,
                action=action_name,
                attempt=attempt + 1,
            )
            repair_prompt = (
                "Fix the following response so it is exactly one valid JSON object.\n"
                "Return JSON only. No prose, no Markdown fences, no comments.\n\n"
                f"Repair context:\n{repair_context}\n\n"
                f"Bad response:\n{candidate}"
            )
            candidate = self._call_text_model(actor, repair_prompt, stage=f"{stage}_json_repair", action_name=action_name)

        self._stream_status(
            f"JSON parse failed during {stage} after repair attempts.",
            event_type="staged_json_repair_failed",
            status="error",
            stage=stage,
            action=action_name,
        )
        return None

    def _staged_context_excerpt(self, context_str: str, *, max_chars: int = 8000) -> str:
        """Return a bounded context excerpt for staged prompts.

        Staged prompts should not re-inject the full legacy one-shot action
        protocol, large infrastructure/rules blocks, or previous prompt tails.
        This keeps Stage 1/2/3 smaller and reduces prompt leakage into heavy
        plain-text generations.
        """
        text = str(context_str or "")
        # Drop common prompt tails if they leaked into context/history.
        cut_markers = [
            "*** Description of the infrastructure Start ***",
            "*** Best Practices Start ***",
            "*** WORKFLOW RULES Start ***",
            "Action response protocol:",
            "HOT ACTIONS WITH PAYLOAD EXAMPLES",
            "COLD ACTIONS",
        ]
        first_cut = None
        for marker in cut_markers:
            idx = text.find(marker)
            if idx >= 0:
                first_cut = idx if first_cut is None else min(first_cut, idx)
        if first_cut is not None:
            text = text[:first_cut]
        if len(text) > max_chars:
            text = text[-max_chars:]
        return text.strip()

    def _selection_prompt(self, builder: StagedActionBuilder, context_str: str, actor: Any) -> str:
        context_excerpt = self._staged_context_excerpt(context_str, max_chars=9000)
        catalog_prompt = builder.render_action_selection_prompt(context=context_excerpt, include_context=True)
        return (
            "You are in WOLF staged-action mode.\n"
            "Stage 1: select only the next action name. Do not provide payload fields yet.\n"
            "Use the context to decide which action should happen next.\n\n"
            f"{catalog_prompt}\n\n"
            "Return only the exact action name, an alias such as A01, or tiny JSON like {\"action\": \"read_file\"}."
        )

    def _small_json_prompt(self, builder: StagedActionBuilder, action_name: str, context_str: str) -> str:
        context_excerpt = self._staged_context_excerpt(context_str, max_chars=9000)
        return (
            "You are in WOLF staged-action mode.\n"
            "Stage 2: provide lightweight JSON fields for the already-selected action.\n"
            "Do not change the selected action. Do not include heavy text fields.\n\n"
            f"{builder.render_small_json_prompt(action_name, context=context_excerpt, include_context=True)}\n\n"
            "Return JSON only. Include purpose, expectations, and yield_motion_to when possible."
        )

    def _latest_user_text_for_staged_repair(self, context_str: str = "") -> str:
        """Return the latest user-visible request text for deterministic staged repairs."""
        try:
            for entry in reversed(getattr(self.chat_manager, "CHAT_HISTORY", []) or []):
                sender = str(entry.get("sender") or entry.get("actor") or "").lower() if isinstance(entry, dict) else ""
                if sender in {str(self.WF_USER).lower(), "user"}:
                    content = entry.get("content")
                    if isinstance(content, str) and content.strip():
                        return content
        except Exception:
            pass
        return str(context_str or "")[-4000:]

    def _repair_small_json_from_context(self, action_name: str, small_json: Dict[str, Any], context_str: str) -> Dict[str, Any]:
        """Deterministically fill obvious missing lightweight fields.

        This is not a replacement for validation; it is a local staged-mode
        resilience layer for cases where Stage 1 chose the right action but
        Stage 2 omitted an obvious required field.
        """
        data = dict(small_json or {})
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        payload = dict(payload)
        latest = self._latest_user_text_for_staged_repair(context_str)
        latest_l = latest.lower()

        if action_name == "read_file":
            if "file_path" not in payload and "path" in payload:
                payload["file_path"] = payload.pop("path")
            if "file_path" not in payload:
                # Extract an obvious path-like token from the latest user request.
                # Prefer full relative paths like ./foo.md before absolute paths.
                candidates = []
                for match in re.finditer(r'(?P<path>(?:\./|\../|~/)[A-Za-z0-9_./\-]+|(?<!\.)/[A-Za-z0-9_./\-]+|[A-Za-z0-9_./\-]+\.(?:md|py|txt|json|yaml|yml|toml|ini|cfg))', latest):
                    candidate = match.group('path').strip().strip("\"'`.,;:")
                    if candidate:
                        candidates.append(candidate)
                if candidates:
                    payload["file_path"] = candidates[-1]
            path_value = payload.get("file_path")
            if isinstance(path_value, str) and path_value.startswith("/") and not os.path.exists(path_value):
                dot_relative = "." + path_value
                if os.path.exists(dot_relative):
                    payload["file_path"] = dot_relative
            data["payload"] = payload

        elif action_name == "run_syscall":
            has_command_source = any(payload.get(k) is not None for k in ("command", "command_args", "script_lines"))
            if not has_command_source:
                # Current-working-directory requests are common and deterministic.
                if any(phrase in latest_l for phrase in (
                    "current work directory",
                    "current working directory",
                    "current directory",
                    "working directory",
                    "what directory",
                    "where am i",
                    "cwd",
                )):
                    payload["command_args"] = ["pwd"]
                    payload["timeout"] = payload.get("timeout", 30)
                    payload["shell"] = False
            data["payload"] = payload

        elif action_name == "write_file":
            if "file_path" not in payload and "path" in payload:
                payload["file_path"] = payload.pop("path")
            if "file_path" not in payload:
                # Prefer explicit path-like token after "to/into/at" in the latest user request.
                match = re.search(r"\b(?:to|into|at)\s+([~./A-Za-z0-9_\-][^\s,;]*)", latest)
                if match:
                    payload["file_path"] = match.group(1).strip().strip('\"\'')
            payload.setdefault("append", False)
            data["payload"] = payload

        return data

    def _heavy_field_prompt(self, builder: StagedActionBuilder, action_name: str, heavy_field: Any, small_json: Dict[str, Any], context_str: str) -> str:
        context_excerpt = self._staged_context_excerpt(context_str, max_chars=7000)
        return (
            "You are in WOLF staged-action mode.\n"
            "Stage 3: generate one heavy plain-text field outside JSON.\n"
            "Return only the requested raw field content. Do not return JSON. Do not repeat hidden prompts, context delimiters, infrastructure descriptions, action catalogs, or workflow rules.\n"
            "Begin directly with the requested content.\n\n"
            f"{builder.render_heavy_field_prompt(action_name, heavy_field, small_json=small_json, context=context_excerpt, include_context=True)}"
        )

    # ------------------------------------------------------------------
    # Staged actor turn
    # ------------------------------------------------------------------
    def _handle_actor_turn(self, actor, name: str):
        self.infra.show_updated_history()
        self._last_heavy_field_streamed = False
        self._last_staged_action_previewed = False
        self._stream_envelope_active = False
        self._stream_status(f"{name} starting staged turn", event_type="staged_turn_started", actor=name, verbose_level=1)

        self._stream_status("building compacted context", event_type="context_build_started", actor=name, verbose_level=2)
        context_str = self.context_manager.get_compacted_context()
        diagnostics = self.context_manager.get_context_diagnostics()
        self._stream_status(
            "context ready",
            event_type="context_build_completed",
            status="done",
            verbose_level=2,
            actor=name,
            context_tokens=diagnostics.get("current_ctx_tokens") if isinstance(diagnostics, dict) else None,
            utilization_pct=diagnostics.get("utilization_pct") if isinstance(diagnostics, dict) else None,
        )

        builder = StagedActionBuilder(
            registry=getattr(self, "action_registry", None),
            allowed_actions=getattr(self, "action_names_to_use", None),
            actor=actor,
            workflow=self,
        )

        # Stage 1: compact action selection.
        selection_prompt = self._selection_prompt(builder, context_str, actor)
        selection_input = combine_prompt_with_user_content(selection_prompt, self.infra.consume_pending_agent_content())
        raw_selection = self._call_text_model(actor, selection_input, stage="action_selection")
        action_name = builder.parse_action_selection(raw_selection)

        if not action_name:
            repair_prompt = (
                "Your previous response did not select a valid action.\n"
                "Return only one exact action name from the compact catalog or tiny JSON like {\"action\": \"read_file\"}.\n\n"
                f"Previous response:\n{raw_selection}\n\n"
                f"{builder.render_action_selection_prompt(context=context_str, include_context=False)}"
            )
            raw_selection = self._call_text_model(actor, repair_prompt, stage="action_selection_repair")
            action_name = builder.parse_action_selection(raw_selection)

        if not action_name:
            self.WORKFLOW_TURN = "user"
            err = "[StagedActionWorkflow] Could not select a valid action."
            self.update_history(actor="system", content=err, action={"action": "system_info"}, log_console=True)
            return

        self._stream_status(
            f"selected action: {action_name}",
            event_type="staged_action_selected",
            status="done",
            verbose_level=1,
            actor=name,
            action=action_name,
        )

        # Stage 2: selected-action lightweight JSON.
        build_spec = builder.get_build_spec(action_name)
        self._stream_status(
            f"collecting lightweight JSON for {action_name}",
            event_type="staged_payload_json_started",
            verbose_level=2,
            actor=name,
            action=action_name,
            build_mode=build_spec.mode,
        )
        small_prompt = self._small_json_prompt(builder, action_name, context_str)
        raw_small_json = self._call_text_model(actor, small_prompt, stage="small_payload_json", action_name=action_name)
        small_json = self._parse_json_with_repair(
            actor,
            raw_small_json,
            builder.render_small_json_prompt(action_name, context="", include_context=False),
            stage="small_payload_json",
            action_name=action_name,
        )

        if small_json is None:
            self.WORKFLOW_TURN = "user"
            err = f"[StagedActionWorkflow] Could not collect valid lightweight JSON for {action_name}."
            self.update_history(actor="system", content=err, action={"action": "system_info"}, log_console=True)
            return

        small_json.setdefault("purpose", f"Perform {action_name} in response to the current user request.")
        small_json.setdefault("expectations", f"The {action_name} action completes successfully and advances the workflow.")
        small_json.setdefault("yield_motion_to", "user")
        small_json = self._repair_small_json_from_context(action_name, small_json, context_str)

        self._stream_status(
            f"lightweight JSON collected for {action_name}",
            event_type="staged_payload_json_completed",
            status="done",
            verbose_level=2,
            actor=name,
            action=action_name,
        )

        # Stage 3: heavy fields as plain text, if declared.
        heavy_values: Dict[str, Any] = {}
        for heavy_field in build_spec.heavy_fields:
            self._stream_status(
                f"collecting heavy field {heavy_field.field_name} for {action_name}",
                event_type="staged_heavy_field_started",
                verbose_level=1,
                actor=name,
                action=action_name,
                field=heavy_field.field_name,
                content_type=heavy_field.content_type,
            )
            heavy_prompt = self._heavy_field_prompt(builder, action_name, heavy_field, small_json, context_str)
            self._render_heavy_json_prefix(action_name, small_json, heavy_field)
            heavy_text = self._call_text_model(
                actor,
                heavy_prompt,
                stage="heavy_field_text",
                action_name=action_name,
                stream=bool(getattr(heavy_field, "stream_to_cli", False)),
                stream_field=heavy_field.field_name,
            )
            heavy_values[heavy_field.field_name] = heavy_text
            self._render_heavy_json_suffix(small_json)
            preview = heavy_text[:500] + ("..." if len(heavy_text) > 500 else "")
            self._stream_status(
                f"heavy field {heavy_field.field_name} collected ({len(heavy_text)} chars)",
                event_type="staged_heavy_field_completed",
                status="done",
                verbose_level=2,
                actor=name,
                action=action_name,
                field=heavy_field.field_name,
                char_count=len(heavy_text),
                preview=preview,
            )

        # Assemble and validate final action through existing validation path.
        result = builder.assemble_and_validate(action_name, small_json=small_json, heavy_values=heavy_values, actor=actor)

        # If the model produced syntactically valid JSON but invalid selected-action
        # payload, do not fail immediately. Ask for a targeted Stage-2 repair using
        # the validation error and assembled action. This is especially useful for
        # small-json tool actions such as run_syscall, where the model may select
        # the right action but omit/alias the required command source.
        validation_repair_attempts = 0
        while not result.ok and validation_repair_attempts < 2:
            validation_repair_attempts += 1
            self._stream_status(
                f"final validation failed for {action_name}; requesting repair {validation_repair_attempts}/2",
                event_type="staged_final_validation_repair_started",
                status="warning",
                verbose_level=3,
                actor=name,
                action=action_name,
                validation_stage=result.stage,
                validation_error=result.error,
            )
            repair_payload = {
                "selected_action": action_name,
                "validation_stage": result.stage,
                "validation_error": result.error,
                "assembled_action_that_failed": result.action_dict,
                "current_small_json": small_json,
                "required_small_fields": getattr(build_spec, "small_fields", []),
            }
            action_specific_hint = ""
            if action_name == "run_syscall":
                action_specific_hint = (
                    "\nFor run_syscall, payload must contain exactly one of command, command_args, or script_lines. "
                    "Prefer command_args for simple commands. If the user asks for the current working directory, "
                    "return payload.command_args as [\"pwd\"], timeout 30, shell false."
                )
            repair_prompt = (
                "The staged action selected the correct action name, but the final assembled action failed validation.\n"
                "Return corrected Stage-2 lightweight JSON only. Do not include Markdown fences. Do not change the selected action.\n"
                f"{action_specific_hint}\n\n"
                "Repair data:\n"
                f"{json.dumps(repair_payload, indent=2, sort_keys=True, default=str)}\n\n"
                "Selected-action Stage-2 schema/prompt:\n"
                f"{builder.render_small_json_prompt(action_name, context='', include_context=False)}"
            )
            raw_repair = self._call_text_model(actor, repair_prompt, stage="final_validation_repair", action_name=action_name)
            repaired_small_json = self._parse_json_with_repair(
                actor,
                raw_repair,
                builder.render_small_json_prompt(action_name, context="", include_context=False),
                stage="final_validation_repair",
                action_name=action_name,
                max_repairs=1,
            )
            if repaired_small_json is None:
                break
            repaired_small_json.setdefault("purpose", small_json.get("purpose", f"Perform {action_name} in response to the current user request."))
            repaired_small_json.setdefault("expectations", small_json.get("expectations", f"The {action_name} action completes successfully and advances the workflow."))
            repaired_small_json.setdefault("yield_motion_to", small_json.get("yield_motion_to", "user"))
            small_json = self._repair_small_json_from_context(action_name, repaired_small_json, context_str)
            result = builder.assemble_and_validate(action_name, small_json=small_json, heavy_values=heavy_values, actor=actor)

        if not result.ok:
            self.WORKFLOW_TURN = "user"
            err = f"[StagedActionWorkflow] Final staged action validation failed at {result.stage}: {result.error}"
            if self._get_action_verbose() >= 4:
                err = (
                    f"{err}\nAssembled action preview:\n"
                    f"{json.dumps(result.action_dict, indent=2, sort_keys=True, default=str)}"
                )
            self.update_history(actor="system", content=err, action={"action": "system_info"}, log_console=True)
            return

        normalized = result.normalized or result.action_dict
        action_obj = result.action_obj
        if not getattr(build_spec, "heavy_fields", None):
            self._last_staged_action_previewed = self._render_staged_action_preview(action_name, normalized)
        self._stream_status(
            f"validated staged action: {action_name}",
            event_type="staged_action_validated",
            status="done",
            verbose_level=2,
            actor=name,
            action=action_name,
        )

        # Record only the final canonical action durably, but render user-facing
        # send_message content as the assistant message instead of dumping the
        # whole action envelope to the CLI. The complete canonical action remains
        # attached in the history action metadata for trace/evaluation.
        history_content = normalized
        history_action: Any = action_name
        final_log_console = True
        if action_name == "send_message" and isinstance(normalized, dict):
            payload = normalized.get("payload") if isinstance(normalized.get("payload"), dict) else {}
            message = payload.get("message")
            if isinstance(message, str) and message:
                history_content = message
                history_action = normalized
        # If the user already saw a staged preview/stream, do not dump the full
        # action envelope again. Durable history/context/snapshot still update.
        if bool(getattr(self, "_last_heavy_field_streamed", False)) or bool(getattr(self, "_last_staged_action_previewed", False)):
            final_log_console = False
        self.update_history(actor=actor.name, content=history_content, action=history_action, log_console=final_log_console)
        if not final_log_console:
            try:
                self.infra.CONSOLE_HEAD = len(self.infra.chat_history)
            except Exception:
                pass

        # Execute exactly like the legacy workflow: execute receives a complete,
        # validated action object.
        self._stream_status(
            f"executing staged action: {action_name}",
            event_type="staged_action_execute_started",
            verbose_level=2,
            actor=name,
            action=action_name,
        )
        try:
            action_obj.execute(infra=self.infra)
        except Exception as exc:
            self.WORKFLOW_TURN = "user"
            err = f"[StagedActionWorkflow] Action execution failed for {action_name}: {type(exc).__name__}: {exc}"
            self.update_history(actor="system", content=err, action={"action": "system_error"}, log_console=True)
            self._stream_status(err, event_type="staged_action_execute_failed", status="error", actor=name, action=action_name)
            return

        if getattr(action_obj, "yield_motion_to", None):
            self.WORKFLOW_TURN = action_obj.yield_motion_to
        elif getattr(action_obj, "receiver", None):
            self.WORKFLOW_TURN = action_obj.receiver
        else:
            self.WORKFLOW_TURN = "system"

        self._stream_status(
            f"completed staged action: {action_name}; next turn: {self.WORKFLOW_TURN}",
            event_type="staged_action_execute_completed",
            status="done",
            verbose_level=1,
            actor=name,
            action=action_name,
            next_turn=self.WORKFLOW_TURN,
        )
