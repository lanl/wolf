from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urljoin

import requests
from pydantic import BaseModel, Field

from framework.workflows.base_agent_action import AgentAction
from framework.universes.endpoint_resolver import get_universe_base_url_or_error
from framework.workflows.agent_actions.gui_actions import _post_gui


DEFAULT_TIMEOUT = 30


def _append_result(infra: Any, action: str, universe: str, base_url: str, result: Any) -> None:
    try:
        infra.append_chat_history(
            actor="system",
            content=f"[Universe Apps][{action}] universe={universe} url={base_url}:\n{json.dumps(result, indent=2, sort_keys=True)}",
            action={"action": action},
            log_console=True,
        )
    except Exception:
        pass


def _base_url(infra: Any, universe: str) -> tuple[str, Optional[str]]:
    return get_universe_base_url_or_error(infra, universe)[:2]


def _absolute_app_url(base_url: str, app: Dict[str, Any]) -> str:
    url = str(app.get("url") or "")
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin(base_url.rstrip("/") + "/", url.lstrip("/"))


class UniverseAppBaseArgs(BaseModel):
    system: str = Field(default="local", description="System where the Universe is registered")
    universe: str = Field(..., description="Name of the Universe")


class UniverseAppIdArgs(UniverseAppBaseArgs):
    app_id: str = Field(..., description="Universe app id")


class UniverseAppRegisterArgs(UniverseAppBaseArgs):
    app_id: Optional[str] = Field(default=None, description="Stable app id; derived from title if omitted")
    title: str = Field(default="Universe App", description="App title")
    kind: str = Field(default="custom", description="App kind, e.g. plot, table, mesh, cad, trame_mesh, report")
    backend: str = Field(default="url", description="App backend/type, e.g. url, static, process, trame, proxy")
    status: str = Field(default="registered", description="Initial app status")
    url: str = Field(..., description="App URL, absolute or relative to the Universe base URL")
    description: str = ""
    artifacts: List[str] = Field(default_factory=list)
    preferred_panel: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    command_args: Optional[List[str]] = Field(default=None, description="Managed-process argv executed with shell=False")
    entrypoint: Optional[str] = Field(default=None, description="Compatibility command string split with shlex by the Universe")
    cwd: Optional[str] = Field(default=None, description="Optional managed-process working directory")
    env: Dict[str, str] = Field(default_factory=dict, description="Optional managed-process environment overrides")
    internal_port: Optional[int] = Field(default=None, description="Internal managed app port if known")
    static_dir: Optional[str] = Field(default=None, description="Static app directory to serve from the Universe")
    index_file: str = Field(default="index.html", description="Static app index file")
    proxy_url: Optional[str] = Field(default=None, description="Base URL for opt-in Universe HTTP proxy")
    websocket_url: Optional[str] = Field(default=None, description="Optional ws:// or wss:// base URL for opt-in WebSocket proxy")
    allow_proxy: bool = Field(default=False, description="Explicit opt-in to proxy app HTTP requests")


class UniverseAppLogsArgs(UniverseAppIdArgs):
    tail: int = Field(default=200, description="Number of log lines to return")


class UniverseAppGuiArgs(UniverseAppIdArgs):
    gui_url: Optional[str] = Field(default=None, description="Optional WOLF GUI URL")
    open: bool = Field(default=True, description="Open the app/dashboard after registering/adding")


class UniverseAppDashboardArgs(UniverseAppGuiArgs):
    dashboard_id: Optional[str] = Field(default=None, description="Target dashboard id; omit to use/create active dashboard")
    dashboard_name: str = Field(default="Universe Apps", description="Dashboard name if one must be created")
    title: Optional[str] = Field(default=None, description="Panel title override")


