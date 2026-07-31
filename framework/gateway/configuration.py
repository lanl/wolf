from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


TOP_LEVEL_KEYS = ['gateway', 'runtime', 'config', 'infra', 'agents', 'pool', 'sessions']


def _parse_value(text: str) -> Any:
    text = text.strip()
    if text == 'None':
        return None
    if text in {'True', 'False'}:
        return text == 'True'
    try:
        return ast.literal_eval(text)
    except Exception:
        return text


def parse_config_command(rest: str) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    path: list[str] = []
    selectors: dict[str, Any] = {}
    updates: dict[str, Any] = {}
    for tok in [t for t in rest.split() if t]:
        if '=' not in tok:
            if updates:
                raise ValueError('path tokens must come before assignments')
            path.append(tok)
            continue
        key, value = tok.split('=', 1)
        key = key.strip()
        value = _parse_value(value)
        if key in {'idx', 'key', 'name'} and not updates:
            selectors[key] = value
        else:
            updates[key] = value
    return path, selectors, updates


def public_attrs(obj: Any) -> dict[str, Any]:
    if is_dataclass(obj):
        return {f.name: getattr(obj, f.name) for f in fields(obj)}
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, (list, tuple)):
        return {str(i): v for i, v in enumerate(obj)}
    if hasattr(obj, '__dict__'):
        out = {}
        for k, v in vars(obj).items():
            if k.startswith('_') or callable(v):
                continue
            out[k] = v
        return out
    return {}


def to_display(obj: Any, depth: int = 1) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        if depth <= 0:
            return f'<dict keys={list(obj.keys())[:8]}>'
        return {str(k): to_display(v, depth - 1) for k, v in list(obj.items())[:32]}
    if isinstance(obj, (list, tuple)):
        if depth <= 0:
            return f'<list n={len(obj)}>'
        return [to_display(v, depth - 1) for v in list(obj)[:16]]
    if is_dataclass(obj):
        return {f.name: to_display(getattr(obj, f.name), depth - 1) for f in fields(obj)}
    attrs = public_attrs(obj)
    if attrs:
        if depth <= 0:
            return f'<{obj.__class__.__name__} attrs={list(attrs.keys())[:8]}>'
        return {'__class__': obj.__class__.__name__, **{k: to_display(v, depth - 1) for k, v in list(attrs.items())[:32]}}
    return repr(obj)


def root_map(gateway: Any) -> dict[str, Any]:
    shared = gateway.infra_factory.shared_resources
    return {
        'gateway': {
            'db_path': str(gateway.store.path),
            'session_count': len(gateway.sessions),
        },
        'runtime': gateway.runtime,
        'config': gateway.runtime.config,
        'infra': {
            'session_root': str(gateway.infra_factory.session_root),
            'objects': {
                'objects': shared.objects,
                'kbs': shared.knowledge_bases,
                'tbs': shared.toolboxes,
                'universes': shared.universes,
                'extra': shared.extra,
            },
        },
        'pool': gateway.agent_pool,
        'agents': getattr(gateway.agent_pool, '_agents', {}),
        'sessions': gateway.sessions,
    }


def _select_from_mapping(obj: Any, selectors: dict[str, Any]) -> Any:
    if not selectors:
        return obj
    if 'name' in selectors:
        name = selectors['name']
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            for k, v in obj.items():
                if str(k) == str(name) or getattr(v, 'name', None) == name:
                    return v
            raise KeyError(f'name not found: {name}')
        if isinstance(obj, (list, tuple)):
            for v in obj:
                if getattr(v, 'name', None) == name:
                    return v
            raise KeyError(f'name not found: {name}')
    if 'key' in selectors:
        key = selectors['key']
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for k, v in obj.items():
                if str(k) == str(key):
                    return v
        raise KeyError(f'key not found: {key}')
    if 'idx' in selectors:
        idx = int(selectors['idx'])
        if isinstance(obj, dict):
            keys = sorted(obj.keys(), key=str)
            if idx < 0 or idx >= len(keys):
                raise IndexError(f'idx out of range: {idx}')
            return obj[keys[idx]]
        if isinstance(obj, (list, tuple)):
            return obj[idx]
        raise TypeError('idx selector requires list/tuple/dict')
    return obj


