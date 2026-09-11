from __future__ import annotations

import json
import os
import shlex
from typing import Any, Iterable, Optional

from framework.workflows.action_registry import ActionRegistry, get_default_action_registry, payload_example_for_action
from framework.workflows.action_validation import ActionValidationError, validate_action_response
from framework.workflows.base_agent_action import AgentAction

from .models import ActionBuildSpec, HeavyFieldSpec, StagedActionBuildResult
from .specs import get_action_build_spec, parse_selected_action, render_compact_action_catalog


class StagedActionBuilder:
    """Non-breaking staged action assembly helper.

    This class is intentionally a foundation layer. It does not yet replace the
    existing one-shot workflow path. It provides the reusable pieces needed by
    future TurnBasedWorkflow / FastTurnBasedWorkflow integration: compact action
    selection prompts, selected-action small-JSON prompts, heavy-field insertion,
    final action assembly, and validation through the existing action validator.
    """

    def __init__(
        self,
        *,
        registry: ActionRegistry | None = None,
        allowed_actions: Iterable[str] | None = None,
        actor: Any = None,
        workflow: Any = None,
    ) -> None:
        self.registry = registry or get_default_action_registry()
        self.allowed_actions = list(allowed_actions) if allowed_actions is not None else None
        self.actor = actor
        self.workflow = workflow

    def emit(self, event_type: str, status: str | None = None, **metadata: Any) -> None:
        wf = self.workflow
        if wf is not None and hasattr(wf, 'emit_event'):
            try:
                wf.emit_event(event_type, status, **metadata)
            except Exception:
                pass

    def render_action_selection_prompt(self, *, context: str = '', include_context: bool = True) -> str:
        catalog = render_compact_action_catalog(
            registry=self.registry,
            names=self.allowed_actions,
            include_aliases=True,
            include_header=True,
        )
        parts = [
            'You are selecting the next WOLF action.',
            'Select the single best action for the current workflow state.',
            'Do not provide payload fields yet.',
            'Return only the exact action name or a tiny JSON object like {"action": "read_file"}.',
            '',
        ]
        if include_context and context:
            parts.extend(['Context:', context, ''])
        parts.append(catalog)
        return '\n'.join(parts)

    def parse_action_selection(self, raw: str | dict) -> str | None:
        selected = parse_selected_action(raw, registry=self.registry, names=self.allowed_actions)
        if selected:
            self.emit('staged_action_selected', 'done', action=selected, content=f'Selected action: {selected}')
        else:
            self.emit('staged_action_selection_failed', 'error', content='Could not parse selected action.', raw=str(raw)[:1000])
        return selected

    def get_build_spec(self, action_name: str) -> ActionBuildSpec:
        return get_action_build_spec(action_name, registry=self.registry)

    def render_small_json_prompt(self, action_name: str, *, context: str = '', include_context: bool = True) -> str:
        spec = self.get_build_spec(action_name)
        action_spec = self.registry.get(action_name)
        payload_schema = {}
        payload_example = {}
        if action_spec is not None:
            try:
                payload_schema = action_spec.action_cls.model_json_schema().get('properties', {}).get('payload', {})
            except Exception:
                payload_schema = {}
            try:
                payload_example = payload_example_for_action(action_spec.action_cls)
            except Exception:
                payload_example = {}

        heavy_paths = {'.'.join(h.payload_path) for h in spec.heavy_fields}
        parts = [
            f'You are building lightweight JSON fields for selected action: {action_name}.',
            'Return exactly one JSON object.',
            'Include top-level purpose, expectations, and yield_motion_to when relevant.',
            'Include payload fields requested by the selected action build spec.',
        ]
        if spec.has_heavy_fields:
            parts.extend([
                'Do NOT include heavy plain-text fields in this JSON response.',
                f'Heavy fields will be requested separately as plain text: {sorted(heavy_paths)}',
            ])
        if spec.json_prompt_hint:
            parts.append(spec.json_prompt_hint)
        parts.extend([
            '',
            'Small/metadata fields requested:',
            json.dumps(spec.small_fields, indent=2, sort_keys=True),
            '',
            'Selected action payload schema:',
            json.dumps(payload_schema, indent=2, sort_keys=True, default=str),
            '',
            'Payload example for orientation. Omit heavy fields if the build spec says they are collected later:',
            json.dumps(payload_example, indent=2, sort_keys=True, default=str),
        ])
        if include_context and context:
            parts.extend(['', 'Context:', context])
        return '\n'.join(parts)

    def render_heavy_field_prompt(
        self,
        action_name: str,
        heavy_field: HeavyFieldSpec,
        *,
        small_json: dict[str, Any] | None = None,
        context: str = '',
        include_context: bool = True,
    ) -> str:
        spec = self.get_build_spec(action_name)
        parts = [
            f'You are generating the heavy plain-text field for action: {action_name}.',
            f'Field: {heavy_field.field_name}',
            f'Content type: {heavy_field.content_type}',
            'Return only the raw field content. Do not wrap it in JSON. Do not add Markdown fences unless they are part of the intended content.',
        ]
        if spec.heavy_prompt_hint:
            parts.append(spec.heavy_prompt_hint)
        if heavy_field.prompt_hint:
            parts.append(heavy_field.prompt_hint)
        if small_json is not None:
            parts.extend(['', 'Already collected lightweight JSON:', json.dumps(small_json, indent=2, sort_keys=True, default=str)])
        if include_context and context:
            parts.extend(['', 'Context:', context])
        return '\n'.join(parts)

    @staticmethod
    def _set_path(root: dict[str, Any], path: list[str], value: Any) -> None:
        cur = root
        for part in path[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[part] = nxt
            cur = nxt
        cur[path[-1]] = value


    def _normalize_read_file_payload(self, payload: Any) -> dict[str, Any]:
        """Normalize staged read_file path variants before validation/execution.

        The Stage-2 model sometimes drops the leading dot from repo-relative
        paths, turning ``./IMPROVEMENTS/file.md`` into
        ``/IMPROVEMENTS/file.md``.  That validates as a string but fails at
        execution by targeting the filesystem root.  Only rewrite this case
        conservatively: the absolute path must not exist, and the dot-prefixed
        relative candidate must exist from the current working directory.
        """
        if isinstance(payload, str):
            payload = {"file_path": payload}
        elif not isinstance(payload, dict):
            payload = {}
        else:
            payload = dict(payload)

        if "file_path" not in payload and "path" in payload:
            payload["file_path"] = payload.pop("path")

        path_value = payload.get("file_path")
        if isinstance(path_value, str):
            candidate = path_value.strip()
            if candidate.startswith("/") and not os.path.exists(candidate):
                dot_relative = "." + candidate
                if os.path.exists(dot_relative):
                    candidate = dot_relative
            payload["file_path"] = candidate

        return payload

    def _normalize_run_syscall_payload(self, payload: Any) -> dict[str, Any]:
        """Normalize staged run_syscall payload variants before validation.

        Staged mode may receive imperfect small JSON from the model, especially
        after converting run_syscall from a heavy streamed command field to a
        compact JSON action.  Accept common near-misses and coerce them to the
        real SysCallActionArgs schema: exactly one of command, command_args, or
        script_lines plus timeout/shell.
        """
        if isinstance(payload, str):
            payload = {"command": payload}
        elif not isinstance(payload, dict):
            payload = {}
        else:
            payload = dict(payload)

        # Older staged spec used payload.command_transport as a planning field.
        # If a running prompt/model still emits it, interpret it as the command
        # source instead of failing validation.
        transport = payload.pop("command_transport", None)
        if transport is not None and not any(k in payload for k in ("command", "command_args", "script_lines")):
            if isinstance(transport, list):
                payload["command_args"] = [str(x) for x in transport]
            else:
                payload["command"] = str(transport)

        # Common aliases from model outputs.
        for alias in ("cmd", "command_text", "shell_command"):
            if alias in payload and not any(k in payload for k in ("command", "command_args", "script_lines")):
                payload["command"] = payload.pop(alias)

        if isinstance(payload.get("command_args"), str):
            try:
                payload["command_args"] = shlex.split(payload["command_args"])
            except Exception:
                payload["command"] = payload.pop("command_args")

        if isinstance(payload.get("script_lines"), str):
            payload["script_lines"] = [line for line in payload["script_lines"].splitlines() if line.strip()]

        # Prefer argv form for a simple one-word command such as pwd/ls/wc.
        if isinstance(payload.get("command"), str) and not payload.get("shell", False):
            command_text = payload["command"].strip()
            if command_text and not any(ch in command_text for ch in [";", "&&", "||", "|", "`", "$", ">", "<", "\n", "\r"]):
                try:
                    parts = shlex.split(command_text)
                    if parts:
                        payload.pop("command", None)
                        payload["command_args"] = parts
                except Exception:
                    pass

        payload.setdefault("timeout", 30)
        payload.setdefault("shell", False)
        return payload

    def assemble_action_dict(
        self,
        action_name: str,
        *,
        small_json: dict[str, Any] | None = None,
        heavy_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        small_json = dict(small_json or {})
        heavy_values = dict(heavy_values or {})
        action_dict: dict[str, Any] = {
            'action': action_name,
            'payload': {},
            'purpose': '',
            'expectations': '',
            'yield_motion_to': 'user',
        }

        # Accept either a full-ish action envelope or only lightweight fields.
        if isinstance(small_json.get('payload'), dict):
            action_dict['payload'].update(small_json.get('payload') or {})
        for key in ('purpose', 'expectations', 'yield_motion_to'):
            if key in small_json and small_json.get(key) is not None:
                action_dict[key] = small_json[key]

        # Also accept dotted payload keys for early staged experiments.
        for key, value in small_json.items():
            if key.startswith('payload.') and value is not None:
                self._set_path(action_dict, key.split('.'), value)

        spec = self.get_build_spec(action_name)
        for heavy in spec.heavy_fields:
            if heavy.field_name in heavy_values:
                self._set_path(action_dict, heavy.payload_path, heavy_values[heavy.field_name])
            else:
                dotted = '.'.join(heavy.payload_path)
                if dotted in heavy_values:
                    self._set_path(action_dict, heavy.payload_path, heavy_values[dotted])

        if action_name == 'run_syscall':
            # Also accept top-level command-ish fields from imperfect staged JSON.
            for key in ('command', 'command_args', 'script_lines', 'command_transport', 'cmd'):
                if key in small_json and key not in action_dict['payload']:
                    action_dict['payload'][key] = small_json[key]
            action_dict['payload'] = self._normalize_run_syscall_payload(action_dict.get('payload'))

        if action_name == 'write_file':
            # Also accept top-level write_file fields from imperfect staged JSON.
            for key in ('file_path', 'path', 'append', 'content', 'content_lines', 'content_base64'):
                if key in small_json and key not in action_dict['payload']:
                    action_dict['payload'][key] = small_json[key]
            if 'file_path' not in action_dict['payload'] and 'path' in action_dict['payload']:
                action_dict['payload']['file_path'] = action_dict['payload'].pop('path')
            action_dict['payload'].setdefault('append', False)

        if action_name == 'read_file':
            # Also accept top-level read_file path fields from imperfect staged JSON.
            # Stage-2 models often return {"path": "..."} or {"file_path": "..."}
            # instead of nesting the value under payload.
            for key in ('file_path', 'path'):
                if key in small_json and key not in action_dict['payload']:
                    action_dict['payload'][key] = small_json[key]
            action_dict['payload'] = self._normalize_read_file_payload(action_dict.get('payload'))

        return action_dict

    def validate_assembled(
        self,
        action_dict: dict[str, Any],
        *,
        actor: Any = None,
        allowed_actions: Iterable[str] | None = None,
    ) -> StagedActionBuildResult:
        action_name = str(action_dict.get('action') or '')
        allowed = list(allowed_actions) if allowed_actions is not None else self.allowed_actions
        validated = validate_action_response(
            action_dict,
            registry=self.registry,
            allowed_actions=allowed,
            actor=actor if actor is not None else self.actor,
        )
        if isinstance(validated, ActionValidationError):
            return StagedActionBuildResult(
                ok=False,
                action_name=action_name or None,
                action_dict=action_dict,
                error=validated.message,
                stage=validated.stage,
                metadata={'validation_error': validated.model_dump(mode='json')},
            )
        return StagedActionBuildResult(
            ok=True,
            action_name=action_name,
            action_dict=action_dict,
            action_obj=validated.action_obj,
            normalized=validated.normalized,
            stage='validated',
            metadata={'spec_risk_level': validated.spec.risk_level, 'spec_tags': list(validated.spec.tags)},
        )

    def assemble_and_validate(
        self,
        action_name: str,
        *,
        small_json: dict[str, Any] | None = None,
        heavy_values: dict[str, Any] | None = None,
        actor: Any = None,
    ) -> StagedActionBuildResult:
        self.emit('staged_action_assembly_started', 'running', action=action_name, content=f'Assembling staged action {action_name}.')
        action_dict = self.assemble_action_dict(action_name, small_json=small_json, heavy_values=heavy_values)
        self.emit('staged_action_assembled', 'done', action=action_name, content=f'Staged action {action_name} assembled.')
        result = self.validate_assembled(action_dict, actor=actor)
        if result.ok:
            self.emit('staged_action_validated', 'done', action=action_name, content=f'Staged action {action_name} validated.')
        else:
            self.emit('staged_action_validation_failed', 'error', action=action_name, content=result.error or 'Staged action validation failed.', stage=result.stage)
        return result

    def build_from_precollected(
        self,
        action_name: str,
        *,
        small_json: dict[str, Any] | None = None,
        heavy_values: dict[str, Any] | None = None,
        actor: Any = None,
    ) -> StagedActionBuildResult:
        """Convenience helper for tests and early workflow integration.

        It assumes the workflow has already collected the selected action, the
        small JSON fields, and any heavy text fields. Future integration will
        add model calls and CLI text-delta streaming around these same assembly
        and validation primitives.
        """
        return self.assemble_and_validate(action_name, small_json=small_json, heavy_values=heavy_values, actor=actor)