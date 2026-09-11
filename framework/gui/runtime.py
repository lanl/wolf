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
    zoom: float = 1.0
    min_zoom: float = 0.25
    max_zoom: float = 3.0
    zoom_step: float = 0.1


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
        self.approvals: List[Dict[str, Any]] = []
        self.chat_scale: float = 1.0
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
            "approvals": self.approval_requests_list(include_done=False),
            "chat_scale": self.chat_scale,
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
            "chat_scale": self.chat_scale,
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

    def _truthy(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _should_open_after_panel_add(self, data: Dict[str, Any], panel: DashboardPanel, dashboard: Dashboard) -> bool:
        """Decide whether adding a dashboard panel should make that dashboard visible.

        Agent/actionbox dashboard panel creation is a visual action; by default it
        should produce visible feedback. Callers that are building a dashboard in
        batches, such as publish_dashboard, can pass open_after_add=False and open
        once after all panels are installed.
        """
        for key in ("open_after_add", "open", "auto_open"):
            if key in data:
                return self._truthy(data.get(key))
        actor = str(data.get("created_by") or panel.created_by or "").lower()
        source = str(data.get("source") or panel.source or dashboard.source or "").lower()
        return actor in {"agent", "actionbox"} or source in {"agent", "actionbox"}

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
            zoom=self._coerce_zoom(data.get("zoom", 1.0), data.get("min_zoom", 0.25), data.get("max_zoom", 3.0)),
            min_zoom=float(data.get("min_zoom") or 0.25),
            max_zoom=float(data.get("max_zoom") or 3.0),
            zoom_step=float(data.get("zoom_step") or 0.1),
        )
        dashboard.panels = [p for p in dashboard.panels if p.id != panel.id]
        dashboard.panels.append(panel)
        dashboard.updated_at = now_ts()
        payload = {"dashboard": asdict(dashboard), "panel": asdict(panel)}
        self.emit("dashboard_panel_added", payload)
        if self._should_open_after_panel_add(data, panel, dashboard):
            payload["opened"] = self.open_dashboard(dashboard_id=dashboard.id)
        return asdict(panel)

    def update_dashboard_panel(self, panel_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        for dashboard in self.dashboards:
            for panel in dashboard.panels:
                if panel.id == panel_id:
                    for key in ("name", "kind", "url", "title", "content_html", "status", "source", "universe", "created_by", "session_id", "workflow", "host_status"):
                        if key in data:
                            setattr(panel, key, data[key])
                    if "min_zoom" in data:
                        panel.min_zoom = float(data.get("min_zoom") or panel.min_zoom)
                    if "max_zoom" in data:
                        panel.max_zoom = float(data.get("max_zoom") or panel.max_zoom)
                    if "zoom_step" in data:
                        panel.zoom_step = float(data.get("zoom_step") or panel.zoom_step)
                    if "zoom" in data:
                        panel.zoom = self._coerce_zoom(data.get("zoom"), panel.min_zoom, panel.max_zoom)
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


    def _coerce_zoom(self, value: Any, min_zoom: Any = 0.25, max_zoom: Any = 3.0) -> float:
        try:
            lo = float(min_zoom if min_zoom is not None else 0.25)
        except (TypeError, ValueError):
            lo = 0.25
        try:
            hi = float(max_zoom if max_zoom is not None else 3.0)
        except (TypeError, ValueError):
            hi = 3.0
        if hi < lo:
            lo, hi = hi, lo
        try:
            zoom = float(value if value is not None else 1.0)
        except (TypeError, ValueError):
            zoom = 1.0
        return round(max(lo, min(hi, zoom)), 3)

    def _find_dashboard_panel(self, panel_id: str) -> tuple[Dashboard, DashboardPanel]:
        panel_id = str(panel_id or "")
        for dashboard in self.dashboards:
            for panel in dashboard.panels:
                if panel.id == panel_id:
                    return dashboard, panel
        raise ValueError(f"Dashboard panel not found: {panel_id}")

    def _record_dashboard_note(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        msg = ChatMessage(
            id=self._id("msg"),
            role="system",
            content=content,
            metadata={"dashboard_control": True, **(metadata or {})},
        )
        self.messages.append(msg)
        self.emit("message_created", asdict(msg))
        return asdict(msg)


    def _coerce_chat_scale(self, value: Any) -> float:
        try:
            scale = float(value if value is not None else 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        return round(max(0.75, min(1.6, scale)), 3)

    def _record_chat_control_note(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        msg = ChatMessage(
            id=self._id("msg"),
            role="system",
            content=content,
            metadata={"chat_control": True, **(metadata or {})},
        )
        self.messages.append(msg)
        self.emit("message_created", asdict(msg))
        return asdict(msg)

    def set_chat_scale(self, scale: Any, source: str = "agent") -> Dict[str, Any]:
        old_scale = float(getattr(self, "chat_scale", 1.0) or 1.0)
        self.chat_scale = self._coerce_chat_scale(scale)
        payload = {"chat_scale": self.chat_scale, "old_chat_scale": old_scale, "source": source}
        self.emit("chat_scale_changed", payload)
        self._record_chat_control_note(
            f"Agent chat display size set to {round(self.chat_scale * 100)}% by {source}.",
            {"event": "chat_scale_changed", "chat_scale": self.chat_scale, "old_chat_scale": old_scale, "source": source},
        )
        return payload

    def adjust_chat_scale(self, delta: Any, source: str = "agent") -> Dict[str, Any]:
        try:
            amount = float(delta)
        except (TypeError, ValueError):
            amount = 0.1
        return self.set_chat_scale(float(getattr(self, "chat_scale", 1.0) or 1.0) + amount, source=source)

    def reset_chat_scale(self, source: str = "agent") -> Dict[str, Any]:
        return self.set_chat_scale(1.0, source=source)

    def set_dashboard_panel_zoom(self, panel_id: str, zoom: Any, source: str = "agent") -> Dict[str, Any]:
        dashboard, panel = self._find_dashboard_panel(panel_id)
        old_zoom = float(panel.zoom or 1.0)
        panel.zoom = self._coerce_zoom(zoom, panel.min_zoom, panel.max_zoom)
        panel.updated_at = now_ts()
        dashboard.updated_at = panel.updated_at
        payload = {"dashboard": asdict(dashboard), "panel": asdict(panel), "old_zoom": old_zoom, "zoom": panel.zoom, "source": source}
        self.emit("dashboard_panel_zoom_changed", payload)
        pct = round(panel.zoom * 100)
        self._record_dashboard_note(
            f"Dashboard panel '{panel.title or panel.name}' zoom set to {pct}% by {source}.",
            {"event": "dashboard_panel_zoom_changed", "panel_id": panel.id, "dashboard_id": dashboard.id, "zoom": panel.zoom, "old_zoom": old_zoom, "source": source},
        )
        return payload

    def adjust_dashboard_panel_zoom(self, panel_id: str, delta: Any, source: str = "agent") -> Dict[str, Any]:
        _dashboard, panel = self._find_dashboard_panel(panel_id)
        try:
            amount = float(delta)
        except (TypeError, ValueError):
            amount = float(panel.zoom_step or 0.1)
        return self.set_dashboard_panel_zoom(panel_id, float(panel.zoom or 1.0) + amount, source=source)

    def reset_dashboard_panel_zoom(self, panel_id: str, source: str = "agent") -> Dict[str, Any]:
        return self.set_dashboard_panel_zoom(panel_id, 1.0, source=source)

    def remove_dashboard_panel(self, panel_id: str, source: str = "agent") -> Dict[str, Any]:
        dashboard, panel = self._find_dashboard_panel(panel_id)
        dashboard.panels = [p for p in dashboard.panels if p.id != panel.id]
        dashboard.updated_at = now_ts()
        payload = {"dashboard": asdict(dashboard), "panel": asdict(panel), "panel_id": panel.id, "source": source}
        self.emit("dashboard_panel_removed", payload)
        self._record_dashboard_note(
            f"Dashboard panel '{panel.title or panel.name}' was closed by {source}.",
            {"event": "dashboard_panel_removed", "panel_id": panel.id, "dashboard_id": dashboard.id, "source": source},
        )
        return payload

    def remove_dashboard(self, dashboard_id: Optional[str] = None, source: str = "agent") -> Dict[str, Any]:
        dashboard = self._find_dashboard(dashboard_id)
        if dashboard is None:
            raise ValueError("No dashboard found to close")
        removed = asdict(dashboard)
        self.dashboards = [d for d in self.dashboards if d.id != dashboard.id]
        next_dashboard = self.dashboards[0] if self.dashboards else None
        if next_dashboard is not None:
            self.active_dashboard_id = next_dashboard.id
            self.workspace.mode = "dashboard"
            self.workspace.url = f"about:dashboard/{next_dashboard.id}"
            self.workspace.title = next_dashboard.name
            self.workspace.file_path = None
            self.workspace.glance_url = None
            self.workspace.metadata = {**(self.workspace.metadata or {}), "active_dashboard_id": next_dashboard.id, "active_dashboard_layout": next_dashboard.layout}
        else:
            self.active_dashboard_id = None
            self.workspace.mode = "browser"
            self.workspace.url = "about:blank"
            self.workspace.title = "Blank workspace"
            self.workspace.file_path = None
            self.workspace.glance_url = None
            self.workspace.metadata = {k: v for k, v in (self.workspace.metadata or {}).items() if not str(k).startswith("active_dashboard")}
        payload = {"dashboard": removed, "dashboard_id": dashboard.id, "next_dashboard": asdict(next_dashboard) if next_dashboard else None, "workspace": asdict(self.workspace), "source": source}
        self.emit("dashboard_removed", payload)
        self._record_dashboard_note(
            f"Dashboard '{dashboard.name}' was closed by {source}.",
            {"event": "dashboard_removed", "dashboard_id": dashboard.id, "source": source, "next_dashboard_id": next_dashboard.id if next_dashboard else None},
        )
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


    def approval_requests_list(self, include_done: bool = False) -> List[Dict[str, Any]]:
        rows = list(self.approvals or [])
        if not include_done:
            rows = [r for r in rows if str(r.get("status") or "pending") == "pending"]
        return sorted(rows, key=lambda r: float(r.get("created_at") or 0), reverse=True)

    def create_approval_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(data or {})
        request_id = str(payload.get("id") or payload.get("request_id") or self._id("approval"))
        payload["id"] = request_id
        payload.setdefault("request_id", request_id)
        payload.setdefault("kind", payload.get("action") or "unknown")
        payload.setdefault("action", payload.get("kind") or "unknown")
        payload.setdefault("status", "pending")
        payload.setdefault("created_at", now_ts())
        payload.setdefault("requested_at_iso", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(payload.get("requested_at") or time.time()))))
        payload.setdefault("decision", None)
        self.approvals = [r for r in self.approvals if r.get("id") != request_id]
        self.approvals.insert(0, payload)
        if len(self.approvals) > 200:
            self.approvals = self.approvals[:200]
        self.emit("approval_requested", {"request": payload})
        return payload

    def get_approval_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        request_id = str(request_id or "")
        for req in self.approvals:
            if str(req.get("id") or req.get("request_id")) == request_id:
                return req
        return None

    def decide_approval_request(self, request_id: str, decision: Dict[str, Any]) -> Dict[str, Any]:
        req = self.get_approval_request(request_id)
        if req is None:
            raise KeyError(request_id)
        approved = bool((decision or {}).get("approved"))
        status = str((decision or {}).get("status") or ("approved" if approved else "denied")).lower()
        if status not in {"approved", "denied", "timeout"}:
            status = "approved" if approved else "denied"
        decision_payload = {
            "request_id": request_id,
            "kind": (decision or {}).get("kind") or req.get("kind"),
            "approved": status == "approved" or approved,
            "approve_for_session": bool((decision or {}).get("approve_for_session")),
            "reason": (decision or {}).get("reason") or (decision or {}).get("feedback"),
            "feedback": (decision or {}).get("feedback") or (decision or {}).get("reason"),
            "source": (decision or {}).get("source") or "wolf_gui",
            "status": status,
            "decided_at": now_ts(),
            "decided_by": (decision or {}).get("decided_by") or "gui_user",
        }
        req["status"] = status
        req["decision"] = decision_payload
        req["decided_at"] = decision_payload["decided_at"]
        self.emit("approval_decided", {"request": req, "decision": decision_payload})
        return req

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

    def set_chat_scale(self, *, scale: Any, source: str = "agent") -> Dict[str, Any]:
        return self.runtime.set_chat_scale(scale=scale, source=source)

    def adjust_chat_scale(self, *, delta: Any, source: str = "agent") -> Dict[str, Any]:
        return self.runtime.adjust_chat_scale(delta=delta, source=source)

    def reset_chat_scale(self, *, source: str = "agent") -> Dict[str, Any]:
        return self.runtime.reset_chat_scale(source=source)

    def create_dashboard(self, **kwargs: Any) -> Dict[str, Any]:
        return self.runtime.create_dashboard(kwargs)

    def add_dashboard_panel(self, **kwargs: Any) -> Dict[str, Any]:
        return self.runtime.add_dashboard_panel(kwargs)

    def update_dashboard_panel(self, *, panel_id: str, **kwargs: Any) -> Dict[str, Any]:
        return self.runtime.update_dashboard_panel(panel_id, kwargs)

    def open_dashboard(self, *, dashboard_id: Optional[str] = None) -> Dict[str, Any]:
        return self.runtime.open_dashboard(dashboard_id=dashboard_id)

    def set_dashboard_panel_zoom(self, *, panel_id: str, zoom: Any, source: str = "agent") -> Dict[str, Any]:
        return self.runtime.set_dashboard_panel_zoom(panel_id=panel_id, zoom=zoom, source=source)

    def adjust_dashboard_panel_zoom(self, *, panel_id: str, delta: Any, source: str = "agent") -> Dict[str, Any]:
        return self.runtime.adjust_dashboard_panel_zoom(panel_id=panel_id, delta=delta, source=source)

    def reset_dashboard_panel_zoom(self, *, panel_id: str, source: str = "agent") -> Dict[str, Any]:
        return self.runtime.reset_dashboard_panel_zoom(panel_id=panel_id, source=source)

    def remove_dashboard_panel(self, *, panel_id: str, source: str = "agent") -> Dict[str, Any]:
        return self.runtime.remove_dashboard_panel(panel_id=panel_id, source=source)

    def remove_dashboard(self, *, dashboard_id: Optional[str] = None, source: str = "agent") -> Dict[str, Any]:
        return self.runtime.remove_dashboard(dashboard_id=dashboard_id, source=source)

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

    def set_chat_scale(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.set_chat_scale(**kwargs)

    def adjust_chat_scale(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.adjust_chat_scale(**kwargs)

    def reset_chat_scale(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.reset_chat_scale(**kwargs)

    def create_dashboard(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.create_dashboard(**kwargs)

    def add_panel(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.add_dashboard_panel(**kwargs)

    def update_panel(self, *, panel_id: str, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.update_dashboard_panel(panel_id=panel_id, **kwargs)

    def open_dashboard(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.open_dashboard(**kwargs)

    def set_panel_zoom(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.set_dashboard_panel_zoom(**kwargs)

    def adjust_panel_zoom(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.adjust_dashboard_panel_zoom(**kwargs)

    def reset_panel_zoom(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.reset_dashboard_panel_zoom(**kwargs)

    def close_panel(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.remove_dashboard_panel(**kwargs)

    def close_dashboard(self, **kwargs: Any) -> Dict[str, Any]:
        return self.controller.remove_dashboard(**kwargs)

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
                open_after_add=False,
                **{k: v for k, v in panel.items() if k not in {"source", "universe", "created_by", "session_id", "workflow", "host_status", "open_after_add", "open", "auto_open"}},
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