def resolve_path(gateway: Any, path: list[str], selectors: dict[str, Any]) -> Any:
    current: Any = root_map(gateway)
    for token in path:
        if isinstance(current, dict):
            if token in current:
                current = current[token]
                continue
            aliases = {'knowledge_bases': 'kbs', 'toolboxes': 'tbs', 'univs': 'universes'}
            token2 = aliases.get(token, token)
            if token2 in current:
                current = current[token2]
                continue
            raise KeyError(f'path token not found: {token}')
        if isinstance(current, (list, tuple)):
            raise TypeError(f'path token {token} requires a selector (idx/name/key)')
        attrs = public_attrs(current)
        if token in attrs:
            current = attrs[token]
            continue
        if hasattr(current, token):
            current = getattr(current, token)
            continue
        raise KeyError(f'path token not found: {token}')
    return _select_from_mapping(current, selectors)


def _coerce_value(current: Any, value: Any) -> Any:
    if current is None:
        return value
    target_type = type(current)
    if isinstance(value, target_type):
        return value
    if target_type is bool:
        if isinstance(value, str):
            if value.lower() in {'true', '1', 'yes', 'on'}:
                return True
            if value.lower() in {'false', '0', 'no', 'off'}:
                return False
        raise TypeError(f'expects bool, got {type(value).__name__}')
    if target_type in {int, float, str}:
        try:
            return target_type(value)
        except Exception as exc:
            raise TypeError(f'expects {target_type.__name__}, got {type(value).__name__}') from exc
    if target_type in {list, tuple} and isinstance(value, (list, tuple)):
        return list(value) if target_type is list else tuple(value)
    if target_type is dict and isinstance(value, dict):
        return value
    return value


def _set_attr(target: Any, key: str, value: Any) -> tuple[Any, Any]:
    if isinstance(target, dict):
        before = target.get(key)
        coerced = _coerce_value(before, value) if key in target else value
        target[key] = coerced
        return before, coerced
    attrs = public_attrs(target)
    if key in attrs or hasattr(target, key) or is_dataclass(target):
        before = getattr(target, key, None)
        coerced = _coerce_value(before, value)
        setattr(target, key, coerced)
        return before, coerced
    raise KeyError(f'cannot set {key} on {type(target).__name__}')


def apply_updates(target: Any, updates: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    changes = []
    for key, value in updates.items():
        before, after = _set_attr(target, key, value)
        changes.append((key, before, after))
    return changes


def editable_fields(obj: Any) -> dict[str, str]:
    attrs = public_attrs(obj)
    out = {}
    for k, v in attrs.items():
        if callable(v):
            continue
        out[k] = type(v).__name__
    return out


def config_view(gateway: Any, path: list[str], selectors: dict[str, Any], changes: list[tuple[str, Any, Any]] | None = None) -> dict[str, Any]:
    target = resolve_path(gateway, path, selectors)
    return {
        'path': ' '.join(path) or '<root>',
        'selectors': selectors,
        'type': type(target).__name__,
        'value': to_display(target, depth=2),
        'fields': sorted(list(public_attrs(target).keys())) if public_attrs(target) else [],
        'editable_fields': editable_fields(target),
        'changes': changes or [],
    }


def suggest_tokens(gateway: Any, rest: str) -> list[str]:
    try:
        path, selectors, updates = parse_config_command(rest)
    except Exception:
        return []
    if updates:
        return []
    parts = [t for t in rest.split() if t]
    token = parts[-1] if parts and not rest.endswith(' ') else ''
    pathish = [p for p in path]
    base = []
    if not pathish:
        base = TOP_LEVEL_KEYS
    else:
        try:
            target = resolve_path(gateway, pathish, selectors)
        except Exception:
            # try parent context
            try:
                target = resolve_path(gateway, pathish[:-1], selectors)
                token = pathish[-1]
            except Exception:
                return TOP_LEVEL_KEYS
            attrs = public_attrs(target)
            base = list(attrs.keys())
        else:
            if isinstance(target, dict):
                base = list(target.keys()) + ['idx=', 'key=', 'name=']
            elif isinstance(target, (list, tuple)):
                base = ['idx=', 'name=']
            else:
                attrs = public_attrs(target)
                base = list(attrs.keys())
                if attrs:
                    base += [f'{k}=' for k in attrs.keys()]
    if token:
        return [str(b) for b in base if str(b).startswith(token)]
    return [str(b) for b in base]
