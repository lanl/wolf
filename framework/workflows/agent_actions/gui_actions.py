from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from framework.workflows.base_agent_action import AgentAction
from framework.workflows.agent_actions.formatting_utils import coerce_bool, normalize_text_payload_dict, resolve_text_source


DEFAULT_GUI_URL = "http://127.0.0.1:8765"
CONTROL_TOKEN_HEADER = "X-Wolf-Gui-Token"


def _normalize_gui_bool_fields(data: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        if name in data:
            data[name] = coerce_bool(data[name])
    return data


def _normalize_panel_dict(panel: dict[str, Any]) -> dict[str, Any]:
    out = dict(panel)
    _normalize_gui_bool_fields(out, "open")
    return normalize_text_payload_dict(out, target="content_html", required=False)


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
    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "Agent Dashboard", "layout": "grid", "description": "...", "open": False}]})

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

    @model_validator(mode="before")
    @classmethod
    def normalize_bools(cls, data: Any):
        return _normalize_gui_bool_fields(dict(data), "open") if isinstance(data, dict) else data


class GuiDashboardPanelPayload(GuiBasePayload):
    model_config = ConfigDict(json_schema_extra={"examples": [{"title": "Panel", "kind": "html", "content_html_lines": ["<h1>Hello</h1>"], "open": True}]})

    id: Optional[str] = None
    dashboard_id: Optional[str] = Field(default=None, description="Target dashboard id. If omitted, the active/latest dashboard is used or a new one is created")
    dashboard_name: Optional[str] = Field(default=None, description="Name to use if a dashboard must be created implicitly")
    name: str = Field(default="Dashboard Panel", description="Panel internal name")
    title: Optional[str] = Field(default=None, description="Panel title shown in the dashboard")
    kind: str = Field(default="html", description="Panel kind, e.g. html, url, app, report, chart, log")
    url: str = Field(default="about:blank", description="Panel iframe URL when content_html is not supplied")
    content_html: Optional[str] = Field(default=None, description="Inline HTML content for the panel")
    content_html_lines: Optional[List[str]] = Field(default=None, description="Safer multiline HTML transport; joined with newline characters")
    content_html_base64: Optional[str] = Field(default=None, description="Base64 encoded UTF-8 HTML content")
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

    @model_validator(mode="before")
    @classmethod
    def normalize_bools(cls, data: Any):
        return _normalize_gui_bool_fields(dict(data), "open") if isinstance(data, dict) else data

    @model_validator(mode="after")
    def normalize_content_html_transport(self):
        self.content_html = resolve_text_source(
            text=self.content_html,
            lines=self.content_html_lines,
            base64_text=self.content_html_base64,
            field_label="content_html",
            required=False,
        )
        self.content_html_lines = None
        self.content_html_base64 = None
        return self


class GuiUpdateDashboardPanelPayload(GuiBasePayload):
    model_config = ConfigDict(json_schema_extra={"examples": [{"panel_id": "panel_...", "content_html_lines": ["<h1>Updated</h1>"], "status": "ready", "open": False}]})

    panel_id: str = Field(..., description="Panel id to update")
    name: Optional[str] = None
    title: Optional[str] = None
    kind: Optional[str] = None
    url: Optional[str] = None
    content_html: Optional[str] = None
    content_html_lines: Optional[List[str]] = None
    content_html_base64: Optional[str] = None
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

    @model_validator(mode="before")
    @classmethod
    def normalize_bools(cls, data: Any):
        return _normalize_gui_bool_fields(dict(data), "open") if isinstance(data, dict) else data

    @model_validator(mode="after")
    def normalize_content_html_transport(self):
        self.content_html = resolve_text_source(
            text=self.content_html,
            lines=self.content_html_lines,
            base64_text=self.content_html_base64,
            field_label="content_html",
            required=False,
        )
        self.content_html_lines = None
        self.content_html_base64 = None
        return self