class UniverseAppListAction(AgentAction):
    action: Literal["universe_app_list"] = "universe_app_list"
    description: Literal["List webapps registered with a Universe"] = "List webapps registered with a Universe"
    payload: UniverseAppBaseArgs
    payload_schema: str = '{"system": "local", "universe": "example_universe"}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        base, err = _base_url(infra, self.payload.universe)
        if err:
            result = {"ok": False, "error": err}
        else:
            try:
                r = requests.get(f"{base}/apps", timeout=DEFAULT_TIMEOUT)
                r.raise_for_status()
                result = r.json()
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _append_result(infra, self.action, self.payload.universe, base or "<unresolved>", result)
        return result


class UniverseAppRegisterAction(AgentAction):
    action: Literal["universe_app_register"] = "universe_app_register"
    description: Literal["Register a URL/webapp manifest with a Universe"] = "Register a URL/webapp manifest with a Universe"
    payload: UniverseAppRegisterArgs
    payload_schema: str = '{"system": "local", "universe": "u", "title": "Mesh Viewer", "kind": "trame_mesh", "url": "/apps/mesh_viewer", "allow_proxy": true, "websocket_url": "ws://127.0.0.1:9000"}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        base, err = _base_url(infra, self.payload.universe)
        data = self.payload.model_dump(exclude_none=True)
        data.pop("system", None); data.pop("universe", None)
        if err:
            result = {"ok": False, "error": err}
        else:
            try:
                r = requests.post(f"{base}/apps/register", json=data, timeout=DEFAULT_TIMEOUT)
                r.raise_for_status()
                result = r.json()
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _append_result(infra, self.action, self.payload.universe, base or "<unresolved>", result)
        return result


class _UniverseAppLifecycleMixin:
    payload: UniverseAppIdArgs

    def _verb(self) -> str:
        return str(self.action).replace("universe_app_", "")

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        base, err = _base_url(infra, self.payload.universe)
        if err:
            result = {"ok": False, "error": err}
        else:
            try:
                method = requests.delete if self._verb() == "delete" else requests.post
                r = method(f"{base}/apps/{self.payload.app_id}" + ("" if self._verb() == "delete" else f"/{self._verb()}"), timeout=DEFAULT_TIMEOUT)
                r.raise_for_status()
                result = r.json()
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _append_result(infra, self.action, self.payload.universe, base or "<unresolved>", result)
        return result


class UniverseAppStartAction(_UniverseAppLifecycleMixin, AgentAction):
    action: Literal["universe_app_start"] = "universe_app_start"
    description: Literal["Start or mark running a Universe app"] = "Start or mark running a Universe app"
    payload: UniverseAppIdArgs
    payload_schema: str = '{"system": "local", "universe": "u", "app_id": "mesh_viewer"}'


class UniverseAppStopAction(_UniverseAppLifecycleMixin, AgentAction):
    action: Literal["universe_app_stop"] = "universe_app_stop"
    description: Literal["Stop or mark stopped a Universe app"] = "Stop or mark stopped a Universe app"
    payload: UniverseAppIdArgs
    payload_schema: str = '{"system": "local", "universe": "u", "app_id": "mesh_viewer"}'


class UniverseAppRestartAction(_UniverseAppLifecycleMixin, AgentAction):
    action: Literal["universe_app_restart"] = "universe_app_restart"
    description: Literal["Restart a Universe app"] = "Restart a Universe app"
    payload: UniverseAppIdArgs
    payload_schema: str = '{"system": "local", "universe": "u", "app_id": "mesh_viewer"}'


class UniverseAppDeleteAction(_UniverseAppLifecycleMixin, AgentAction):
    action: Literal["universe_app_delete"] = "universe_app_delete"
    description: Literal["Delete/unregister a Universe app"] = "Delete/unregister a Universe app"
    payload: UniverseAppIdArgs
    payload_schema: str = '{"system": "local", "universe": "u", "app_id": "mesh_viewer"}'


