from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from framework.workflows.base_agent_action import AgentAction


DEFAULT_GUI_URL = "http://127.0.0.1:8765"
CONTROL_TOKEN_HEADER = "X-Wolf-Gui-Token"


def _gui_base_url(explicit: Optional[str] = None) -> str:
    return str(explicit or os.environ.get("WOLF_GUI_URL") or DEFAULT_GUI_URL).strip().rstrip("/")


def _gui_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("WOLF_GUI_CONTROL_TOKEN", "").strip()
    if token:
        headers[CONTROL_TOKEN_HEADER] = token
    return headers


def _post_gui(endpoint: str, payload: Dict[str, Any], gui_url: Optional[str] = None) -> Dict[str, Any]:
    url = f"{_gui_base_url(gui_url)}{endpoint}"
    data = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=_gui_headers(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GUI API HTTP {exc.code} for {endpoint}: {raw}") from exc
    except Exception as exc:
        raise RuntimeError(f"GUI API request failed for {endpoint} at {url}: {exc}") from exc


def _append_gui_result(infra: Any, action_name: str, result: Any) -> None:
    if infra is None:
        return
    try:
        infra.append_chat_history(
            actor="system",
            content=f"[GUI] {action_name} result: {json.dumps(result, indent=2, sort_keys=True)}",
            action={"action": "system_info"},
            log_console=True,
        )
    except Exception:
        pass


class GuiBasePayload(BaseModel):
    gui_url: Optional[str] = Field(default=None, description="Optional GUI server base URL. Defaults to WOLF_GUI_URL or http://127.0.0.1:8765")


class GuiNotifyPayload(GuiBasePayload):
    message: str = Field(..., description="User-visible status message to show in the GUI")
    level: str = Field(default="info", description="Status level such as info, warning, or error")
    source: str = Field(default="agent", description="Source label for provenance")


class GuiCreateDashboardPayload(GuiBasePayload):
    id: Optional[str] = None
    name: str = Field(default="Agent Dashboard", description="Dashboard display name")
    layout: str = Field(default="grid", description="Dashboard layout, usually grid")
    description: str = ""
    source: str = "agent"
    universe: Optional[str] = None
    created_by: str = "agent"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None
    workflow: Optional[str] = None
    host_status: str = "unknown"
    open: bool = Field(default=False, description="If true, open the dashboard after creating it")


class GuiDashboardPanelPayload(GuiBasePayload):
    id: Optional[str] = None
    dashboard_id: Optional[str] = Field(default=None, description="Target dashboard id. If omitted, the active/latest dashboard is used or a new one is created")
    dashboard_name: Optional[str] = Field(default=None, description="Name to use if a dashboard must be created implicitly")
    name: str = Field(default="Dashboard Panel", description="Panel internal name")
    title: Optional[str] = Field(default=None, description="Panel title shown in the dashboard")
    kind: str = Field(default="html", description="Panel kind, e.g. html, url, app, report, chart, log")
    url: str = Field(default="about:blank", description="Panel iframe URL when content_html is not supplied")
    content_html: Optional[str] = Field(default=None, description="Inline HTML content for the panel")
    layout: Dict[str, Any] = Field(default_factory=dict, description="Optional panel layout hints")
    source: str = "agent"
    universe: Optional[str] = None
    created_by: str = "agent"
    status: str = "ready"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None
    workflow: Optional[str] = None
    host_status: str = "unknown"
    open: bool = Field(default=False, description="If true, open the dashboard after adding the panel")


class GuiUpdateDashboardPanelPayload(GuiBasePayload):
    panel_id: str = Field(..., description="Panel id to update")
    name: Optional[str] = None
    title: Optional[str] = None
    kind: Optional[str] = None
    url: Optional[str] = None
    content_html: Optional[str] = None
    layout: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    source: Optional[str] = None
    universe: Optional[str] = None
    created_by: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    workflow: Optional[str] = None
    host_status: Optional[str] = None
    open: bool = Field(default=False, description="If true, open the dashboard after updating the panel")


class GuiOpenDashboardPayload(GuiBasePayload):
    dashboard_id: Optional[str] = Field(default=None, description="Dashboard id to open. If omitted, open the active/latest dashboard")


class GuiPublishDashboardPayload(GuiBasePayload):
    name: str = Field(default="Agent Dashboard", description="Dashboard display name")
    panels: List[Dict[str, Any]] = Field(default_factory=list, description="Panels to add to the dashboard")
    layout: str = "grid"
    description: str = ""
    source: str = "agent"
    universe: Optional[str] = None
    created_by: str = "agent"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None
    workflow: Optional[str] = None
    host_status: str = "unknown"
    open: bool = Field(default=True, description="Open dashboard after publishing")


class GuiRegisterAppPayload(GuiBasePayload):
    name: str = Field(..., description="App display name")
    url: str = Field(..., description="App URL to open in the workspace")
    kind: str = "custom"
    source: str = "agent"
    universe: Optional[str] = None
    created_by: str = "agent"
    description: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None
    workflow: Optional[str] = None
    host_status: str = "unknown"
    open: bool = Field(default=False, description="If true, open the app after registering")


class GuiOpenAppPayload(GuiBasePayload):
    app_id: Optional[str] = Field(default=None, description="Registered app id to open")
    url: Optional[str] = Field(default=None, description="URL to open directly if app_id is not supplied")


class GuiGetVisualContextPayload(GuiBasePayload):
    include_dom_excerpt: bool = Field(default=True, description="Include same-origin/inline DOM or text excerpts when available")
    include_layout: bool = Field(default=True, description="Include viewport, panel bounds, and visible-surface layout metadata")
    include_annotations: bool = Field(default=True, description="Include current workspace annotations")


class GuiGetVisualContextAction(AgentAction):
    action: Literal["gui_get_visual_context"] = "gui_get_visual_context"
    description: Literal["Inspect the current Wolf GUI visual workspace context when the user has enabled agent inspection"] = "Inspect the current Wolf GUI visual workspace context when the user has enabled agent inspection"
    payload: GuiGetVisualContextPayload = Field(default_factory=GuiGetVisualContextPayload)
    payload_schema: str = '{"include_dom_excerpt": true, "include_layout": true, "include_annotations": true}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        result = {
            "ok": False,
            "requires_gui_client": True,
            "route": "client_event",
            "message": "gui_get_visual_context must be executed by the connected Wolf GUI browser client so user permission and live workspace state are honored.",
        }
        _append_gui_result(infra, self.action, result)
        return result


