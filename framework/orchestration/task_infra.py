from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional
import copy


@dataclass(slots=True)
class SharedResources:
    objects: list[Any] = field(default_factory=list)
    toolboxes: Dict[str, Any] = field(default_factory=dict)
    knowledge_bases: Dict[str, Any] = field(default_factory=dict)
    universes: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LocalTaskState:
    task_id: str
    workflow_type: str
    session_dir: str
    artifact_dir: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class SimpleLocalHistory:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
    def add(self, sender: str, content: Any) -> None:
        self.entries.append({'sender': sender, 'content': content})


class SimpleLocalMemory:
    def __init__(self) -> None:
        self.fragments: dict[str, list[Any]] = {}


class SimpleLocalContext:
    def __init__(self) -> None:
        self.snapshots: list[str] = []


class TaskInfrastructure:
    def __init__(self, shared: SharedResources, local: LocalTaskState, compat_infra: Any = None, chat_manager: Any = None, memory_manager: Any = None, context_manager: Any = None) -> None:
        self.shared = shared
        self.local = local
        self.compat_infra = compat_infra
        self.chat_manager = chat_manager or SimpleLocalHistory()
        self.memory_manager = memory_manager or SimpleLocalMemory()
        self.context_manager = context_manager or SimpleLocalContext()
        self.objects = list(shared.objects)
        self.KBs = shared.knowledge_bases
        self.TBs = shared.toolboxes
        self.UNIVs = shared.universes
        self.events: list[dict[str, Any]] = []

    def append_chat_history(self, actor: str, content: Any, action: Any = None, log_console: bool = False) -> None:
        self.update_history(actor, content, action=action, log_console=log_console)

    def update_history(self, actor: str, content: Any, action: Any = None, log_console: bool = False) -> None:
        self.events.append({'actor': actor, 'content': content, 'action': action, 'log_console': log_console})
        if hasattr(self.compat_infra, 'append_chat_history'):
            self.compat_infra.append_chat_history(actor=actor, content=content, action=action, log_console=log_console)
        elif hasattr(self.chat_manager, 'add'):
            self.chat_manager.add(actor, content)


class TaskInfrastructureFactory:
    def __init__(self, shared_resources: Optional[SharedResources] = None, compat_builder: Optional[Callable[..., Any]] = None, session_root: str = '.gateway_sessions') -> None:
        self.shared_resources = shared_resources or SharedResources()
        self.compat_builder = compat_builder
        self.session_root = Path(session_root)
        self.session_root.mkdir(parents=True, exist_ok=True)

    def create(self, task_id: str, workflow_type: str, metadata: Optional[Dict[str, Any]] = None) -> TaskInfrastructure:
        session_dir = self.session_root / task_id
        artifact_dir = session_dir / 'artifacts'
        session_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        local = LocalTaskState(
            task_id=task_id,
            workflow_type=workflow_type,
            session_dir=str(session_dir),
            artifact_dir=str(artifact_dir),
            metadata=metadata or {},
        )
        compat_infra = None
        if self.compat_builder is not None:
            compat_infra = self.compat_builder(task_id=task_id, workflow_type=workflow_type, session_dir=str(session_dir), metadata=metadata or {})
        return TaskInfrastructure(shared=copy.deepcopy(self.shared_resources), local=local, compat_infra=compat_infra)
