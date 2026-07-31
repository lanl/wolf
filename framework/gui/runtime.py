from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from framework.cli.config_loader import build_launch_config, to_jsonable
from framework.cli.discovery import get_workflows
from framework.cli.session_commands import list_sessions


def now_ts() -> float:
    return time.time()


@dataclass
class WorkspaceState:
    mode: str = "browser"
    url: str = "about:blank"
    title: str = "Blank workspace"
    file_path: Optional[str] = None
    glance_url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Annotation:
    id: str
    kind: str
    author: str
    workspace_mode: str
    x: float
    y: float
    w: Optional[float] = None
    h: Optional[float] = None
    label: str = ""
    color: str = "#7dd3fc"
    created_at: float = field(default_factory=now_ts)
    metadata: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    workflow: Optional[str] = None
    source: str = "user"
    universe: Optional[str] = None


@dataclass
class ChatMessage:
    id: str
    role: str
    content: str
    created_at: float = field(default_factory=now_ts)
    visual_context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GuiEvent:
    seq: int
    type: str
    created_at: float
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkspaceApp:
    id: str
    name: str
    kind: str
    url: str
    source: str = "agent"
    universe: Optional[str] = None
    status: str = "running"
    created_by: str = "agent"
    created_at: float = field(default_factory=now_ts)
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    workflow: Optional[str] = None
    host_status: str = "unknown"


@dataclass
class DashboardPanel:
    id: str
    dashboard_id: str
    name: str
    kind: str = "html"
    url: str = "about:blank"
    title: str = ""
    content_html: Optional[str] = None
    layout: Dict[str, Any] = field(default_factory=dict)
    source: str = "agent"
    universe: Optional[str] = None
    created_by: str = "agent"
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    status: str = "ready"
    metadata: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    workflow: Optional[str] = None
    host_status: str = "unknown"


@dataclass
class Dashboard:
    id: str
    name: str
    layout: str = "grid"
    description: str = ""
    panels: List[DashboardPanel] = field(default_factory=list)
    source: str = "agent"
    universe: Optional[str] = None
    created_by: str = "agent"
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    metadata: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    workflow: Optional[str] = None
    host_status: str = "unknown"