class GuiNotifyAction(AgentAction):
    action: Literal["gui_notify"] = "gui_notify"
    description: Literal["Show a status notification in the GUI workspace"] = "Show a status notification in the GUI workspace"
    payload: GuiNotifyPayload
    payload_schema: str = '{"message": "status text", "level": "info", "source": "agent"}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        result = _post_gui("/api/gui/control", {"command": "notify", "args": data}, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiCreateDashboardAction(AgentAction):
    action: Literal["gui_create_dashboard"] = "gui_create_dashboard"
    description: Literal["Create a dashboard in the GUI workspace"] = "Create a dashboard in the GUI workspace"
    payload: GuiCreateDashboardPayload
    payload_schema: str = '{"name": "Dashboard name", "layout": "grid", "description": "...", "open": false}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        open_after = bool(data.pop("open", False))
        result = _post_gui("/api/gui/dashboards/create", data, gui_url)
        if open_after and result.get("dashboard", {}).get("id"):
            result["opened"] = _post_gui("/api/gui/dashboards/open", {"dashboard_id": result["dashboard"]["id"]}, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiAddDashboardPanelAction(AgentAction):
    action: Literal["gui_add_dashboard_panel"] = "gui_add_dashboard_panel"
    description: Literal["Add a panel to a GUI dashboard"] = "Add a panel to a GUI dashboard"
    payload: GuiDashboardPanelPayload
    payload_schema: str = '{"dashboard_id": "optional", "title": "Panel", "kind": "html", "url": "about:blank", "content_html": "<html>...</html>", "open": true}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        open_after = bool(data.pop("open", False))
        result = _post_gui("/api/gui/dashboards/add_panel", data, gui_url)
        if open_after:
            dash_id = data.get("dashboard_id") or result.get("panel", {}).get("dashboard_id")
            result["opened"] = _post_gui("/api/gui/dashboards/open", {"dashboard_id": dash_id}, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiUpdateDashboardPanelAction(AgentAction):
    action: Literal["gui_update_dashboard_panel"] = "gui_update_dashboard_panel"
    description: Literal["Update an existing GUI dashboard panel"] = "Update an existing GUI dashboard panel"
    payload: GuiUpdateDashboardPanelPayload
    payload_schema: str = '{"panel_id": "panel_...", "content_html": "<html>updated</html>", "status": "ready", "open": false}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        open_after = bool(data.pop("open", False))
        result = _post_gui("/api/gui/dashboards/update_panel", data, gui_url)
        if open_after:
            dash_id = result.get("panel", {}).get("dashboard_id")
            result["opened"] = _post_gui("/api/gui/dashboards/open", {"dashboard_id": dash_id}, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiOpenDashboardAction(AgentAction):
    action: Literal["gui_open_dashboard"] = "gui_open_dashboard"
    description: Literal["Open a dashboard in the GUI workspace"] = "Open a dashboard in the GUI workspace"
    payload: GuiOpenDashboardPayload
    payload_schema: str = '{"dashboard_id": "optional dashboard id"}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        result = _post_gui("/api/gui/dashboards/open", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiPublishDashboardAction(AgentAction):
    action: Literal["gui_publish_dashboard"] = "gui_publish_dashboard"
    description: Literal["Create, populate, and optionally open a GUI dashboard in one action"] = "Create, populate, and optionally open a GUI dashboard in one action"
    payload: GuiPublishDashboardPayload
    payload_schema: str = '{"name": "Dashboard", "panels": [{"title": "Panel", "kind": "html", "content_html": "<html>...</html>"}], "open": true}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        result = _post_gui("/api/gui/dashboards/publish", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiRegisterAppAction(AgentAction):
    action: Literal["gui_register_app"] = "gui_register_app"
    description: Literal["Register an app surface with the GUI workspace"] = "Register an app surface with the GUI workspace"
    payload: GuiRegisterAppPayload
    payload_schema: str = '{"name": "App", "url": "http://...", "kind": "custom", "open": false}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        open_after = bool(data.pop("open", False))
        result = _post_gui("/api/gui/apps/register", data, gui_url)
        if open_after and result.get("app", {}).get("id"):
            result["opened"] = _post_gui("/api/gui/workspace/open_app", {"app_id": result["app"]["id"]}, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiOpenAppAction(AgentAction):
    action: Literal["gui_open_app"] = "gui_open_app"
    description: Literal["Open a registered app or URL in the GUI workspace"] = "Open a registered app or URL in the GUI workspace"
    payload: GuiOpenAppPayload
    payload_schema: str = '{"app_id": "optional app id", "url": "optional URL"}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        result = _post_gui("/api/gui/workspace/open_app", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result
