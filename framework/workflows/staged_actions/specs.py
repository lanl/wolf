from __future__ import annotations

import json
from typing import Iterable

from framework.workflows.action_registry import ActionRegistry, ActionSpec, get_default_action_registry

from .models import ActionBuildSpec, HeavyFieldSpec


def get_explicit_build_specs() -> dict[str, ActionBuildSpec]:
    return {
        'read_file': ActionBuildSpec(
            action_name='read_file',
            mode='small_json',
            small_fields=[
                'payload.file_path',
                'payload.path',
                'purpose',
                'expectations',
                'yield_motion_to',
            ],
            heavy_fields=[],
            selection_description='Read a local file and append its contents to context.',
            risk_level='normal',
            tags=['io', 'file_read'],
            json_prompt_hint=(
                'Return one JSON object for read_file. Include payload.file_path with the exact path requested by the user; preserve leading ./, ../, ~/, or / exactly. '
                'The legacy alias payload.path is also accepted, but payload.file_path is preferred. Example: '
                '{"payload":{"file_path":"./IMPROVEMENTS/dynamic_context_build_strategy.md"}, '
                '"purpose":"Read the requested improvement document.", '
                '"expectations":"The file contents are added to context.", '
                '"yield_motion_to":"assistant"}'
            ),
        ),
        'send_message': ActionBuildSpec(
            action_name='send_message',
            mode='json_plus_heavy_text',
            small_fields=[
                'payload.sender',
                'payload.receiver',
                'payload.audio_references',
                'payload.image_references',
                'payload.video_references',
                'payload.file_references',
                'purpose',
                'expectations',
                'yield_motion_to',
            ],
            heavy_fields=[
                HeavyFieldSpec(
                    field_name='message',
                    payload_path=['payload', 'message'],
                    content_type='markdown',
                    stream_to_cli=True,
                    prompt_hint='Generate only the user-facing assistant message body. Do not wrap it in JSON.',
                )
            ],
            selection_description='Send a user-facing message or final answer.',
            risk_level='normal',
            tags=['message', 'heavy_text'],
            json_prompt_hint='Return JSON for send_message metadata only. Do not include the message body.',
            heavy_prompt_hint='The next response should be the message body as plain text.',
        ),
        'write_file': ActionBuildSpec(
            action_name='write_file',
            mode='json_plus_heavy_text',
            small_fields=[
                'payload.file_path',
                'payload.append',
                'purpose',
                'expectations',
                'yield_motion_to',
            ],
            heavy_fields=[
                HeavyFieldSpec(
                    field_name='content',
                    payload_path=['payload', 'content'],
                    content_type='text',
                    stream_to_cli=True,
                    prompt_hint='Generate only the exact file content to write. Do not wrap it in JSON.',
                )
            ],
            selection_description='Write or append text content to a local file after approval.',
            risk_level='medium',
            tags=['local_side_effect', 'risky', 'heavy_text'],
            json_prompt_hint='Return JSON for write_file metadata only. Do not include file content.',
            heavy_prompt_hint='The next response should be exact file content as plain text.',
        ),
        'run_syscall': ActionBuildSpec(
            action_name='run_syscall',
            mode='small_json',
            small_fields=[
                'payload.command',
                'payload.command_args',
                'payload.script_lines',
                'payload.timeout',
                'payload.shell',
                'purpose',
                'expectations',
                'yield_motion_to',
            ],
            heavy_fields=[],
            selection_description='Run a local command or script after guardrails and user approval.',
            risk_level='high',
            tags=['local_side_effect', 'risky', 'command'],
            json_prompt_hint=(
                'Return one JSON object for run_syscall. You MUST include exactly one command source: '
                'payload.command_args, payload.command, or payload.script_lines. '
                'Prefer payload.command_args for simple commands, e.g. '
                '{"payload":{"command_args":["pwd"],"timeout":30,"shell":false}, '
                '"purpose":"Get the current working directory.", '
                '"expectations":"stdout contains the working directory path.", '
                '"yield_motion_to":"user"}. '
                'If the user asks for the current working directory, use payload.command_args=["pwd"]. '
                'Use script_lines only for real multi-line scripts.'
            ),
        ),
    }


def get_action_build_spec(action_name: str, registry: ActionRegistry | None = None) -> ActionBuildSpec:
    name = str(action_name or '').strip()
    explicit = get_explicit_build_specs()
    if name in explicit:
        return explicit[name]

    registry = registry or get_default_action_registry()
    spec = registry.get(name)
    if spec is None:
        raise KeyError(f'Unknown action for staged build spec: {name}')

    return ActionBuildSpec(
        action_name=name,
        mode='small_json',
        small_fields=['payload', 'purpose', 'expectations', 'yield_motion_to'],
        heavy_fields=[],
        selection_description=spec.description or name,
        risk_level=spec.risk_level,
        tags=list(spec.tags),
        json_prompt_hint='Return one JSON object for the selected action using only this action payload schema.',
    )


def render_compact_action_catalog(
    registry: ActionRegistry | None = None,
    names: Iterable[str] | None = None,
    *,
    include_aliases: bool = True,
    include_header: bool = True,
    ) -> str:
    registry = registry or get_default_action_registry()
    selected_specs: list[ActionSpec] = []
    if names is None:
        selected_specs = registry.specs()
    else:
        for name in names:
            spec = registry.get(str(name))
            if spec is not None:
                selected_specs.append(spec)

    lines: list[str] = []
    if include_header:
        lines.extend([
            'Select exactly one action from this compact action catalog.',
            'Return only the action name or a tiny JSON object like {"action": "read_file"}.',
            'Do not include payload fields yet; the selected action schema will be requested next.',
            '',
            'Actions:',
        ])
    for idx, spec in enumerate(selected_specs, start=1):
        alias = f'A{idx:02d}'
        risk = '' if spec.risk_level == 'normal' else f' [RISK: {spec.risk_level}]'
        tags = f' tags={list(spec.tags)}' if spec.tags else ''
        desc = spec.description or 'No description.'
        if include_aliases:
            lines.append(f'- {alias} = {spec.name!r}{risk}: {desc}{tags}')
        else:
            lines.append(f'- {spec.name!r}{risk}: {desc}{tags}')
    return '\n'.join(lines)


def parse_selected_action(raw: str | dict, registry: ActionRegistry | None = None, names: Iterable[str] | None = None) -> str | None:
    registry = registry or get_default_action_registry()
    allowed = [str(n) for n in (names if names is not None else registry.names())]
    allowed_set = set(allowed)

    if isinstance(raw, dict):
        candidate = str(raw.get('action') or raw.get('name') or raw.get('selected_action') or '').strip()
        return candidate if candidate in allowed_set else None

    text = str(raw or '').strip()
    if not text:
        return None
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            candidate = str(loaded.get('action') or loaded.get('name') or loaded.get('selected_action') or '').strip()
            if candidate in allowed_set:
                return candidate
        elif isinstance(loaded, str) and loaded in allowed_set:
            return loaded
    except Exception:
        pass

    stripped = text.strip().strip('`').strip().strip('"').strip("'")
    if stripped in allowed_set:
        return stripped

    upper = stripped.upper()
    if upper.startswith('A') and upper[1:].isdigit():
        idx = int(upper[1:]) - 1
        if 0 <= idx < len(allowed):
            return allowed[idx]

    found = [name for name in allowed if name in stripped.split() or name == stripped]
    return found[0] if len(found) == 1 else None