class GuiOpenDashboardPayload(GuiBasePayload):
    dashboard_id: Optional[str] = Field(default=None, description="Dashboard id to open. If omitted, open the active/latest dashboard")


class GuiDashboardPanelZoomPayload(GuiBasePayload):
    panel_id: str = Field(..., description="Dashboard panel id to zoom")
    zoom: Optional[float] = Field(default=None, description="Absolute zoom value, e.g. 1.0 for 100%, 1.25 for 125%")
    delta: Optional[float] = Field(default=None, description="Relative zoom delta, e.g. 0.1 to zoom in or -0.1 to zoom out")
    source: str = Field(default="agent", description="Source label recorded in GUI chat history")


class GuiRemoveDashboardPanelPayload(GuiBasePayload):
    panel_id: str = Field(..., description="Dashboard panel id to close/remove")
    source: str = Field(default="agent", description="Source label recorded in GUI chat history")


class GuiRemoveDashboardPayload(GuiBasePayload):
    dashboard_id: Optional[str] = Field(default=None, description="Dashboard id to close/remove. If omitted, close the active/latest dashboard")
    source: str = Field(default="agent", description="Source label recorded in GUI chat history")


class GuiPublishDashboardPayload(GuiBasePayload):
    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "Dashboard", "panels": [{"title": "Panel", "kind": "html", "content_html_lines": ["<h1>Hello</h1>"]}], "open": True}]})

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

    @model_validator(mode="before")
    @classmethod
    def normalize_panels_and_bools(cls, data: Any):
        if isinstance(data, dict):
            data = _normalize_gui_bool_fields(dict(data), "open")
            if isinstance(data.get("panels"), list):
                data["panels"] = [_normalize_panel_dict(p) if isinstance(p, dict) else p for p in data["panels"]]
        return data


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

    @model_validator(mode="before")
    @classmethod
    def normalize_bools(cls, data: Any):
        return _normalize_gui_bool_fields(dict(data), "open") if isinstance(data, dict) else data


class GuiOpenAppPayload(GuiBasePayload):
    app_id: Optional[str] = Field(default=None, description="Registered app id to open")
    url: Optional[str] = Field(default=None, description="URL to open directly if app_id is not supplied")


class GuiGetVisualContextPayload(GuiBasePayload):
    @model_validator(mode="before")
    @classmethod
    def normalize_bools(cls, data: Any):
        return _normalize_gui_bool_fields(dict(data), "include_dom_excerpt", "include_layout", "include_annotations") if isinstance(data, dict) else data

    include_dom_excerpt: bool = Field(default=True, description="Include same-origin/inline DOM or text excerpts when available")
    include_layout: bool = Field(default=True, description="Include viewport, panel bounds, and visible-surface layout metadata")
    include_annotations: bool = Field(default=True, description="Include current workspace annotations")