class GuiRuntime:
    """Small in-memory state container for the milestone-1 GUI.

    This class deliberately keeps no background workflow running yet. It stores
    the shared visual workspace state, annotations, chat messages, and a simple
    event log. Milestone 3 can replace/extend the placeholder message handling
    with real Wolf workflow runtime integration.
    """

    def __init__(self, launch_config: Optional[Dict[str, Any]] = None, workspace: str = "wf_workspace") -> None:
        self.launch_config = launch_config or build_launch_config(None, {"mode": "gui"})
        self.workspace_root = workspace
        self.workspace = WorkspaceState()
        self.annotations: List[Annotation] = []
        self.apps: List[WorkspaceApp] = []
        self.dashboards: List[Dashboard] = []
        self.active_dashboard_id: Optional[str] = None
        self.messages: List[ChatMessage] = [
            ChatMessage(
                id=self._id("msg"),
                role="assistant",
                content=(
                    "Wolf GUI visual workspace is online. Load a URL or Glance view, "
                    "place annotations, then ask the agent about what you are seeing."
                ),
                metadata={"system": True},
            )
        ]
        self.events: List[GuiEvent] = []
        self._seq = 0
        self.controller = GuiWorkspaceController(self)
        self.emit("runtime_started", {"workspace": asdict(self.workspace)})

    def _id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def emit(self, event_type: str, payload: Dict[str, Any]) -> GuiEvent:
        self._seq += 1
        event = GuiEvent(seq=self._seq, type=event_type, created_at=now_ts(), payload=payload)
        self.events.append(event)
        if len(self.events) > 1000:
            self.events = self.events[-1000:]
        return event

    def bootstrap(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "workspace": asdict(self.workspace),
            "annotations": [asdict(a) for a in self.annotations],
            "messages": [asdict(m) for m in self.messages],
            "apps": self.apps_list(),
            "dashboards": self.dashboards_list(),
            "launch_config": self.redacted_launch_config(),
            "workflows": self.workflows(),
            "sessions": self.sessions(),
        }

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "wolf-gui",
            "workspace": asdict(self.workspace),
            "messages": len(self.messages),
            "annotations": len(self.annotations),
            "apps": len(self.apps),
            "dashboards": len(self.dashboards),
            "events": len(self.events),
        }

    def workflows(self) -> Dict[str, Any]:
        try:
            return get_workflows()
        except Exception as exc:
            return {"error": str(exc)}

    def sessions(self) -> List[Dict[str, Any]]:
        try:
            return to_jsonable(list_sessions(workspace=self.workspace_root))
        except Exception as exc:
            return [{"error": str(exc)}]

    def redacted_launch_config(self) -> Dict[str, Any]:
        cfg = to_jsonable(self.launch_config)
        session = cfg.get("session") if isinstance(cfg, dict) else None
        llms = session.get("LLMs") if isinstance(session, dict) else None
        if isinstance(llms, dict):
            for value in llms.values():
                if isinstance(value, dict):
                    for key in list(value):
                        if "key" in key.lower() or "token" in key.lower() or "secret" in key.lower():
                            value[key] = "***redacted***"
        return cfg


    def apps_list(self) -> List[Dict[str, Any]]:
        return [asdict(a) for a in self.apps]

    def register_app(self, data: Dict[str, Any]) -> Dict[str, Any]:
        app = WorkspaceApp(
            id=str(data.get("id") or self._id("app")),
            name=str(data.get("name") or "Workspace App"),
            kind=str(data.get("kind") or "custom"),
            url=str(data.get("url") or "about:blank"),
            source=str(data.get("source") or "agent"),
            universe=data.get("universe"),
            status=str(data.get("status") or "running"),
            created_by=str(data.get("created_by") or "agent"),
            description=str(data.get("description") or ""),
            metadata=dict(data.get("metadata") or {}),
            session_id=data.get("session_id"),
            workflow=data.get("workflow"),
            host_status=str(data.get("host_status") or "unknown"),
        )
        self.apps = [a for a in self.apps if a.id != app.id and a.url != app.url]
        self.apps.insert(0, app)
        if len(self.apps) > 200:
            self.apps = self.apps[:200]
        self.emit("app_registered", asdict(app))
        return asdict(app)


    def remove_app(self, app_id: str) -> Dict[str, Any]:
        before = len(self.apps)
        self.apps = [a for a in self.apps if a.id != app_id]
        removed = before - len(self.apps)
        self.emit("app_removed", {"app_id": app_id, "removed": removed})
        return {"ok": True, "removed": removed, "app_id": app_id}

    def open_app(self, app_id: Optional[str] = None, url: Optional[str] = None) -> Dict[str, Any]:
        app = None
        if app_id:
            for candidate in self.apps:
                if candidate.id == app_id:
                    app = candidate
                    break
        if app is None and url:
            for candidate in self.apps:
                if candidate.url == url:
                    app = candidate
                    break
        if app is None and self.apps:
            app = self.apps[0]
        if app is None:
            raise ValueError("No app found to open")

        self.workspace.mode = "browser" if app.kind not in {"glance"} else "glance"
        self.workspace.url = app.url
        self.workspace.title = app.name
        self.workspace.metadata = {
            **(self.workspace.metadata or {}),
            "active_app_id": app.id,
            "active_app_kind": app.kind,
            "active_app_source": app.source,
        }
        self.emit("workspace_app_opened", {"app": asdict(app), "workspace": asdict(self.workspace)})
        return {"app": asdict(app), "workspace": asdict(self.workspace)}

    def open_url(self, url: str) -> Dict[str, Any]:
        normalized = (url or "").strip()
        if not normalized:
            normalized = "about:blank"
        if normalized != "about:blank" and "://" not in normalized and not normalized.startswith("/"):
            normalized = "https://" + normalized
        self.workspace.mode = "browser"
        self.workspace.url = normalized
        self.workspace.title = normalized
        self.workspace.file_path = None
        self.emit("workspace_opened", {"mode": "browser", "url": normalized})
        return asdict(self.workspace)

    def open_glance(self, url: Optional[str] = None, path: Optional[str] = None, name: Optional[str] = None) -> Dict[str, Any]:
        target_url = (url or "").strip() or "https://kitware.github.io/glance/app/"
        self.workspace.mode = "glance"
        self.workspace.url = target_url
        self.workspace.glance_url = target_url
        self.workspace.file_path = path
        self.workspace.title = name or path or "Glance workspace"
        self.workspace.metadata = {"integration": "iframe", "path": path, "name": name}
        self.emit("workspace_opened", {"mode": "glance", "url": target_url, "path": path, "name": name})
        return asdict(self.workspace)

    def add_annotation(self, data: Dict[str, Any]) -> Dict[str, Any]:
        ann = Annotation(
            id=str(data.get("id") or self._id("ann")),
            kind=str(data.get("kind") or "point"),
            author=str(data.get("author") or "user"),
            workspace_mode=self.workspace.mode,
            x=float(data.get("x") or 0.0),
            y=float(data.get("y") or 0.0),
            w=float(data["w"]) if data.get("w") is not None else None,
            h=float(data["h"]) if data.get("h") is not None else None,
            label=str(data.get("label") or ""),
            color=str(data.get("color") or "#7dd3fc"),
            metadata=dict(data.get("metadata") or {}),
            session_id=data.get("session_id"),
            workflow=data.get("workflow"),
            source=str(data.get("source") or data.get("author") or "user"),
            universe=data.get("universe"),
        )
        self.annotations.append(ann)
        self.emit("annotation_created", asdict(ann))
        return asdict(ann)

    def clear_annotations(self) -> Dict[str, Any]:
        count = len(self.annotations)
        self.annotations.clear()
        self.emit("annotations_cleared", {"count": count})
        return {"ok": True, "cleared": count}

    def pointer_event(self, data: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "id": self._id("ptr"),
            "author": data.get("author") or "user",
            "workspace_mode": self.workspace.mode,
            "x": data.get("x"),
            "y": data.get("y"),
            "label": data.get("label") or "",
            "created_at": now_ts(),
            "metadata": data.get("metadata") or {},
        }
        self.emit("pointer_event", payload)
        return payload


    def dashboards_list(self) -> List[Dict[str, Any]]:
        return [asdict(d) for d in self.dashboards]

    def _find_dashboard(self, dashboard_id: Optional[str] = None) -> Optional[Dashboard]:
        if dashboard_id:
            for dashboard in self.dashboards:
                if dashboard.id == dashboard_id:
                    return dashboard
        if self.active_dashboard_id:
            for dashboard in self.dashboards:
                if dashboard.id == self.active_dashboard_id:
                    return dashboard
        return self.dashboards[0] if self.dashboards else None

    def create_dashboard(self, data: Dict[str, Any]) -> Dict[str, Any]:
        dashboard = Dashboard(
            id=str(data.get("id") or self._id("dash")),
            name=str(data.get("name") or "Agent Dashboard"),
            layout=str(data.get("layout") or "grid"),
            description=str(data.get("description") or ""),
            source=str(data.get("source") or "agent"),
            universe=data.get("universe"),
            created_by=str(data.get("created_by") or "agent"),
            metadata=dict(data.get("metadata") or {}),
            session_id=data.get("session_id"),
            workflow=data.get("workflow"),
            host_status=str(data.get("host_status") or "unknown"),
        )
        self.dashboards = [d for d in self.dashboards if d.id != dashboard.id]
        self.dashboards.insert(0, dashboard)
        if len(self.dashboards) > 100:
            self.dashboards = self.dashboards[:100]
        self.emit("dashboard_created", asdict(dashboard))
        return asdict(dashboard)

    def add_dashboard_panel(self, data: Dict[str, Any]) -> Dict[str, Any]:
        dashboard = self._find_dashboard(data.get("dashboard_id"))
        if dashboard is None:
            dashboard_data = {
                "name": data.get("dashboard_name") or "Agent Dashboard",
                "source": data.get("source") or "agent",
                "universe": data.get("universe"),
                "created_by": data.get("created_by") or "agent",
                "session_id": data.get("session_id"),
                "workflow": data.get("workflow"),
                "host_status": data.get("host_status") or "unknown",
            }
            created = self.create_dashboard(dashboard_data)
            dashboard = self._find_dashboard(created.get("id"))
        if dashboard is None:
            raise ValueError("No dashboard available for panel")
        panel = DashboardPanel(
            id=str(data.get("id") or self._id("panel")),
            dashboard_id=dashboard.id,
            name=str(data.get("name") or "Dashboard Panel"),
            kind=str(data.get("kind") or "html"),
            url=str(data.get("url") or "about:blank"),
            title=str(data.get("title") or data.get("name") or "Dashboard Panel"),
            content_html=data.get("content_html"),
            layout=dict(data.get("layout") or {}),
            source=str(data.get("source") or dashboard.source or "agent"),
            universe=data.get("universe") or dashboard.universe,
            created_by=str(data.get("created_by") or dashboard.created_by or "agent"),
            status=str(data.get("status") or "ready"),
            metadata=dict(data.get("metadata") or {}),
            session_id=data.get("session_id") or dashboard.session_id,
            workflow=data.get("workflow") or dashboard.workflow,
            host_status=str(data.get("host_status") or dashboard.host_status or "unknown"),
        )
        dashboard.panels = [p for p in dashboard.panels if p.id != panel.id]
        dashboard.panels.append(panel)
        dashboard.updated_at = now_ts()
        self.emit("dashboard_panel_added", {"dashboard": asdict(dashboard), "panel": asdict(panel)})
        return asdict(panel)

    def update_dashboard_panel(self, panel_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        for dashboard in self.dashboards:
            for panel in dashboard.panels:
                if panel.id == panel_id:
                    for key in ("name", "kind", "url", "title", "content_html", "status", "source", "universe", "created_by", "session_id", "workflow", "host_status"):
                        if key in data:
                            setattr(panel, key, data[key])
                    if "layout" in data:
                        panel.layout = dict(data.get("layout") or {})
                    if "metadata" in data:
                        panel.metadata = dict(data.get("metadata") or {})
                    panel.updated_at = now_ts()
                    dashboard.updated_at = panel.updated_at
                    payload = {"dashboard": asdict(dashboard), "panel": asdict(panel)}
                    self.emit("dashboard_panel_updated", payload)
                    return asdict(panel)
        raise ValueError(f"Dashboard panel not found: {panel_id}")

    def open_dashboard(self, dashboard_id: Optional[str] = None) -> Dict[str, Any]:
        dashboard = self._find_dashboard(dashboard_id)
        if dashboard is None:
            raise ValueError("No dashboard found to open")
        self.active_dashboard_id = dashboard.id
        self.workspace.mode = "dashboard"
        self.workspace.url = f"about:dashboard/{dashboard.id}"
        self.workspace.title = dashboard.name
        self.workspace.file_path = None
        self.workspace.glance_url = None
        self.workspace.metadata = {
            **(self.workspace.metadata or {}),
            "active_dashboard_id": dashboard.id,
            "active_dashboard_layout": dashboard.layout,
        }
        payload = {"dashboard": asdict(dashboard), "workspace": asdict(self.workspace)}
        self.emit("dashboard_opened", payload)
        return payload

    def add_message(self, content: str, visual_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        user_msg = ChatMessage(
            id=self._id("msg"),
            role="user",
            content=content,
            visual_context=visual_context or {},
        )
        self.messages.append(user_msg)
        self.emit("message_created", asdict(user_msg))

        # Placeholder response until the GUI is wired to the workflow runtime.
        assistant_msg = ChatMessage(
            id=self._id("msg"),
            role="assistant",
            content=(
                "Captured your message with visual context. "
                "Workflow-backed agent execution will be connected in the next milestone."
            ),
            visual_context={
                "received_workspace": asdict(self.workspace),
                "received_annotation_count": len((visual_context or {}).get("annotations", [])),
            },
            metadata={"placeholder": True},
        )
        self.messages.append(assistant_msg)
        self.emit("message_created", asdict(assistant_msg))
        return {"ok": True, "messages": [asdict(user_msg), asdict(assistant_msg)]}

    def events_since(self, seq: int = 0) -> Dict[str, Any]:
        events = [e for e in self.events if e.seq > seq]
        return {"ok": True, "events": [asdict(e) for e in events], "latest_seq": self._seq}


class GuiWorkspaceController:
    """Runtime-native adapter for workflows/agents to control GUI without HTTP hops."""

    def __init__(self, runtime: GuiRuntime):
        self.runtime = runtime

    def register_app(self, *, name: str, url: str, kind: str = "custom", source: str = "agent", universe: Optional[str] = None,
                     created_by: str = "agent", description: str = "", metadata: Optional[Dict[str, Any]] = None,
                     session_id: Optional[str] = None, workflow: Optional[str] = None, host_status: str = "unknown") -> Dict[str, Any]:
        return self.runtime.register_app({
            "name": name,
            "url": url,
            "kind": kind,
            "source": source,
            "universe": universe,
            "created_by": created_by,
            "description": description,
            "metadata": metadata or {},
            "session_id": session_id,
            "workflow": workflow,
            "host_status": host_status,
        })

    def open_app(self, *, app_id: Optional[str] = None, url: Optional[str] = None) -> Dict[str, Any]:
        return self.runtime.open_app(app_id=app_id, url=url)

    def annotate(self, *, kind: str, x: float, y: float, w: Optional[float] = None, h: Optional[float] = None,
                 label: str = "", color: str = "#7dd3fc", author: str = "agent", metadata: Optional[Dict[str, Any]] = None,
                 session_id: Optional[str] = None, workflow: Optional[str] = None, source: str = "agent",
                 universe: Optional[str] = None) -> Dict[str, Any]:
        return self.runtime.add_annotation({
            "kind": kind,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "label": label,
            "color": color,
            "author": author,
            "metadata": metadata or {},
            "session_id": session_id,
            "workflow": workflow,
            "source": source,
            "universe": universe,
        })

    def create_dashboard(self, **kwargs: Any) -> Dict[str, Any]:
        return self.runtime.create_dashboard(kwargs)

    def add_dashboard_panel(self, **kwargs: Any) -> Dict[str, Any]:
        return self.runtime.add_dashboard_panel(kwargs)

    def update_dashboard_panel(self, *, panel_id: str, **kwargs: Any) -> Dict[str, Any]:
        return self.runtime.update_dashboard_panel(panel_id, kwargs)

    def open_dashboard(self, *, dashboard_id: Optional[str] = None) -> Dict[str, Any]:
        return self.runtime.open_dashboard(dashboard_id=dashboard_id)

    def notify(self, message: str, level: str = "info", source: str = "agent") -> Dict[str, Any]:
        payload = {"id": self.runtime._id("note"), "message": message, "level": level, "source": source, "created_at": now_ts()}
        self.runtime.emit("agent_status", payload)
        return payload


class GuiControllerClient:
    """Small in-process wrapper around GuiWorkspaceController for workflow-friendly usage."""

    def __init__(self, controller: GuiWorkspaceController):
        self.controller = controller

    @classmethod
    def from_runtime(cls, runtime: GuiRuntime) -> "GuiControllerClient":
        return cls(runtime.controller)

    def register(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.register_app(**kwargs)

    def open(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.open_app(**kwargs)

    def annotate(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.annotate(**kwargs)

    def create_dashboard(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.create_dashboard(**kwargs)

    def add_panel(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.add_dashboard_panel(**kwargs)

    def update_panel(self, *, panel_id: str, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.update_dashboard_panel(panel_id=panel_id, **kwargs)

    def open_dashboard(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.open_dashboard(**kwargs)

    def notify(self, message: str, level: str = "info", source: str = "agent") -> Dict[str, Any]:
        return self.controller.notify(message=message, level=level, source=source)

    def publish_dashboard(
        self,
        *,
        name: str,
        panels: List[Dict[str, Any]],
        layout: str = "grid",
        source: str = "agent",
        universe: Optional[str] = None,
        created_by: str = "agent",
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        workflow: Optional[str] = None,
        host_status: str = "unknown",
        open_after_create: bool = True,
    ) -> Dict[str, Any]:
        dashboard = self.controller.create_dashboard(
            name=name,
            layout=layout,
            source=source,
            universe=universe,
            created_by=created_by,
            description=description,
            metadata=metadata or {},
            session_id=session_id,
            workflow=workflow,
            host_status=host_status,
        )
        created_panels = []
        for panel in panels:
            created_panels.append(self.controller.add_dashboard_panel(
                dashboard_id=dashboard.get("id"),
                source=panel.get("source", source),
                universe=panel.get("universe", universe),
                created_by=panel.get("created_by", created_by),
                session_id=panel.get("session_id", session_id),
                workflow=panel.get("workflow", workflow),
                host_status=panel.get("host_status", host_status),
                **{k: v for k, v in panel.items() if k not in {"source", "universe", "created_by", "session_id", "workflow", "host_status"}},
            ))
        opened = self.controller.open_dashboard(dashboard_id=dashboard.get("id")) if open_after_create else None
        note = self.controller.notify(message=f"Agent opened dashboard: {dashboard.get('name')}", level="info", source=source) if open_after_create else None
        return {"dashboard": dashboard, "panels": created_panels, "opened": opened, "status": note}

    def publish(
        self,
        *,
        name: str,
        url: str,
        kind: str = "custom",
        source: str = "actionbox",
        universe: Optional[str] = None,
        created_by: str = "agent",
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        workflow: Optional[str] = None,
        host_status: str = "unknown",
        status_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        app = self.controller.register_app(
            name=name,
            url=url,
            kind=kind,
            source=source,
            universe=universe,
            created_by=created_by,
            description=description,
            metadata=metadata or {},
            session_id=session_id,
            workflow=workflow,
            host_status=host_status,
        )
        opened = self.controller.open_app(app_id=app.get("id"))
        note = self.controller.notify(
            message=status_message or f"Agent opened {app.get('name', 'workspace app')}",
            level="info",
            source=source,
        )
        return {"app": app, "opened": opened, "status": note}
