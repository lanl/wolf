from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import dataclasses
import json
import time
from urllib.parse import urljoin

SECRET_KEY_PARTS = (
    'api_key', 'apikey', 'token', 'secret', 'password', 'authorization',
    'access_token', 'refresh_token', 'bearer', 'credential',
)
GENERIC_COLLECTION_NAMES = {'summaries', 'traces', 'db', 'default', 'default_collection'}


def is_secret_key(key: Any) -> bool:
    text = str(key or '').lower()
    return any(part in text for part in SECRET_KEY_PARTS)


def redact_value(value: Any, *, key: Any = None, max_depth: int = 6) -> Any:
    """Return a JSON-safe, recursively redacted representation.

    This intentionally does not call object methods that could perform I/O.  It
    only inspects simple containers, dataclasses, pydantic-style model dumps, and
    a small set of explicit scalar attributes gathered elsewhere.
    """
    if is_secret_key(key):
        return '***redacted***'
    if max_depth <= 0:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return f'<{type(value).__name__}>'
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:4000] + ('…' if len(value) > 4000 else '')
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        try:
            value = dataclasses.asdict(value)
        except Exception:
            return f'<{type(value).__name__}>'
    if isinstance(value, dict):
        return {str(k): redact_value(v, key=k, max_depth=max_depth - 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact_value(v, max_depth=max_depth - 1) for v in list(value)[:250]]
    if hasattr(value, 'model_dump'):
        try:
            return redact_value(value.model_dump(), max_depth=max_depth - 1)
        except Exception:
            return f'<{type(value).__name__}>'
    return f'<{type(value).__name__}>'


def _len(value: Any) -> int:
    try:
        return len(value)
    except Exception:
        return 0


def _class(obj: Any) -> Optional[str]:
    return type(obj).__name__ if obj is not None else None


def _safe_getattr(obj: Any, attr: str, default: Any = None) -> Any:
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def summarize_chat_manager(chat_manager: Any) -> Dict[str, Any]:
    history = _safe_getattr(chat_manager, 'CHAT_HISTORY')
    return {
        'present': chat_manager is not None,
        'class': _class(chat_manager),
        'entries': _len(history),
        'tokens': _safe_getattr(chat_manager, 'CHAT_HISTORY_TOKEN_COUNT'),
        'last_index': _safe_getattr(chat_manager, 'LAST_CHAT_ENTRY_IDX'),
    }


def summarize_context_manager(context_manager: Any) -> Dict[str, Any]:
    entries = _safe_getattr(context_manager, 'current_ctx')
    tokens = _safe_getattr(context_manager, 'current_ctx_tokens')
    max_tokens = _safe_getattr(context_manager, 'max_ctx_tokens')
    utilization = None
    try:
        utilization = round(100.0 * float(tokens or 0) / max(1.0, float(max_tokens or 0)), 2) if max_tokens else None
    except Exception:
        utilization = None
    return {
        'present': context_manager is not None,
        'class': _class(context_manager),
        'entries': _len(entries),
        'tokens': tokens,
        'max_tokens': max_tokens,
        'utilization_pct': utilization,
        'session_dir': _safe_getattr(context_manager, 'session_dir'),
    }


def _vstore_summary(obj: Any, name: str) -> Optional[Dict[str, Any]]:
    """Summarize a vector store without invoking query/count/update methods."""
    if obj is None:
        return None
    data: Dict[str, Any] = {'name': name, 'class': _class(obj)}
    for attr in (
        'collection_name', 'text_collection_name', 'vision_collection_name', 'table_collection_name',
        'name', 'persist_directory', 'persist_dir', 'db_path', 'rebuild_vstore',
        'chunk_size', 'chunk_overlap', 'allow_online',
    ):
        if hasattr(obj, attr):
            data[attr] = redact_value(_safe_getattr(obj, attr), key=attr)
    params = _safe_getattr(obj, 'params') or _safe_getattr(obj, '_params')
    if params is not None:
        data['params'] = redact_value(params, key='params')
    data['client_present'] = _safe_getattr(obj, 'client') is not None
    data['collection_present'] = any(_safe_getattr(obj, attr) is not None for attr in ('collection', 'text_collection', 'vision_collection', 'table_collection'))
    return data


def summarize_memory_manager(memory_manager: Any) -> Dict[str, Any]:
    summaries_vs = (
        _safe_getattr(memory_manager, 'summaries_vector_store')
        or _safe_getattr(memory_manager, 'summaries_vs')
        or _safe_getattr(memory_manager, '_summaries_vector_store')
    )
    traces_vs = (
        _safe_getattr(memory_manager, 'traces_vector_store')
        or _safe_getattr(memory_manager, 'traces_vs')
        or _safe_getattr(memory_manager, '_traces_vector_store')
    )
    vstores = []
    for label, store in [('summaries', summaries_vs), ('traces', traces_vs)]:
        item = _vstore_summary(store, label)
        if item:
            vstores.append(item)
    fragments = _safe_getattr(memory_manager, 'fragments')
    memory_fragments = _safe_getattr(memory_manager, 'memory_fragments')
    return {
        'present': memory_manager is not None,
        'class': _class(memory_manager),
        'memory_path': redact_value(_safe_getattr(memory_manager, 'memory_path'), key='memory_path'),
        'fragments': _len(fragments) if fragments is not None else _len(memory_fragments),
        'fragment_categories': sorted(list((memory_fragments or {}).keys()))[:80] if isinstance(memory_fragments, dict) else [],
        'summaries_count': _len(_safe_getattr(memory_manager, 'summaries')),
        'vstores': vstores,
    }


def _tool_metadata_from_obj(tool: Any, fallback_name: str) -> Dict[str, Any]:
    card = _safe_getattr(tool, 'card')
    meta = {'class': _class(tool)}
    if card is not None:
        for attr in ('name', 'language', 'kind', 'version', 'description', 'usage', 'entrypoint', 'tags', 'author', 'homepage'):
            value = _safe_getattr(card, attr)
            if value is not None:
                meta[attr] = redact_value(value, key=attr)
    else:
        meta['name'] = fallback_name
    tool_meta = _safe_getattr(tool, 'meta')
    if tool_meta is not None:
        meta['tool_meta'] = redact_value(tool_meta, max_depth=3)
    doc = _safe_getattr(tool, 'doc')
    if doc is not None:
        vstore = _safe_getattr(doc, 'vstore')
        if vstore is not None:
            meta['doc_vstore'] = _vstore_summary(vstore, f'{fallback_name}_docstore')
    return meta


def _tool_resource(name: str, tool: Any, *, owner_type: str, owner_id: str, locality: str, source: str, toolbox_name: Optional[str] = None) -> Dict[str, Any]:
    metadata = tool if isinstance(tool, dict) else _tool_metadata_from_obj(tool, name)
    if toolbox_name:
        metadata = dict(metadata)
        metadata.setdefault('toolbox', toolbox_name)
    status = metadata.get('status') if isinstance(metadata, dict) else None
    display_name = metadata.get('name') if isinstance(metadata, dict) else None
    return {
        'id': f'tool:{owner_type}:{owner_id}:{toolbox_name or "toolbox"}:{display_name or name}',
        'name': str(display_name or name),
        'kind': 'tool',
        'locality': locality,
        'owner_type': owner_type,
        'owner_id': str(owner_id),
        'source': source,
        'status': str(status or 'unknown'),
        'shared': locality in {'gateway_session', 'shared_client', 'remote_universe', 'local_universe', 'main_workflow'},
        'persistent': bool((metadata or {}).get('doc_vstore') or (metadata or {}).get('path') or (metadata or {}).get('entrypoint')),
        'write_capable': False,
        'destructive_capable': False,
        'metadata': redact_value(metadata),
    }


def _absolute_app_url(base_url: str | None, raw_url: Any) -> str:
    raw = str(raw_url or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    base = str(base_url or "").strip()
    if not base:
        return raw
    return urljoin(base.rstrip("/") + "/", raw.lstrip("/"))


def _absolute_app_websocket_url(base_url: str | None, raw_url: Any) -> str:
    raw = str(raw_url or "").strip()
    if not raw:
        return ""
    if raw.startswith("ws://") or raw.startswith("wss://"):
        return raw
    if raw.startswith("http://"):
        return "ws://" + raw[len("http://"):]
    if raw.startswith("https://"):
        return "wss://" + raw[len("https://"):]
    base = str(base_url or "").strip()
    if not base:
        return raw
    absolute = urljoin(base.rstrip("/") + "/", raw.lstrip("/"))
    if absolute.startswith("http://"):
        return "ws://" + absolute[len("http://"):]
    if absolute.startswith("https://"):
        return "wss://" + absolute[len("https://"):]
    return absolute


def _universe_base_url(universe_obj: Any) -> str:
    target = _safe_getattr(universe_obj, 'info') or universe_obj
    host = _safe_getattr(target, 'host')
    port = _safe_getattr(target, 'port')
    if not host:
        return ""
    host_s = str(host).rstrip("/")
    if host_s.startswith("http://") or host_s.startswith("https://"):
        if port and str(port) not in {"0", "None"} and ":" not in host_s.rsplit("/", 1)[-1]:
            return f"{host_s}:{int(port)}"
        return host_s
    if port and str(port) not in {"0", "None"}:
        return f"http://{host_s}:{int(port)}"
    return f"http://{host_s}"


def _app_resource(name: str, app: Any, *, owner_type: str, owner_id: str, locality: str, source: str, universe_name: Optional[str] = None, universe_base_url: Optional[str] = None) -> Dict[str, Any]:
    metadata = redact_value(app, max_depth=4)
    if not isinstance(metadata, dict):
        metadata = {'value': metadata}
    app_id = str(metadata.get('app_id') or metadata.get('id') or name)
    title = str(metadata.get('title') or metadata.get('name') or app_id)
    status = str(metadata.get('status') or 'unknown')
    base_url = str(universe_base_url or metadata.get('universe_base_url') or '').rstrip('/')
    url = _absolute_app_url(base_url, metadata.get('url'))
    view_url = _absolute_app_url(base_url, metadata.get('view_url') or metadata.get('url'))
    proxy_url = _absolute_app_url(base_url, metadata.get('proxy_url'))
    websocket_url = _absolute_app_websocket_url(base_url, metadata.get('websocket_url'))
    lifecycle_endpoints = {}
    if base_url:
        quoted = str(app_id)
        lifecycle_endpoints = {
            'manifest': f'{base_url}/apps/{quoted}/manifest',
            'start': f'{base_url}/apps/{quoted}/start',
            'stop': f'{base_url}/apps/{quoted}/stop',
            'restart': f'{base_url}/apps/{quoted}/restart',
            'delete': f'{base_url}/apps/{quoted}',
            'logs': f'{base_url}/apps/{quoted}/logs',
            'websocket_proxy': f'{base_url}/apps/{quoted}/proxy',
        }
    return {
        'id': f'app:{owner_type}:{owner_id}:{universe_name or "universe"}:{app_id}',
        'name': app_id,
        'app_id': app_id,
        'title': title,
        'display_name': title,
        'kind': 'app',
        'app_kind': metadata.get('kind') or 'custom',
        'backend': metadata.get('backend') or metadata.get('app_backend') or 'url',
        'url': url,
        'view_url': view_url,
        'proxy_url': proxy_url,
        'websocket_url': websocket_url,
        'universe': str(universe_name or metadata.get('universe') or ''),
        'universe_base_url': base_url,
        'lifecycle_endpoints': lifecycle_endpoints,
        'locality': locality,
        'owner_type': owner_type,
        'owner_id': str(owner_id),
        'source': source,
        'status': status,
        'shared': locality in {'gateway_session', 'shared_client', 'remote_universe', 'local_universe', 'main_workflow'},
        'persistent': False,
        'write_capable': bool(lifecycle_endpoints),
        'destructive_capable': bool(lifecycle_endpoints),
        'metadata': metadata,
    }


def _toolbox_tool_items(tb: Any) -> List[Any]:
    direct = _safe_getattr(tb, 'tools')
    if isinstance(direct, dict):
        return list(direct.items())[:250]
    if isinstance(direct, list):
        return [(str(idx), item) for idx, item in enumerate(direct[:250])]
    # V4 toolboxes expose list_tools() as a local registry read.  Do not call
    # search, execute, test, build, deploy, or remote universe endpoints here.
    list_tools = _safe_getattr(tb, 'list_tools')
    if callable(list_tools):
        try:
            listed = list_tools()
        except Exception:
            return []
        out = []
        for idx, item in enumerate(list(listed or [])[:250]):
            if isinstance(item, dict):
                out.append((str(item.get('id') or item.get('name') or idx), item))
            else:
                out.append((str(item), {'name': str(item)}))
        return out
    return []


def _resource(kind: str, name: str, obj: Any, owner_type: str, owner_id: str, locality: str, source: str) -> Dict[str, Any]:
    target = _safe_getattr(obj, 'info') or obj
    metadata = {'class': _class(obj)}
    for attr in ('name', 'description', 'host', 'port', 'api_version', 'status', 'kind', 'type', 'registry_path', 'inventory_path'):
        if hasattr(target, attr):
            metadata[attr] = redact_value(_safe_getattr(target, attr), key=attr)
    if kind == 'kb':
        vstore = _safe_getattr(obj, 'vstore') or _safe_getattr(obj, 'vector_store')
        if vstore is not None:
            metadata['vstore'] = _vstore_summary(vstore, f'{name}_vstore')
        inventory = _safe_getattr(obj, 'inventory_path') or _safe_getattr(obj, '_inventory_path')
        if inventory is not None:
            metadata['inventory_path'] = redact_value(inventory, key='inventory_path')
    if kind == 'tb':
        tool_items = _toolbox_tool_items(obj)
        metadata['tool_count'] = len(tool_items)
        metadata['tool_names'] = [str(k) for k, _ in tool_items[:80]]
        index = _safe_getattr(obj, 'index')
        if index is not None:
            vstore = _safe_getattr(index, 'vstore')
            if vstore is not None:
                metadata['index_vstore'] = _vstore_summary(vstore, f'{name}_index')
    if kind == 'universe':
        metadata['kb_count'] = _len(_safe_getattr(obj, 'KBs'))
        metadata['tb_count'] = _len(_safe_getattr(obj, 'TBs'))
        metadata['app_count'] = _len(_safe_getattr(obj, 'Apps'))
        allowed = _safe_getattr(obj, 'allowed_actions')
        if callable(allowed):
            try:
                metadata['allowed_actions'] = list(allowed())[:120]
            except Exception:
                metadata['allowed_actions_error'] = 'unavailable'
    if hasattr(target, 'api_token'):
        metadata['api_token'] = '***redacted***'
    return {
        'id': f'{kind}:{owner_type}:{owner_id}:{name}',
        'name': str(name),
        'kind': kind,
        'locality': locality,
        'owner_type': owner_type,
        'owner_id': str(owner_id),
        'source': source,
        'status': str(metadata.get('status') or 'unknown'),
        'shared': locality in {'gateway_session', 'shared_client', 'remote_universe', 'local_universe', 'main_workflow'},
        'persistent': True,
        'write_capable': kind in {'tb', 'universe', 'vstore'},
        'destructive_capable': kind in {'vstore', 'universe'},
        'metadata': metadata,
    }


def summarize_resources_from_infra(infra: Any, *, owner_type: str, owner_id: str, locality: str, source: str) -> Dict[str, List[Dict[str, Any]]]:
    resources = {'kbs': [], 'tbs': [], 'tools': [], 'universes': [], 'apps': []}
    if infra is None:
        return resources
    for name, obj in (_safe_getattr(infra, 'KBs', {}) or {}).items():
        resources['kbs'].append(_resource('kb', name, obj, owner_type, owner_id, locality, source))
    for name, obj in (_safe_getattr(infra, 'TBs', {}) or {}).items():
        resources['tbs'].append(_resource('tb', name, obj, owner_type, owner_id, locality, source))
        for tool_name, tool in _toolbox_tool_items(obj):
            resources['tools'].append(_tool_resource(str(tool_name), tool, owner_type='toolbox', owner_id=f'{owner_id}:{name}', locality=locality, source=source, toolbox_name=str(name)))
    for name, obj in (_safe_getattr(infra, 'UNIVs', {}) or {}).items():
        resources['universes'].append(_resource('universe', name, obj, owner_type, owner_id, locality, source))
        nested_locality = 'local_universe' if locality not in {'remote_universe', 'actionbox'} else locality
        universe_base_url = _universe_base_url(obj)
        apps = []
        list_apps = _safe_getattr(obj, 'list_apps')
        if callable(list_apps):
            try:
                apps = list_apps() or []
            except Exception:
                apps = []
        elif isinstance(_safe_getattr(obj, 'Apps'), dict):
            apps = list((_safe_getattr(obj, 'Apps') or {}).values())
        for idx, app_item in enumerate(list(apps)[:250]):
            if isinstance(app_item, dict):
                app_name = str(app_item.get('app_id') or app_item.get('id') or idx)
            else:
                app_name = str(_safe_getattr(app_item, 'app_id', idx))
            resources.setdefault('apps', []).append(
                _app_resource(
                    app_name,
                    app_item,
                    owner_type='universe',
                    owner_id=f'{owner_id}:{name}',
                    locality=nested_locality,
                    source='universe_app_registry',
                    universe_name=str(name),
                    universe_base_url=universe_base_url,
                )
            )
        nested = summarize_resources_from_infra(obj, owner_type='universe', owner_id=f'{owner_id}:{name}', locality=nested_locality, source='universe_registry')
        for key, values in nested.items():
            resources.setdefault(key, []).extend(values)
    return resources


def summarize_base_infra(infra: Any, *, owner_type: str = 'session', owner_id: str = 'main', locality: str = 'main_workflow', source: str = 'main_infra') -> Dict[str, Any]:
    resources = summarize_resources_from_infra(infra, owner_type=owner_type, owner_id=owner_id, locality=locality, source=source)
    return {
        'present': infra is not None,
        'class': _class(infra),
        'session_dir': _safe_getattr(infra, 'session_dir'),
        'log_dir': _safe_getattr(infra, 'log_dir'),
        'db_client_present': _safe_getattr(infra, 'db_client') is not None,
        'agent_name': _safe_getattr(_safe_getattr(infra, 'agent'), 'name'),
        'chat': summarize_chat_manager(_safe_getattr(infra, 'chat_manager')),
        'context': summarize_context_manager(_safe_getattr(infra, 'context_manager')),
        'memory': summarize_memory_manager(_safe_getattr(infra, 'memory_manager')),
        'resources': resources,
        'resource_counts': {k: len(v) for k, v in resources.items()},
    }


def summarize_task_infras(orch: Any) -> List[Dict[str, Any]]:
    runtime = _safe_getattr(orch, 'runtime')
    memory = _safe_getattr(runtime, 'memory')
    context_capsules = _safe_getattr(memory, 'context_capsules', {}) or {}
    artifact_store = _safe_getattr(runtime, 'artifacts')
    rows = []
    for task_id, infra in (_safe_getattr(runtime, 'task_infras', {}) or {}).items():
        local = _safe_getattr(infra, 'local')
        capsule = context_capsules.get(task_id) if isinstance(context_capsules, dict) else None
        artifacts = []
        if artifact_store is not None and hasattr(artifact_store, 'list_task_artifacts'):
            try:
                artifacts = list(artifact_store.list_task_artifacts(task_id))
            except Exception:
                artifacts = []
        event_summary = summarize_events(_safe_getattr(infra, 'events'))
        rows.append({
            'task_id': str(task_id),
            'class': _class(infra),
            'session_dir': _safe_getattr(local, 'session_dir'),
            'artifact_dir': _safe_getattr(local, 'artifact_dir'),
            'workflow_type': _safe_getattr(local, 'workflow_type'),
            'metadata': redact_value(_safe_getattr(local, 'metadata', {})),
            'events': event_summary.get('total', 0),
            'event_summary': event_summary,
            'local_message_count': _len(_safe_getattr(capsule, 'local_messages')),
            'local_fact_count': _len(_safe_getattr(capsule, 'local_facts')),
            'child_summary_count': _len(_safe_getattr(capsule, 'child_summaries')),
            'dependency_summary_count': _len(_safe_getattr(capsule, 'dependency_summaries')),
            'compressed_history_count': _len(_safe_getattr(capsule, 'compressed_history')),
            'artifact_count': len(artifacts),
            'resource_counts': {
                'kbs': _len(_safe_getattr(infra, 'KBs')),
                'tbs': _len(_safe_getattr(infra, 'TBs')),
                'tools': sum(len(_toolbox_tool_items(tb)) for tb in (_safe_getattr(infra, 'TBs', {}) or {}).values()),
                'universes': _len(_safe_getattr(infra, 'UNIVs')),
            },
        })
    return rows


def _vstore_resource(vstore: Dict[str, Any], *, owner_type: str, owner_id: str, locality: str, source: str) -> Dict[str, Any]:
    label = str(vstore.get('name') or vstore.get('collection_name') or 'vstore')
    collection = vstore.get('collection_name') or vstore.get('text_collection_name') or label
    return {
        'id': f'vstore:{owner_type}:{owner_id}:{collection}',
        'name': label,
        'kind': 'vstore',
        'locality': locality,
        'owner_type': owner_type,
        'owner_id': str(owner_id),
        'source': source,
        'status': 'unknown',
        'shared': locality in {'shared_client', 'gateway_session', 'main_workflow'},
        'persistent': bool(vstore.get('persist_directory') or vstore.get('persist_dir') or vstore.get('db_path')),
        'write_capable': True,
        'destructive_capable': bool(vstore.get('rebuild_vstore')),
        'metadata': redact_value(vstore),
    }


def _artifact_resource(record: Dict[str, Any], *, task_id: str) -> Dict[str, Any]:
    artifact_id = str(record.get('artifact_id') or record.get('id') or record.get('path') or f'artifact:{task_id}')
    return {
        'id': f'artifact:task:{task_id}:{artifact_id}',
        'name': str(Path(str(record.get('path') or artifact_id)).name),
        'kind': 'artifact',
        'locality': 'task_local',
        'owner_type': 'task',
        'owner_id': str(task_id),
        'source': 'orchestration_runtime',
        'status': 'ready',
        'shared': False,
        'persistent': bool(record.get('path')),
        'write_capable': False,
        'destructive_capable': False,
        'metadata': redact_value(record),
    }


def _nested_vstore_resources_from_resource(resource: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create VStore resource rows from KB/TB/Tool metadata without extra I/O."""
    metadata = resource.get('metadata') or {}
    rows: List[Dict[str, Any]] = []
    for key, label in (('vstore', 'kb_vstore'), ('index_vstore', 'toolbox_index_vstore'), ('doc_vstore', 'tool_doc_vstore')):
        vstore = metadata.get(key)
        if isinstance(vstore, dict):
            source = f"{resource.get('source') or 'resource'}:{label}"
            rows.append(_vstore_resource(
                vstore,
                owner_type=str(resource.get('kind') or 'resource'),
                owner_id=str(resource.get('id') or resource.get('owner_id') or 'unknown'),
                locality=str(resource.get('locality') or 'unknown'),
                source=source,
            ))
    return rows


def _extend_resource_inventory_from_nested_vstores(resources: Dict[str, List[Dict[str, Any]]]) -> None:
    seen = {str(item.get('id')) for item in resources.get('vstores', []) or []}
    for key in ('kbs', 'tbs', 'tools'):
        for resource in list(resources.get(key, []) or []):
            for vstore_resource in _nested_vstore_resources_from_resource(resource):
                rid = str(vstore_resource.get('id'))
                if rid not in seen:
                    resources.setdefault('vstores', []).append(vstore_resource)
                    seen.add(rid)


def _extend_resource_inventory_from_task_infras(resources: Dict[str, List[Dict[str, Any]]], orch: Any) -> None:
    """Promote task-local TaskInfrastructure KB/TB/Tool/Universe objects into inventory.

    This is intentionally read-only and only inspects in-memory registries already
    attached to TaskInfrastructure.  It does not call KB searches, Tool execution,
    Universe health checks, deployments, or ingestion methods.
    """
    runtime = _safe_getattr(orch, 'runtime')
    for task_id, infra in (_safe_getattr(runtime, 'task_infras', {}) or {}).items():
        task_resources = summarize_resources_from_infra(
            infra,
            owner_type='task',
            owner_id=str(task_id),
            locality='task_local',
            source='task_infra',
        )
        for key, values in task_resources.items():
            resources.setdefault(key, []).extend(values or [])


def _extend_resource_inventory_from_worker(resources: Dict[str, List[Dict[str, Any]]], worker: Dict[str, Any]) -> None:
    infra_summary = worker.get('infrastructure') or {}
    for key in ('kbs', 'tbs', 'universes', 'apps'):
        resources.setdefault(key, []).extend((infra_summary.get('resources') or {}).get(key) or [])
    for vstore in ((infra_summary.get('memory') or {}).get('vstores') or []):
        resources['vstores'].append(_vstore_resource(vstore, owner_type='worker', owner_id=worker.get('id') or worker.get('task_id') or 'unknown', locality='task_local', source='worker_session'))


def _extend_resource_inventory_from_artifacts(resources: Dict[str, List[Dict[str, Any]]], orch_snapshot: Dict[str, Any]) -> None:
    artifacts = orch_snapshot.get('artifacts') if isinstance(orch_snapshot, dict) else None
    if not isinstance(artifacts, dict):
        return
    for task_id, records in artifacts.items():
        for record in records or []:
            if isinstance(record, dict):
                resources['artifacts'].append(_artifact_resource(record, task_id=str(task_id)))


def _collection_names(vstore: Dict[str, Any]) -> List[str]:
    names = []
    for key in ('collection_name', 'text_collection_name', 'vision_collection_name', 'table_collection_name'):
        value = vstore.get(key)
        if value:
            names.append(str(value))
    return names


def _event_field(event: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(event, dict) and name in event:
            return event.get(name)
        value = _safe_getattr(event, name, None)
        if value is not None:
            return value
    return default


def _event_payload(event: Any) -> Dict[str, Any]:
    payload = _event_field(event, 'payload', 'data', default={})
    return payload if isinstance(payload, dict) else {}


def _event_type(event: Any) -> str:
    return str(_event_field(event, 'type', 'event_type', 'status', default='event') or 'event')


def _event_severity(event: Any) -> str:
    explicit = str(_event_field(event, 'severity', 'level', default='') or '').lower()
    if explicit in {'error', 'warning', 'info'}:
        return explicit
    event_type = _event_type(event).lower()
    payload = _event_payload(event)
    text = f'{event_type} {payload} {_event_field(event, "message", "error", "reason", default="")}'.lower()
    if any(part in text for part in ('error', 'failed', 'exception', 'traceback')):
        return 'error'
    if any(part in text for part in ('warning', 'waiting', 'blocked', 'timeout', 'retry')):
        return 'warning'
    return 'info'


def summarize_events(events: Any, *, limit: int = 8) -> Dict[str, Any]:
    """Return a compact, redacted audit/event summary for snapshot display."""
    rows = list(events or [])[:500] if not isinstance(events, dict) else list(events.values())[:500]
    counts_by_type: Dict[str, int] = {}
    severity_counts: Dict[str, int] = {'info': 0, 'warning': 0, 'error': 0}
    recent: List[Dict[str, Any]] = []
    for event in rows:
        event_type = _event_type(event)
        severity = _event_severity(event)
        counts_by_type[event_type] = counts_by_type.get(event_type, 0) + 1
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        payload = _event_payload(event)
        if len(recent) < limit:
            recent.append({
                'id': _event_field(event, 'id', 'event_id'),
                'type': event_type,
                'severity': severity,
                'task_id': _event_field(event, 'task_id', 'node_id') or payload.get('task_id'),
                'timestamp': _event_field(event, 'timestamp', 'created_at', 'time'),
                'message': _event_field(event, 'message', 'summary', 'reason', 'error') or payload.get('message') or payload.get('reason') or payload.get('error'),
                'status': _event_field(event, 'status', 'state') or payload.get('status') or payload.get('state'),
                'action': payload.get('action'),
                'payload': redact_value(payload, max_depth=3),
            })
    return {
        'total': len(rows),
        'counts_by_type': dict(sorted(counts_by_type.items())),
        'severity_counts': severity_counts,
        'recent': recent,
    }


def build_audit_summary(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    totals: Dict[str, int] = {}
    severities: Dict[str, int] = {'info': 0, 'warning': 0, 'error': 0}
    recent: List[Dict[str, Any]] = []
    tasks_with_events = 0
    for row in snapshot.get('task_infrastructures', []) or []:
        summary = row.get('event_summary') or {}
        total = int(summary.get('total') or row.get('events') or 0)
        if total:
            tasks_with_events += 1
        for key, value in (summary.get('counts_by_type') or {}).items():
            totals[str(key)] = totals.get(str(key), 0) + int(value or 0)
        for key, value in (summary.get('severity_counts') or {}).items():
            severities[str(key)] = severities.get(str(key), 0) + int(value or 0)
        for event in summary.get('recent') or []:
            item = dict(event)
            item.setdefault('task_id', row.get('task_id'))
            recent.append(item)
    recent = sorted(recent, key=lambda item: str(item.get('timestamp') or ''), reverse=True)[:12]
    return {
        'total_events': sum(totals.values()),
        'tasks_with_events': tasks_with_events,
        'counts_by_type': dict(sorted(totals.items())),
        'severity_counts': severities,
        'recent_events': recent,
    }


def build_warnings(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    warnings: List[Dict[str, Any]] = []
    gateway_has_db = bool(snapshot.get('gateway_runtime', {}).get('db_client_present'))
    for worker in snapshot.get('worker_sessions', []) or []:
        wid = worker.get('id') or worker.get('task_id') or 'unknown'
        if worker.get('error'):
            warnings.append({'severity': 'error', 'code': 'worker_snapshot_error', 'owner': f'worker:{wid}', 'message': str(worker.get('error'))})
            continue
        if gateway_has_db and not worker.get('db_client_inherited'):
            warnings.append({'severity': 'warning', 'code': 'worker_db_client_not_inherited', 'owner': f'worker:{wid}', 'message': 'Worker clone is not reporting inherited gateway db_client.'})
        for vstore in (((worker.get('infrastructure') or {}).get('memory') or {}).get('vstores') or []):
            names = _collection_names(vstore)
            if not names:
                warnings.append({'severity': 'warning', 'code': 'vstore_collection_name_missing', 'owner': f'worker:{wid}', 'message': 'Worker memory vector store is missing a visible collection name.'})
            for name in names:
                if name.lower() in GENERIC_COLLECTION_NAMES:
                    warnings.append({'severity': 'warning', 'code': 'generic_worker_vstore_collection', 'owner': f'worker:{wid}', 'collection_name': name, 'message': 'Worker memory vector store is using a generic collection name.'})
                if not name.startswith('orch_'):
                    warnings.append({'severity': 'info', 'code': 'worker_vstore_namespace_unconfirmed', 'owner': f'worker:{wid}', 'collection_name': name, 'message': 'Worker memory vector store collection name does not show the expected orchestration namespace prefix.'})
            if vstore.get('rebuild_vstore') is True:
                warnings.append({'severity': 'error', 'code': 'worker_vstore_rebuild_enabled', 'owner': f'worker:{wid}', 'message': 'Worker memory vector store has rebuild_vstore enabled while using orchestration worker sessions.'})
    for deployment in snapshot.get('managed_deployments', []) or []:
        did = deployment.get('deployment_id') or deployment.get('name') or 'unknown'
        if deployment.get('endpoint_mismatch'):
            warnings.append({'severity': 'warning', 'code': 'deployment_endpoint_mismatch', 'owner': f'deployment:{did}', 'message': 'Managed deployment endpoint differs from infra.UNIVs registry; repair endpoint may be needed.', 'deployment_id': did})
        if deployment.get('kind') == 'universe' and not deployment.get('endpoint'):
            warnings.append({'severity': 'warning', 'code': 'deployment_endpoint_missing', 'owner': f'deployment:{did}', 'message': 'Managed Universe deployment has no usable endpoint metadata.', 'deployment_id': did})
        if str(deployment.get('status') or '').lower() in {'failed', 'error'}:
            warnings.append({'severity': 'error', 'code': 'deployment_failed', 'owner': f'deployment:{did}', 'message': 'Managed deployment reports failed/error status.', 'deployment_id': did})
    return warnings


def build_locality_edges(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    sid = snapshot.get('session_id') or 'session'
    edges = [{'from': f'session:{sid}', 'to': 'infra:main', 'kind': 'owns', 'label': 'session owns main infrastructure'}]
    if snapshot.get('gateway_runtime', {}).get('db_client_present'):
        edges.append({'from': f'session:{sid}', 'to': 'db_client:gateway', 'kind': 'owns', 'label': 'gateway-owned shared Chroma client'})
    for task in snapshot.get('tasks', []) or []:
        tid = task.get('id') or task.get('task_id')
        if tid:
            edges.append({'from': f'session:{sid}', 'to': f'task:{tid}', 'kind': 'contains', 'label': 'session contains task'})
    for worker in snapshot.get('worker_sessions', []) or []:
        tid = worker.get('task_id')
        agent = worker.get('agent_name')
        wid = f'worker:{tid}:{agent}'
        edges.append({'from': f'task:{tid}', 'to': wid, 'kind': 'uses', 'label': 'task uses worker clone'})
        if worker.get('db_client_inherited'):
            edges.append({'from': wid, 'to': 'db_client:gateway', 'kind': 'inherits', 'label': 'worker inherits gateway db_client'})
        for vstore in (((worker.get('infrastructure') or {}).get('memory') or {}).get('vstores') or []):
            for name in _collection_names(vstore) or [vstore.get('name') or 'vstore']:
                edges.append({'from': wid, 'to': f'vstore:{name}', 'kind': 'uses', 'label': 'worker uses task-local memory collection'})
    resource_map = snapshot.get('resources') or {}
    for group in ('kbs', 'tbs', 'tools', 'universes', 'apps'):
        for resource in resource_map.get(group) or []:
            if resource.get('owner_type') == 'task' and resource.get('owner_id'):
                edges.append({'from': f'task:{resource.get("owner_id")}', 'to': resource.get('id'), 'kind': 'uses', 'label': f'task uses {resource.get("kind")} resource'})
    for tool in resource_map.get('tools') or []:
        metadata = tool.get('metadata') or {}
        toolbox = metadata.get('toolbox')
        if toolbox:
            edges.append({'from': f'toolbox:{tool.get("owner_id")}', 'to': tool.get('id'), 'kind': 'contains', 'label': f'toolbox {toolbox} contains tool'})
    for group in ('kbs', 'tbs', 'tools'):
        for resource in resource_map.get(group) or []:
            for vstore in _nested_vstore_resources_from_resource(resource):
                edges.append({'from': resource.get('id'), 'to': vstore.get('id'), 'kind': 'uses', 'label': f'{resource.get("kind")} uses vector store'})
    for artifact in ((snapshot.get('resources') or {}).get('artifacts') or []):
        owner = artifact.get('owner_id')
        if owner:
            edges.append({'from': f'task:{owner}', 'to': artifact.get('id'), 'kind': 'produces', 'label': 'task produced artifact'})
    return edges


def _resource_counts(resources: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
    return {k: len(v or []) for k, v in resources.items()}


def _positive_int(value: Any) -> int | None:
    try:
        ivalue = int(value)
    except Exception:
        return None
    return ivalue if ivalue > 0 else None


def _read_json_file(path: Any) -> Dict[str, Any] | None:
    if not path:
        return None
    try:
        target = Path(str(path))
        if not target.exists():
            return None
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _safe_poll(handle: Any) -> Any:
    poll = getattr(handle, "poll", None)
    if callable(poll):
        try:
            return poll()
        except Exception:
            return "poll_error"
    return None


def _deployment_endpoint_from_entry(entry: Dict[str, Any], status_data: Dict[str, Any] | None = None) -> tuple[Dict[str, Any] | None, str]:
    """Resolve best known endpoint from status file, backend metadata, or entry metadata."""
    if isinstance(status_data, dict):
        state = str(status_data.get("status") or "").strip().lower()
        port = _positive_int(status_data.get("port"))
        if state in {"ready", "running"} and port:
            host = str(status_data.get("host") or "127.0.0.1")
            scheme = str(status_data.get("scheme") or "http")
            url = str(status_data.get("url") or f"{scheme}://{host}:{port}")
            return {"scheme": scheme, "host": host, "port": port, "url": url}, "status_file"

    for source, container in (("deployment_endpoint", entry.get("endpoint")), ("deployment_metadata", entry.get("meta_data"))):
        if isinstance(container, dict):
            port = _positive_int(container.get("port") or container.get("actual_port"))
            if port:
                host = str(container.get("host") or container.get("remote_host") or "127.0.0.1")
                scheme = str(container.get("scheme") or "http")
                url = str(container.get("url") or f"{scheme}://{host}:{port}")
                return {"scheme": scheme, "host": host, "port": port, "url": url}, source

    backend_handle = entry.get("backend_handle")
    endpoint = getattr(backend_handle, "endpoint", None)
    port = _positive_int(getattr(endpoint, "port", None))
    host = getattr(endpoint, "host", None)
    if endpoint is not None and host and port:
        scheme = str(getattr(endpoint, "scheme", None) or "http")
        url = str(getattr(endpoint, "url", None) or f"{scheme}://{host}:{port}")
        return {"scheme": scheme, "host": str(host), "port": port, "url": url}, "backend_handle"

    return None, "unknown"


def _deployment_log_paths(entry: Dict[str, Any]) -> Dict[str, Any]:
    paths: Dict[str, Any] = {}
    for canonical, aliases in {
        "stdout": ("stdout_file", "stdout_log"),
        "stderr": ("stderr_file", "stderr_log"),
        "status": ("status_file",),
        "params": ("params_file",),
    }.items():
        for alias in aliases:
            if entry.get(alias):
                paths[canonical] = entry.get(alias)
                break
    return paths


def summarize_managed_deployments(infra: Any) -> List[Dict[str, Any]]:
    """Return redacted lifecycle rows from infra.managed_deployments.

    This is deliberately separate from resources.universes.  resources.universes
    describes the interaction registry (infra.UNIVs); managed_deployments
    describes lifecycle handles, status files, PIDs, logs, and backend metadata.
    """
    deployments = _safe_getattr(infra, "managed_deployments", {}) or {}
    univs = _safe_getattr(infra, "UNIVs", {}) or {}
    rows: List[Dict[str, Any]] = []
    if not isinstance(deployments, dict):
        return rows

    for name, entry in sorted(deployments.items(), key=lambda item: str(item[0])):
        if not isinstance(entry, dict):
            rows.append({
                "deployment_id": str(name),
                "name": str(name),
                "kind": "unknown",
                "backend": "unknown",
                "status": "unknown",
                "metadata": redact_value(entry),
            })
            continue

        meta = entry.get("meta_data") if isinstance(entry.get("meta_data"), dict) else {}
        backend_handle = entry.get("backend_handle")
        handle = entry.get("handle")
        backend = str(entry.get("backend") or getattr(backend_handle, "backend", None) or meta.get("backend") or meta.get("deployment_type") or "unknown")
        kind = str(meta.get("type") or "universe")
        status = str(meta.get("status") or getattr(backend_handle, "state", None) or "unknown")

        rc = _safe_poll(handle)
        if rc is None and callable(getattr(handle, "poll", None)):
            status = "running"
        elif rc not in (None, "poll_error"):
            status = f"exited({rc})"

        status_data = _read_json_file(entry.get("status_file"))
        if isinstance(status_data, dict) and status_data.get("status"):
            status = str(status_data.get("status"))

        endpoint, endpoint_source = _deployment_endpoint_from_entry(entry, status_data=status_data)
        pid = meta.get("subprocess_pid") or getattr(handle, "pid", None) or getattr(getattr(backend_handle, "process", None), "pid", None)
        log_paths = _deployment_log_paths(entry)

        univ = univs.get(name) if isinstance(univs, dict) else None
        univ_info = _safe_getattr(univ, "info")
        registry_host = _safe_getattr(univ_info, "host")
        registry_port = _positive_int(_safe_getattr(univ_info, "port"))
        endpoint_port = _positive_int((endpoint or {}).get("port"))
        endpoint_mismatch = bool(
            endpoint_port and (
                registry_port is None or
                registry_port != endpoint_port or
                (registry_host and endpoint and str(registry_host) != str(endpoint.get("host")))
            )
        )

        row = {
            "deployment_id": str(name),
            "name": str(name),
            "kind": kind,
            "backend": backend,
            "status": status,
            "pid": pid,
            "endpoint": endpoint,
            "endpoint_source": endpoint_source,
            "url": (endpoint or {}).get("url"),
            "host": (endpoint or {}).get("host"),
            "port": (endpoint or {}).get("port"),
            "registry_endpoint": {
                "host": registry_host,
                "port": registry_port,
            } if univ_info is not None else None,
            "endpoint_mismatch": endpoint_mismatch,
            "files": redact_value(log_paths),
            "status_file_state": redact_value(status_data, max_depth=3),
            "created_at": meta.get("created_at") or getattr(backend_handle, "created_at", None),
            "updated_at": meta.get("updated_at") or getattr(backend_handle, "updated_at", None) or (status_data or {}).get("updated_at"),
            "can_probe": bool(endpoint and endpoint.get("url")),
            "can_view_logs": bool(log_paths.get("stdout") or log_paths.get("stderr")),
            "can_repair_endpoint": bool(endpoint and endpoint_mismatch),
            "can_terminate": True,
            "metadata": redact_value(meta, max_depth=4),
        }
        rows.append(row)
    return rows


def deployment_counts(deployments: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    by_backend: Dict[str, int] = {}
    endpoint_mismatch = 0
    for row in deployments or []:
        status = str(row.get("status") or "unknown")
        backend = str(row.get("backend") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_backend[backend] = by_backend.get(backend, 0) + 1
        if row.get("endpoint_mismatch"):
            endpoint_mismatch += 1
    return {
        "total": len(deployments or []),
        "by_status": dict(sorted(by_status.items())),
        "by_backend": dict(sorted(by_backend.items())),
        "endpoint_mismatch": endpoint_mismatch,
    }


async def build_infrastructure_snapshot(*, session_id: str, runtime: Optional[Dict[str, Any]], orch: Any = None) -> Dict[str, Any]:
    runtime = runtime or {}
    wf = runtime.get('wf')
    infra = runtime.get('infra') or getattr(wf, 'infra', None)
    config = runtime.get('config') or {}
    orch_snapshot: Dict[str, Any] = {}
    if orch is not None:
        try:
            orch_snapshot = await orch.snapshot()
        except Exception as exc:
            orch_snapshot = {'error': f'{type(exc).__name__}: {exc}'}
    worker_sessions = []
    if orch is not None and hasattr(orch, 'worker_sessions_snapshot'):
        try:
            worker_sessions = list(orch.worker_sessions_snapshot())
        except Exception as exc:
            worker_sessions = [{'error': f'{type(exc).__name__}: {exc}'}]
    main_infra = summarize_base_infra(infra, owner_type='session', owner_id=session_id, locality='main_workflow', source='main_infra')
    resources = {'kbs': [], 'tbs': [], 'tools': [], 'universes': [], 'apps': [], 'vstores': [], 'artifacts': []}
    for key, values in (main_infra.get('resources') or {}).items():
        resources.setdefault(key, []).extend(values)
    for vstore in ((main_infra.get('memory') or {}).get('vstores') or []):
        resources['vstores'].append(_vstore_resource(vstore, owner_type='session', owner_id=session_id, locality='main_workflow', source='main_infra'))
    if orch is not None:
        _extend_resource_inventory_from_task_infras(resources, orch)
    for worker in worker_sessions:
        _extend_resource_inventory_from_worker(resources, worker)
    _extend_resource_inventory_from_artifacts(resources, orch_snapshot)
    _extend_resource_inventory_from_nested_vstores(resources)
    tasks = list(orch_snapshot.get('tasks') or []) if isinstance(orch_snapshot, dict) else []
    managed_deployments = summarize_managed_deployments(infra)
    snapshot = {
        'type': 'infrastructure_snapshot',
        'session_id': session_id,
        'timestamp': datetime.now().isoformat(),
        'generated_at_epoch': time.time(),
        'gateway_runtime': {
            'session_dir': runtime.get('session_dir'),
            'db_client_present': runtime.get('db_client') is not None,
            'workflow_class': _class(wf),
            'agent_name': getattr(runtime.get('agent'), 'name', None),
            'orchestration_enabled': bool(config.get('orchestration_enabled')),
            'run_control_status': (runtime.get('run_control') or {}).get('status'),
        },
        'main_infrastructure': main_infra,
        'orchestration': {
            'enabled': bool(orch is not None and getattr(orch, 'enabled', False)),
            'started': bool(getattr(orch, '_started', False)) if orch is not None else False,
            'adapter': orch_snapshot.get('adapter'),
            'task_count': len(tasks),
            'active_adapter_task_ids': list(orch_snapshot.get('adapter_task_ids') or []),
            'agent_pool': orch_snapshot.get('agent_pool_summary') or orch_snapshot.get('agent_pool') or {},
            'task_graph': orch_snapshot.get('task_graph') or {},
            'artifact_count': sum(len(v or []) for v in (orch_snapshot.get('artifacts') or {}).values()) if isinstance(orch_snapshot.get('artifacts'), dict) else 0,
        },
        'tasks': tasks,
        'task_infrastructures': summarize_task_infras(orch) if orch is not None else [],
        'worker_sessions': worker_sessions,
        'resources': resources,
        'resource_counts': _resource_counts(resources),
        'managed_deployments': managed_deployments,
        'deployment_counts': deployment_counts(managed_deployments),
        'locality_edges': [],
        'warnings': [],
        'audit_summary': {},
    }
    snapshot['warnings'] = build_warnings(snapshot)
    snapshot['locality_edges'] = build_locality_edges(snapshot)
    snapshot['audit_summary'] = build_audit_summary(snapshot)
    return redact_value(snapshot)