class GuiSetChatScaleAction(AgentAction):
    action: Literal["gui_set_chat_scale"] = "gui_set_chat_scale"
    description: Literal["Set the Agent Chat panel content/readability size"] = "Set the Agent Chat panel content/readability size"
    payload: GuiChatScalePayload
    payload_schema: str = '{"scale": 1.15}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        data.pop("delta", None)
        result = _post_gui("/api/gui/chat_scale", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiAdjustChatScaleAction(AgentAction):
    action: Literal["gui_adjust_chat_scale"] = "gui_adjust_chat_scale"
    description: Literal["Increase or decrease the Agent Chat panel content/readability size"] = "Increase or decrease the Agent Chat panel content/readability size"
    payload: GuiChatScalePayload
    payload_schema: str = '{"delta": 0.1}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        if "delta" not in data:
            data["delta"] = 0.1
        data.pop("scale", None)
        result = _post_gui("/api/gui/chat_scale_delta", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiResetChatScaleAction(AgentAction):
    action: Literal["gui_reset_chat_scale"] = "gui_reset_chat_scale"
    description: Literal["Reset the Agent Chat panel content/readability size to 100%"] = "Reset the Agent Chat panel content/readability size to 100%"
    payload: GuiChatScalePayload
    payload_schema: str = '{}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        data.pop("scale", None)
        data.pop("delta", None)
        result = _post_gui("/api/gui/chat_scale_reset", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


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
    payload_schema: str = '{"dashboard_id": "optional", "title": "Panel", "kind": "html", "url": "about:blank", "content_html_lines": ["<html>...</html>"], "open": true}'

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
    payload_schema: str = '{"panel_id": "panel_...", "content_html_lines": ["<html>updated</html>"], "status": "ready", "open": false}'

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


class GuiSetDashboardPanelZoomAction(AgentAction):
    action: Literal["gui_set_dashboard_panel_zoom"] = "gui_set_dashboard_panel_zoom"
    description: Literal["Set the zoom level of a GUI dashboard panel"] = "Set the zoom level of a GUI dashboard panel"
    payload: GuiDashboardPanelZoomPayload
    payload_schema: str = '{"panel_id": "panel_...", "zoom": 1.25}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        data.pop("delta", None)
        result = _post_gui("/api/gui/dashboards/panel_zoom", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiAdjustDashboardPanelZoomAction(AgentAction):
    action: Literal["gui_adjust_dashboard_panel_zoom"] = "gui_adjust_dashboard_panel_zoom"
    description: Literal["Zoom a GUI dashboard panel in or out by a delta"] = "Zoom a GUI dashboard panel in or out by a delta"
    payload: GuiDashboardPanelZoomPayload
    payload_schema: str = '{"panel_id": "panel_...", "delta": 0.1}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        if "delta" not in data:
            data["delta"] = 0.1
        data.pop("zoom", None)
        result = _post_gui("/api/gui/dashboards/panel_zoom_delta", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiResetDashboardPanelZoomAction(AgentAction):
    action: Literal["gui_reset_dashboard_panel_zoom"] = "gui_reset_dashboard_panel_zoom"
    description: Literal["Reset a GUI dashboard panel zoom level to 100%"] = "Reset a GUI dashboard panel zoom level to 100%"
    payload: GuiDashboardPanelZoomPayload
    payload_schema: str = '{"panel_id": "panel_..."}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        data.pop("zoom", None)
        data.pop("delta", None)
        result = _post_gui("/api/gui/dashboards/panel_zoom_reset", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiRemoveDashboardPanelAction(AgentAction):
    action: Literal["gui_remove_dashboard_panel"] = "gui_remove_dashboard_panel"
    description: Literal["Close/remove a panel from a GUI dashboard"] = "Close/remove a panel from a GUI dashboard"
    payload: GuiRemoveDashboardPanelPayload
    payload_schema: str = '{"panel_id": "panel_..."}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        result = _post_gui("/api/gui/dashboards/remove_panel", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiRemoveDashboardAction(AgentAction):
    action: Literal["gui_remove_dashboard"] = "gui_remove_dashboard"
    description: Literal["Close/remove a GUI dashboard"] = "Close/remove a GUI dashboard"
    payload: GuiRemoveDashboardPayload
    payload_schema: str = '{"dashboard_id": "optional dashboard id"}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        gui_url = data.pop("gui_url", None)
        result = _post_gui("/api/gui/dashboards/remove", data, gui_url)
        _append_gui_result(infra, self.action, result)
        return result


class GuiPublishDashboardAction(AgentAction):
    action: Literal["gui_publish_dashboard"] = "gui_publish_dashboard"
    description: Literal["Create, populate, and optionally open a GUI dashboard in one action"] = "Create, populate, and optionally open a GUI dashboard in one action"
    payload: GuiPublishDashboardPayload
    payload_schema: str = '{"name": "Dashboard", "panels": [{"title": "Panel", "kind": "html", "content_html_lines": ["<html>...</html>"]}], "open": true}'

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