class UniverseAppLogsAction(AgentAction):
    action: Literal["universe_app_logs"] = "universe_app_logs"
    description: Literal["Fetch logs for a Universe app"] = "Fetch logs for a Universe app"
    payload: UniverseAppLogsArgs
    payload_schema: str = '{"system": "local", "universe": "u", "app_id": "mesh_viewer", "tail": 200}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        base, err = _base_url(infra, self.payload.universe)
        if err:
            result = {"ok": False, "error": err}
        else:
            try:
                r = requests.get(f"{base}/apps/{self.payload.app_id}/logs", params={"tail": self.payload.tail}, timeout=DEFAULT_TIMEOUT)
                r.raise_for_status()
                result = r.json()
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _append_result(infra, self.action, self.payload.universe, base or "<unresolved>", result)
        return result


def _fetch_manifest(infra: Any, universe: str, app_id: str) -> tuple[str, Dict[str, Any]]:
    base, err = _base_url(infra, universe)
    if err:
        return base or "", {"ok": False, "error": err}
    r = requests.get(f"{base}/apps/{app_id}/manifest", timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    app = data.get("app") if isinstance(data, dict) and "app" in data else data
    return base, app if isinstance(app, dict) else {"ok": False, "error": "Invalid app manifest response"}


class UniverseAppOpenInGuiAction(AgentAction):
    action: Literal["universe_app_open_in_gui"] = "universe_app_open_in_gui"
    description: Literal["Register and open a Universe app in the WOLF GUI workspace"] = "Register and open a Universe app in the WOLF GUI workspace"
    payload: UniverseAppGuiArgs
    payload_schema: str = '{"system": "local", "universe": "u", "app_id": "mesh_viewer", "open": true}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        try:
            base, app = _fetch_manifest(infra, self.payload.universe, self.payload.app_id)
            if app.get("ok") is False:
                result = app
            else:
                url = _absolute_app_url(base, app)
                result = _post_gui("/api/gui/apps/register", {
                    "name": app.get("title") or app.get("app_id"),
                    "url": url,
                    "kind": app.get("kind") or "custom",
                    "source": "universe",
                    "universe": self.payload.universe,
                    "description": app.get("description") or "",
                    "metadata": {"universe_app": app, "universe_base_url": base},
                    "host_status": app.get("status") or "unknown",
                    "open": self.payload.open,
                }, self.payload.gui_url)
        except Exception as exc:
            base = "<unresolved>"
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _append_result(infra, self.action, self.payload.universe, base, result)
        return result


class UniverseAppAddToDashboardAction(AgentAction):
    action: Literal["universe_app_add_to_dashboard"] = "universe_app_add_to_dashboard"
    description: Literal["Add a Universe app URL as a panel in a WOLF GUI dashboard"] = "Add a Universe app URL as a panel in a WOLF GUI dashboard"
    payload: UniverseAppDashboardArgs
    payload_schema: str = '{"system": "local", "universe": "u", "app_id": "mesh_viewer", "dashboard_name": "Results", "open": true}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        try:
            base, app = _fetch_manifest(infra, self.payload.universe, self.payload.app_id)
            if app.get("ok") is False:
                result = app
            else:
                url = _absolute_app_url(base, app)
                result = _post_gui("/api/gui/dashboards/add_panel", {
                    "dashboard_id": self.payload.dashboard_id,
                    "dashboard_name": self.payload.dashboard_name,
                    "title": self.payload.title or app.get("title") or app.get("app_id"),
                    "name": app.get("app_id") or self.payload.app_id,
                    "kind": "url",
                    "url": url,
                    "source": "universe",
                    "universe": self.payload.universe,
                    "status": app.get("status") or "unknown",
                    "metadata": {"universe_app": app, "universe_base_url": base},
                    "host_status": app.get("status") or "unknown",
                    "open": self.payload.open,
                }, self.payload.gui_url)
        except Exception as exc:
            base = "<unresolved>"
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _append_result(infra, self.action, self.payload.universe, base, result)
        return result
