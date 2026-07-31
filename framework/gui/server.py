from __future__ import annotations

import json
import os
import mimetypes
import threading
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from framework.gui.runtime import GuiRuntime

STATIC_DIR = Path(__file__).with_name("static")
CONTROL_TOKEN_HEADER = "X-Wolf-Gui-Token"


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, indent=2, sort_keys=True).encode("utf-8")


class GuiRequestHandler(BaseHTTPRequestHandler):
    runtime: GuiRuntime

    server_version = "WolfGui/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[wolf-gui] {self.address_string()} - {fmt % args}")

    def _send(self, status: int, body: bytes, content_type: str = "application/octet-stream", extra_headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if content_type == "application/json" else "public, max-age=60")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Wolf-Gui-Token")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, data: Any) -> None:
        self._send(status, _json_bytes(data), "application/json; charset=utf-8")

    def _check_control_token(self) -> bool:
        token = os.environ.get("WOLF_GUI_CONTROL_TOKEN", "").strip()
        if not token:
            return True
        presented = str(self.headers.get(CONTROL_TOKEN_HEADER) or "")
        return presented == token

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_OPTIONS(self) -> None:
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/gui/health":
                self._send_json(200, self.runtime.health())
                return
            if path == "/api/gui/bootstrap":
                self._send_json(200, self.runtime.bootstrap())
                return
            if path == "/api/gui/workflows":
                self._send_json(200, self.runtime.workflows())
                return
            if path == "/api/gui/sessions":
                self._send_json(200, self.runtime.sessions())
                return
            if path == "/api/gui/config":
                self._send_json(200, self.runtime.redacted_launch_config())
                return
            if path == "/api/gui/apps":
                self._send_json(200, self.runtime.apps_list())
                return
            if path == "/api/gui/dashboards":
                self._send_json(200, self.runtime.dashboards_list())
                return
            if path == "/api/gui/annotations":
                self._send_json(200, [a.__dict__ for a in self.runtime.annotations])
                return
            if path == "/api/gui/messages":
                self._send_json(200, [m.__dict__ for m in self.runtime.messages])
                return
            if path == "/api/gui/events":
                seq = int((parse_qs(parsed.query).get("since") or ["0"])[0] or 0)
                self._send_json(200, self.runtime.events_since(seq))
                return
            self._serve_static(path)
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if not self._check_control_token():
                self._send_json(401, {"ok": False, "error": "Unauthorized control token"})
                return
            body = self._read_json()
            if path == "/api/gui/workspace/open_url":
                self._send_json(200, {"ok": True, "workspace": self.runtime.open_url(str(body.get("url") or ""))})
                return
            if path == "/api/gui/workspace/open_glance":
                self._send_json(200, {"ok": True, "workspace": self.runtime.open_glance(url=body.get("url"), path=body.get("path"), name=body.get("name"))})
                return
            if path == "/api/gui/apps/register":
                self._send_json(200, {"ok": True, "app": self.runtime.register_app(body)})
                return
            if path == "/api/gui/workspace/open_app":
                self._send_json(200, {"ok": True, **self.runtime.open_app(app_id=body.get("app_id"), url=body.get("url"))})
                return
            if path == "/api/gui/annotations":
                self._send_json(200, {"ok": True, "annotation": self.runtime.add_annotation(body)})
                return
            if path == "/api/gui/annotations/clear":
                self._send_json(200, self.runtime.clear_annotations())
                return
            if path == "/api/gui/pointer_event":
                self._send_json(200, {"ok": True, "pointer": self.runtime.pointer_event(body)})
                return
            if path == "/api/gui/message":
                self._send_json(200, self.runtime.add_message(str(body.get("content") or ""), body.get("visual_context") or {}))
                return
            if path == "/api/gui/dashboards/create":
                self._send_json(200, {"ok": True, "dashboard": self.runtime.create_dashboard(body)})
                return
            if path == "/api/gui/dashboards/add_panel":
                self._send_json(200, {"ok": True, "panel": self.runtime.add_dashboard_panel(body)})
                return
            if path == "/api/gui/dashboards/update_panel":
                panel_id = str(body.get("panel_id") or "")
                self._send_json(200, {"ok": True, "panel": self.runtime.update_dashboard_panel(panel_id, body)})
                return
            if path == "/api/gui/dashboards/open":
                self._send_json(200, {"ok": True, **self.runtime.open_dashboard(dashboard_id=body.get("dashboard_id"))})
                return
            if path == "/api/gui/dashboards/publish":
                dashboard = self.runtime.create_dashboard(body)
                panels = []
                for panel in body.get("panels") or []:
                    panel_data = {
                        **panel,
                        "dashboard_id": dashboard.get("id"),
                        "source": panel.get("source") or body.get("source") or "agent",
                        "universe": panel.get("universe") or body.get("universe"),
                        "created_by": panel.get("created_by") or body.get("created_by") or "agent",
                        "session_id": panel.get("session_id") or body.get("session_id"),
                        "workflow": panel.get("workflow") or body.get("workflow"),
                        "host_status": panel.get("host_status") or body.get("host_status") or "unknown",
                    }
                    panels.append(self.runtime.add_dashboard_panel(panel_data))
                opened = self.runtime.open_dashboard(dashboard_id=dashboard.get("id")) if body.get("open", True) else None
                self.runtime.emit("agent_status", {"message": f"Agent opened dashboard: {dashboard.get('name')}", "level": "info", "source": body.get("source") or "agent"})
                self._send_json(200, {"ok": True, "dashboard": dashboard, "panels": panels, "opened": opened})
                return
            if path == "/api/gui/control":
                cmd = str(body.get("command") or "").strip()
                args = body.get("args") or {}
                ctl = self.runtime.controller
                if cmd == "register_app":
                    self._send_json(200, {"ok": True, "result": ctl.register_app(**args)})
                    return
                if cmd == "open_app":
                    self._send_json(200, {"ok": True, "result": ctl.open_app(**args)})
                    return
                if cmd == "annotate":
                    self._send_json(200, {"ok": True, "result": ctl.annotate(**args)})
                    return
                if cmd == "create_dashboard":
                    self._send_json(200, {"ok": True, "result": ctl.create_dashboard(**args)})
                    return
                if cmd == "add_dashboard_panel":
                    self._send_json(200, {"ok": True, "result": ctl.add_dashboard_panel(**args)})
                    return
                if cmd == "update_dashboard_panel":
                    self._send_json(200, {"ok": True, "result": ctl.update_dashboard_panel(**args)})
                    return
                if cmd == "open_dashboard":
                    self._send_json(200, {"ok": True, "result": ctl.open_dashboard(**args)})
                    return
                if cmd == "notify":
                    self._send_json(200, {"ok": True, "result": ctl.notify(**args)})
                    return
                self._send_json(400, {"ok": False, "error": f"Unknown control command: {cmd}"})
                return
            if path == "/api/gui/actionbox/publish_app":
                app_data = {
                    "name": body.get("name") or "ActionBox App",
                    "url": body.get("url") or "about:blank",
                    "kind": body.get("kind") or "custom",
                    "source": "actionbox",
                    "universe": body.get("universe"),
                    "created_by": body.get("created_by") or "agent",
                    "description": body.get("description") or "",
                    "metadata": body.get("metadata") or {},
                    "session_id": body.get("session_id"),
                    "workflow": body.get("workflow"),
                    "host_status": body.get("host_status") or "running",
                }
                app = self.runtime.register_app(app_data)
                opened = self.runtime.open_app(app_id=app.get("id"))
                self.runtime.emit("agent_status", {"message": f"Agent opened {app.get('name')}", "level": "info", "source": "actionbox"})
                self._send_json(200, {"ok": True, "app": app, "opened": opened})
                return
            if path.startswith("/api/gui/apps/") and path.endswith("/remove"):
                app_id = path.split("/")[4]
                self._send_json(200, self.runtime.remove_app(app_id))
                return
            self._send_json(404, {"ok": False, "error": f"Unknown endpoint: {path}"})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
        else:
            # Browser-app fallback keeps refresh/deep-link behavior simple.
            rel = "index.html"
        target = (STATIC_DIR / rel).resolve()
        root = STATIC_DIR.resolve()
        if root not in target.parents and target != root:
            self._send_json(403, {"ok": False, "error": "Forbidden"})
            return
        if not target.exists() or not target.is_file():
            self._send_json(404, {"ok": False, "error": f"Static file not found: {rel}"})
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)


def create_gui_server(config: Dict[str, Any], host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    runtime = GuiRuntime(config)
    handler_cls = type("WolfGuiRequestHandler", (GuiRequestHandler,), {"runtime": runtime})
    return ThreadingHTTPServer((host, port), handler_cls)


def start_gui_server(config: Dict[str, Any], host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    server = create_gui_server(config, host=host, port=port)
    actual_host, actual_port = server.server_address[:2]
    browser_host = "127.0.0.1" if actual_host in {"0.0.0.0", "::"} else actual_host
    url = f"http://{browser_host}:{actual_port}/"
    print(f"Wolf GUI serving at {url}")
    print("Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWolf GUI shutting down.")
    finally:
        server.server_close()
    return 0
