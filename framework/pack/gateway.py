"""FastAPI Gateway V3 for WOLF workflow/action interaction.

The gateway is intentionally a transport/session layer. It owns auth,
websocket fanout, account/session ownership, runtime registry, and per-session
locks. WOLF orchestration lives in GatewayActionWorkflow.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from config.session.default.params.inputs import session_params as DEFAULT_SESSION_PARAMS
from framework.agentic.agents import OpenAIAgent
from framework.utils.config_tools import setup_cli_session
from framework.utils.io_tools import console
from framework.workflows.custom_workflows.gateway_action_workflow import (
    DEFAULT_GATEWAY_SAFE_ACTIONS,
    GatewayActionWorkflow,
)
from framework.workflows.workflow_models import ACTION_NAMES
from framework.gui.capture_models import CaptureUrlRequest, CaptureWorkspaceRequest
from framework.gui.capture_worker import capture_url_async
from framework.gui.capture_storage import CaptureStorage


GATEWAY_DEFAULT_MODE = "single_step"
GATEWAY_DEFAULT_MAX_STEPS = 1
GATEWAY_SAFE_ACTIONS = DEFAULT_GATEWAY_SAFE_ACTIONS
GATEWAY_GUI_ACTIONS = [
    "gui_notify",
    "gui_get_visual_context",
    "gui_capture_url",
    "gui_capture_workspace",
    "gui_create_dashboard",
    "gui_add_dashboard_panel",
    "gui_update_dashboard_panel",
    "gui_open_dashboard",
    "gui_publish_dashboard",
    "gui_register_app",
    "gui_open_app",
]
GATEWAY_READ_ACTIONS = ["read_file"]
GATEWAY_WRITE_ACTIONS = GATEWAY_SAFE_ACTIONS + GATEWAY_GUI_ACTIONS + ["write_file"]
GATEWAY_DEV_ACTIONS = GATEWAY_WRITE_ACTIONS + ["run_syscall"]
GATEWAY_SYSCALL_DEFAULT_ALLOWLIST = ["pwd", "ls", "cat", "head", "tail", "grep", "find", "wc", "echo"]
GATEWAY_PRIVILEGE_PARAM_KEYS = {
    "action_policy",
    "enable_write",
    "enable_syscall",
    "enable_gui_capture",
    "syscall_allowed_commands",
    "syscall_max_timeout",
    "syscall_allow_shell",
    "action_names",
}


def _dedupe_actions(actions: List[str]) -> List[str]:
    return list(dict.fromkeys(a for a in actions if a))


def _default_run_control() -> Dict[str, Any]:
    return {
        "run_id": None,
        "status": "idle",
        "pause_requested": False,
        "stop_requested": False,
        "reassess_requested": False,
        "pending_user_messages": [],
        "step": 0,
        "updated_at": datetime.now().isoformat(),
    }


class AgentConfig(BaseModel):
    """Configuration for a WOLF gateway runtime's main agent."""

    model: str = "google/gemma-4-31B-it"
    host_address: str = "http://localhost"
    host_port: Optional[int] = None
    api_key: Optional[str] = None
    api_key_var: Optional[str] = None
    api_version: Optional[str] = None
    sys_prompt: str = "You are a helpful WOLF assistant."
    agent_name: Optional[str] = None
    verbose: int = 1
    capabilities: List[str] = []
    ctx_window_length: Optional[int] = None
    mode: str = GATEWAY_DEFAULT_MODE
    max_steps: int = GATEWAY_DEFAULT_MAX_STEPS
    action_names: Optional[List[str]] = None
    action_policy: str = "limited"  # safe | limited | write | dev | advanced | master | custom
    gui_url: Optional[str] = None
    gui_action_route: str = "auto"  # auto | direct | client_event
    enable_write: bool = False
    enable_syscall: bool = False
    enable_gui_capture: bool = False
    syscall_allowed_commands: Optional[List[str]] = None
    syscall_max_timeout: int = 10
    syscall_allow_shell: bool = False


class UserCredentials(BaseModel):
    username: str
    password: str


class Message(BaseModel):
    """Generic websocket message model retained for framework.pack compatibility."""

    type: str
    content: Optional[str] = None
    sender: Optional[str] = None
    receiver: Optional[str] = None
    timestamp: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class AuthResponse(BaseModel):
    token: str
    account_id: str
    sessions: List[Dict[str, Any]]


class SessionInfo(BaseModel):
    session_id: str
    created_at: str
    client_type: str
    active: bool
    account_id: Optional[str] = None
    agent_config: Optional[Dict[str, Any]] = None
    last_activity: Optional[str] = None


class SessionHistoryResponse(BaseModel):
    account_id: str
    sessions: List[SessionInfo]


class ConnectionManager:
    """Manages WebSocket connections and WOLF runtimes per account/session."""

    def __init__(self, default_agent_config: Optional[Dict[str, Any]] = None):
        self.account_sessions: Dict[str, Dict[str, Dict[str, WebSocket]]] = {}
        self.session_participants: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.sessions: Dict[str, SessionInfo] = {}
        self.session_agents: Dict[str, OpenAIAgent] = {}  # compatibility accessor
        self.session_runtimes: Dict[str, Dict[str, Any]] = {}
        self.account_default_sessions: Dict[str, str] = {}
        self.auth_tokens: Dict[str, str] = {}
        self.default_agent_config = self._merged_default_agent_config(default_agent_config)

    def _merged_default_agent_config(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cfg = AgentConfig().model_dump()
        for key, value in (overrides or {}).items():
            if value is not None:
                cfg[key] = value
        return cfg

    def default_config(self) -> Dict[str, Any]:
        return copy.deepcopy(self.default_agent_config)

    def authenticate(self, username: str, password: str) -> tuple[str, str]:
        if username == "admin" and password == "password":
            account_id = "acc_123"
        else:
            account_id = f"acc_{hashlib.md5(username.encode()).hexdigest()[:8]}"
        token = hashlib.sha256(f"{username}{account_id}{datetime.now()}".encode()).hexdigest()
        self.auth_tokens[token] = account_id
        return token, account_id

    def get_account_id(self, token: str) -> Optional[str]:
        return self.auth_tokens.get(token)

    def session_belongs_to_account(self, session_id: str, account_id: str) -> bool:
        s = self.sessions.get(session_id)
        return bool(s and s.account_id == account_id)

    def _create_session_record(self, account_id: str, session_id: str, client_type: str = "unknown") -> SessionInfo:
        now = datetime.now().isoformat()
        sess = SessionInfo(
            session_id=session_id,
            created_at=now,
            client_type=client_type,
            active=True,
            account_id=account_id,
            last_activity=now,
        )
        self.sessions[session_id] = sess
        self.account_default_sessions[account_id] = session_id
        self.create_runtime_session(session_id, account_id, self.default_config())
        return sess

    def get_or_create_session(self, account_id: str, session_id: Optional[str] = None, client_type: str = "unknown") -> str:
        if session_id:
            if session_id in self.sessions:
                if not self.session_belongs_to_account(session_id, account_id):
                    raise HTTPException(status_code=403, detail="Forbidden")
                self.sessions[session_id].active = True
                self.sessions[session_id].last_activity = datetime.now().isoformat()
                if not self.session_runtimes.get(session_id):
                    cfg = self.sessions[session_id].agent_config or self.default_config()
                    self.create_runtime_session(session_id, account_id, cfg)
                self.account_default_sessions[account_id] = session_id
                return session_id
            self._create_session_record(account_id=account_id, session_id=session_id, client_type=client_type)
            return session_id

        default_sid = self.account_default_sessions.get(account_id)
        if default_sid and self.session_belongs_to_account(default_sid, account_id):
            self.sessions[default_sid].active = True
            self.sessions[default_sid].last_activity = datetime.now().isoformat()
            if not self.session_runtimes.get(default_sid):
                cfg = self.sessions[default_sid].agent_config or self.default_config()
                self.create_runtime_session(default_sid, account_id, cfg)
            return default_sid

        new_sid = str(uuid.uuid4())
        self._create_session_record(account_id=account_id, session_id=new_sid, client_type=client_type)
        return new_sid

    def list_account_sessions(self, account_id: str) -> List[SessionInfo]:
        return [s for s in self.sessions.values() if s.account_id == account_id]

    async def connect(
        self,
        websocket: WebSocket,
        account_id: str,
        session_id: str,
        client_type: str,
        participant_id: Optional[str] = None,
        participant_role: str = "user",
    ):
        await websocket.accept()
        participant_id = participant_id or f"{client_type}_{uuid.uuid4().hex[:8]}"
        self.account_sessions.setdefault(account_id, {}).setdefault(session_id, {})[participant_id] = websocket
        self.session_participants.setdefault(session_id, {})[participant_id] = {
            "participant_id": participant_id,
            "role": participant_role,
            "client_type": client_type,
            "connected_at": datetime.now().isoformat(),
            "active": True,
        }
        self.get_or_create_session(account_id=account_id, session_id=session_id, client_type=client_type)
        self.sessions[session_id].client_type = client_type
        return participant_id

    def disconnect(self, account_id: str, session_id: str, participant_id: Optional[str] = None):
        if account_id in self.account_sessions and session_id in self.account_sessions[account_id]:
            if participant_id:
                self.account_sessions[account_id][session_id].pop(participant_id, None)
            else:
                self.account_sessions[account_id].pop(session_id, None)
            if not self.account_sessions[account_id].get(session_id):
                self.account_sessions[account_id].pop(session_id, None)

        if participant_id and session_id in self.session_participants and participant_id in self.session_participants[session_id]:
            self.session_participants[session_id][participant_id]["active"] = False

        has_connections = any(session_id in sessions for sessions in self.account_sessions.values())
        if session_id in self.sessions:
            self.sessions[session_id].active = bool(has_connections)

        if self.account_default_sessions.get(account_id) == session_id and not has_connections:
            del self.account_default_sessions[account_id]
            for s in self.list_account_sessions(account_id):
                if s.active:
                    self.account_default_sessions[account_id] = s.session_id
                    break

        console.print(f"[-] Connection closed: Account={account_id}, SessionID={session_id}, Participant={participant_id}")

    def _session_dir_for(self, account_id: str, session_id: str) -> str:
        safe_account = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in account_id)
        safe_session = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in session_id)
        path = Path("wf_workspace") / "gateway" / safe_account / f"session_{safe_session}"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _session_params_from_config(self, session_id: str, account_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        params = copy.deepcopy(DEFAULT_SESSION_PARAMS)
        params["session_dir"] = self._session_dir_for(account_id, session_id)
        params["verbose"] = int(config.get("verbose", params.get("verbose", 0)) or 0)
        params["banner_image_width"] = int(params.get("banner_image_width", 80) or 80)
        llm_entry = {
            "provider_type": "openai",
            "host": config.get("host_address") or "http://localhost",
            "port": config.get("host_port"),
            "api_key": config.get("api_key"),
            "api_key_var": config.get("api_key_var"),
            "api_version": config.get("api_version") or "",
            "verbose": int(config.get("verbose", 1) or 1),
            "model": config.get("model"),
            "capabilities": config.get("capabilities") or [],
            "ctx_window_length": config.get("ctx_window_length"),
        }
        params["LLMs"] = {config.get("agent_name") or "main": llm_entry}
        return params

    def create_runtime_session(self, session_id: str, account_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            console.print(f"[!!][CREATE RUNTIME] Starting WOLF runtime for session {session_id}")
            params = self._session_params_from_config(session_id, account_id, config)
            session = setup_cli_session(session_params=params, workflow_cls=GatewayActionWorkflow)
            wf = session["wf"]
            infra = wf.infra
            runtime = {
                "agent": session["agents"]["main"],
                "wf": wf,
                "infra": infra,
                "managers": session["managers"],
                "config": config,
                "gui_route": {
                    "route": config.get("gui_action_route") or "auto",
                    "gui_url": config.get("gui_url"),
                    "reachable": None,
                    "checked_at": None,
                },
                "run_control": _default_run_control(),
                "active_task": None,
                "session_dir": session["session_dir"],
                "db_client": session.get("db_client"),
                "lock": asyncio.Lock(),
            }
            self.session_runtimes[session_id] = runtime
            self.session_agents[session_id] = runtime["agent"]
            if session_id in self.sessions:
                self.sessions[session_id].agent_config = config
            console.print(f"[+] Runtime created for session {session_id}: {runtime['agent'].name}")
            return runtime
        except Exception as e:
            console.print(f"[!] CRITICAL Error creating runtime: {e}")
            console.print(traceback.format_exc())
            raise

    def create_agent(self, session_id: str, config: Dict[str, Any]) -> OpenAIAgent:
        account_id = self.sessions.get(session_id).account_id if session_id in self.sessions else "acc_unknown"
        runtime = self.create_runtime_session(session_id, account_id or "acc_unknown", config)
        return runtime["agent"]

    def get_agent(self, session_id: str) -> Optional[OpenAIAgent]:
        runtime = self.session_runtimes.get(session_id)
        if runtime:
            return runtime.get("agent")
        return self.session_agents.get(session_id)

    def get_runtime(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.session_runtimes.get(session_id)

    async def send_message_to_session(self, message: dict, session_id: str, exclude_participant: Optional[str] = None):
        delivered = False
        for _, sessions in self.account_sessions.items():
            if session_id in sessions:
                for participant_id, websocket in list(sessions[session_id].items()):
                    if exclude_participant and participant_id == exclude_participant:
                        continue
                    try:
                        await websocket.send_json(message)
                        delivered = True
                    except Exception as e:
                        console.print(f"[!] Error sending to session {session_id}/{participant_id}: {e}")
        if not delivered:
            console.print(f"[!] Session {session_id} not found for sending message")
        return delivered


class WolfGateway:
    """Main gateway application for WOLF workflow action interaction."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000, static_dir: str = "./framework/ui/webapp", default_agent_config: Optional[Dict[str, Any]] = None):
        self.app = FastAPI(title="WOLF Agent Gateway V3", version="3.0.0")
        self.host = host
        self.port = port
        self.static_dir = static_dir
        self.manager = ConnectionManager(default_agent_config=default_agent_config)

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._register_routes()

        try:
            self.app.mount("/static", StaticFiles(directory=static_dir), name="static")
        except Exception as e:
            console.print(f"[!] Warning: Could not mount static files: {e}")

    def _get_account_id(self, token: str) -> str:
        account_id = self.manager.get_account_id(token)
        if not account_id:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return account_id

    SECRET_KEYS = {"api_key", "token", "auth_token", "password", "secret", "authorization"}

    def _redact_value(self, key: str, value: Any) -> Any:
        key_l = str(key).lower()
        # api_key_var is the *name* of an environment variable, not the secret
        # value itself.  Redacting it makes the GUI lose the configured key
        # source on later saves/reconnects.
        if key_l == "api_key_var":
            return value
        if any(secret in key_l for secret in self.SECRET_KEYS):
            if value in (None, ""):
                return value
            text = str(value)
            if len(text) <= 8:
                return "***REDACTED***"
            return f"{text[:4]}...{text[-4:]}"
        return value

    def _redact_config(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self._redact_value(k, self._redact_config(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_config(v) for v in value]
        return value

    def _resolve_action_names(self, config: Dict[str, Any]) -> List[str]:
        policy = str(config.get("action_policy") or "limited").strip().lower()
        known = list(ACTION_NAMES)
        explicit = config.get("action_names")
        if explicit:
            if isinstance(explicit, str):
                actions = [a.strip() for a in explicit.split(",") if a.strip()]
            else:
                actions = list(explicit)

            # Explicit action_names is a base allowlist, but gateway-required
            # capability toggles must still be able to append their companion
            # actions. Otherwise stale saved policy lists can silently suppress
            # newly added GUI actions such as screenshot capture.
            if config.get("enable_gui_capture") or policy in {"advanced", "master"}:
                actions.extend(["gui_capture_url", "gui_capture_workspace"])
            if policy != "safe":
                actions.extend(a for a in GATEWAY_GUI_ACTIONS if a not in actions)
            if config.get("enable_write") and "write_file" not in actions:
                actions.append("write_file")
            if config.get("enable_syscall") and "run_syscall" not in actions:
                actions.append("run_syscall")
            return _dedupe_actions(actions)


        if policy == "safe":
            actions = list(GATEWAY_SAFE_ACTIONS)
        elif policy == "limited":
            # Broad non-filesystem/non-syscall workspace capability. This includes
            # GUI actions, memory/context, universe/KB/TB discovery/interactions,
            # and playbook actions, but excludes local read/write/syscall.
            actions = [a for a in known if a not in {"read_file", "write_file", "run_syscall"}]
        elif policy == "advanced":
            # Everything except direct system calls. read_file/write_file remain
            # guarded by explicit execution-policy flags below.
            actions = [a for a in known if a != "run_syscall"]
        elif policy == "master":
            actions = known
        elif policy in {"write", "dev"}:
            actions = list(GATEWAY_WRITE_ACTIONS)
            if policy == "dev":
                actions.append("run_syscall")
        else:
            # Unknown/custom policy name with no explicit action_names falls back
            # to safe rather than silently granting broad privileges.
            actions = list(GATEWAY_SAFE_ACTIONS)

        if policy != "safe":
            actions.extend(GATEWAY_GUI_ACTIONS)
        if config.get("enable_write") and "write_file" not in actions:
            actions.append("write_file")
        if config.get("enable_syscall") and "run_syscall" not in actions:
            actions.append("run_syscall")
        return _dedupe_actions(actions)

    def _resolve_execution_policy(self, config: Dict[str, Any]) -> Dict[str, Any]:
        policy = str(config.get("action_policy") or "limited").strip().lower()
        allow_write = bool(config.get("enable_write")) or policy in {"write", "dev", "advanced", "master"}
        allow_syscall = bool(config.get("enable_syscall")) or policy in {"dev", "master"}
        allowed_cmds = config.get("syscall_allowed_commands") or GATEWAY_SYSCALL_DEFAULT_ALLOWLIST
        if isinstance(allowed_cmds, str):
            allowed_cmds = [c.strip() for c in allowed_cmds.split(",") if c.strip()]
        return {
            "allow_write_file": allow_write,
            "allow_run_syscall": allow_syscall,
            "allow_gui_capture": bool(config.get("enable_gui_capture", False)) or policy in {"advanced", "master"},
            "syscall_allowed_commands": list(allowed_cmds),
            "syscall_max_timeout": int(config.get("syscall_max_timeout") or 10),
            "syscall_allow_shell": bool(config.get("syscall_allow_shell", False)),
        }

    def _probe_gui_api_sync(self, gui_url: Optional[str]) -> Dict[str, Any]:
        target = str(gui_url or "").strip().rstrip("/")
        if not target:
            return {"reachable": False, "gui_url": None, "error": "No GUI URL supplied by client."}
        try:
            request = urllib.request.Request(f"{target}/api/gui/health", method="GET")
            with urllib.request.urlopen(request, timeout=2.0) as response:
                raw = response.read().decode("utf-8", errors="replace")
                ok = 200 <= int(getattr(response, "status", 200)) < 300
                return {"reachable": ok, "gui_url": target, "status": getattr(response, "status", None), "body": raw[:500]}
        except Exception as exc:
            return {"reachable": False, "gui_url": target, "error": str(exc)}

    def _set_gui_route(self, session_id: str, route_info: Dict[str, Any]) -> Dict[str, Any]:
        runtime = self.manager.get_runtime(session_id)
        requested = str((route_info or {}).get("requested_route") or "auto").strip().lower()
        reachable = bool((route_info or {}).get("reachable"))
        gui_url = (route_info or {}).get("gui_url")
        if requested in {"direct", "client_event"}:
            route = requested
        else:
            route = "direct" if reachable else "client_event"
        resolved = {
            "route": route,
            "requested_route": requested or "auto",
            "gui_url": gui_url,
            "reachable": reachable,
            "checked_at": datetime.now().isoformat(),
            "probe": route_info,
        }
        if runtime:
            runtime["gui_route"] = resolved
            cfg = runtime.get("config") or {}
            cfg["gui_action_route"] = route
            if gui_url:
                cfg["gui_url"] = gui_url
            runtime["config"] = cfg
            if session_id in self.manager.sessions:
                self.manager.sessions[session_id].agent_config = cfg
        return resolved

    def _gui_command_from_workflow_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if event.get("type") != "workflow_result":
            return None
        result = event.get("result") or {}
        if not isinstance(result, dict) or not result.get("deferred_to_gui_client"):
            return None
        command = result.get("gui_command") or {}
        if not isinstance(command, dict):
            return None
        action = command.get("action") or event.get("action")
        payload = command.get("payload") or {}
        return {
            "type": "gui_command",
            "command_id": f"guicmd_{uuid.uuid4().hex[:12]}",
            "action": action,
            "payload": payload if isinstance(payload, dict) else {},
            "content": f"Execute GUI command locally: {action}",
            "workflow_event": event,
            "timestamp": datetime.now().isoformat(),
        }

    def _register_routes(self):
        @self.app.get("/")
        async def root():
            try:
                return FileResponse(f"{self.static_dir}/index.html")
            except Exception:
                return {
                    "message": "WOLF Agent Gateway V3",
                    "version": "3.0.0",
                    "status": "running",
                    "note": "Web UI not available. Use WebSocket API directly.",
                }

        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy", "active_accounts": len(self.manager.account_sessions)}

        @self.app.post("/auth/login")
        async def login(credentials: UserCredentials):
            token, account_id = self.manager.authenticate(credentials.username, credentials.password)
            sessions = [s.model_dump(mode="json") for s in self.manager.list_account_sessions(account_id)]
            return AuthResponse(token=token, account_id=account_id, sessions=sessions)

        @self.app.get("/accounts/{account_id}/sessions")
        async def get_account_sessions(account_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            if current_account != account_id:
                raise HTTPException(status_code=403, detail="Forbidden")
            return SessionHistoryResponse(account_id=account_id, sessions=self.manager.list_account_sessions(account_id))

        @self.app.post("/sessions/{session_id}/configure")
        async def configure_agent(session_id: str, config: AgentConfig, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            runtime = self.manager.create_runtime_session(session_id, current_account, config.model_dump())
            return {"status": "configured", "agent_name": runtime["agent"].name, "session_id": session_id, "config": self._redact_config(config.model_dump())}

        @self.app.get("/sessions/{session_id}/params")
        async def get_agent_params(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            config = self.manager.sessions[session_id].agent_config
            if not config:
                raise HTTPException(status_code=400, detail="No runtime configured for this session")
            return self._redact_config(config)

        @self.app.get("/sessions/{session_id}/policy")
        async def get_session_policy(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            runtime = self.manager.get_runtime(session_id)
            config = (runtime or {}).get("config") or self.manager.sessions[session_id].agent_config or self.manager.default_config()
            return {
                "session_id": session_id,
                "configured": self._redact_config(config),
                "resolved_action_names": self._resolve_action_names(config),
                "resolved_execution_policy": self._resolve_execution_policy(config),
            }

        @self.app.patch("/sessions/{session_id}/params")
        async def patch_agent_params(session_id: str, updates: Dict[str, Any], token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            current_config = copy.deepcopy(self.manager.sessions[session_id].agent_config or self.manager.default_config())
            updates = copy.deepcopy(updates or {})
            for secret_key in ("api_key", "api_key_var"):
                if secret_key in updates:
                    raw = updates.get(secret_key)
                    text = str(raw or "")
                    if raw in (None, "") or "redacted" in text.lower() or "***" in text:
                        updates.pop(secret_key, None)
            current_config.update(updates)

            # Policy/privilege updates must not reset chat history or context.
            # The gateway resolves actions/execution policy fresh for each chat
            # turn from runtime["config"], so for policy-only PATCHes we can
            # update config in place without recreating the WOLF runtime.
            policy_only = bool(updates) and all(str(k) in GATEWAY_PRIVILEGE_PARAM_KEYS for k in updates.keys())
            runtime = self.manager.get_runtime(session_id)
            if policy_only and runtime:
                runtime["config"] = current_config
                if session_id in self.manager.sessions:
                    self.manager.sessions[session_id].agent_config = current_config
                return {
                    "status": "updated",
                    "runtime_recreated": False,
                    "session_id": session_id,
                    "agent_name": getattr(runtime.get("agent"), "name", None),
                    "updated_params": self._redact_config(updates),
                    "resolved_action_names": self._resolve_action_names(current_config),
                    "resolved_execution_policy": self._resolve_execution_policy(current_config),
                }

            runtime = self.manager.create_runtime_session(session_id, current_account, current_config)
            return {
                "status": "updated",
                "runtime_recreated": True,
                "session_id": session_id,
                "agent_name": runtime["agent"].name,
                "updated_params": self._redact_config(updates),
            }

        @self.app.get("/sessions/{session_id}/participants")
        async def get_session_participants(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            participants = list(self.manager.session_participants.get(session_id, {}).values())
            return {"session_id": session_id, "participants": participants}

        @self.app.post("/sessions/{session_id}/reset")
        async def reset_session(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            runtime = self.manager.get_runtime(session_id)
            if not runtime:
                raise HTTPException(status_code=400, detail="No runtime configured for this session")
            async with runtime["lock"]:
                runtime["agent"].reset_ctx()
            return {"status": "reset", "session_id": session_id}


        @self.app.post("/api/gui/capture/url")
        async def gui_capture_url_endpoint(request: CaptureUrlRequest, token: str = Query(...), session_id: Optional[str] = Query(None)):
            """Permissioned backend URL screenshot capture endpoint.

            Browser clients call this only after the local user enables the
            screenshot-capture toggle. The server still applies URL/SSRF policy
            in capture_worker/capture_policy before Playwright navigates.
            """
            current_account = self._get_account_id(token)
            target_session = session_id or self.manager.account_default_sessions.get(current_account) or "default"
            if target_session in self.manager.sessions and not self.manager.session_belongs_to_account(target_session, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            result = await capture_url_async(request, session_id=target_session)
            event = {
                "type": "gui_capture_audit",
                "content": f"GUI screenshot capture {result.status}: {request.url}",
                "capture_id": result.capture_id,
                "status": result.status,
                "ok": result.ok,
                "source_url": request.url,
                "image_path": result.image_path,
                "timestamp": datetime.now().isoformat(),
                "session_id": target_session,
            }
            try:
                await self.manager.send_message_to_session(event, target_session)
            except Exception:
                pass
            return result.model_dump(mode="json")


        @self.app.get("/api/gui/capture/{capture_id}")
        async def gui_capture_get_endpoint(capture_id: str, token: str = Query(...), session_id: Optional[str] = Query(None)):
            """Return a previously captured screenshot image for this account/session."""
            current_account = self._get_account_id(token)
            target_session = session_id or self.manager.account_default_sessions.get(current_account) or "default"
            if target_session in self.manager.sessions and not self.manager.session_belongs_to_account(target_session, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            storage = CaptureStorage()
            for fmt in ("png", "jpeg"):
                path = storage.image_path(target_session, capture_id, fmt)
                if path.exists() and path.is_file():
                    media_type = "image/jpeg" if fmt == "jpeg" else "image/png"
                    return FileResponse(str(path), media_type=media_type, filename=path.name)
            raise HTTPException(status_code=404, detail="Capture image not found")

        @self.app.post("/api/gui/capture/workspace")
        async def gui_capture_workspace_endpoint(request: CaptureWorkspaceRequest, token: str = Query(...), session_id: Optional[str] = Query(None)):
            """Capture a bounded set of workspace/panel URLs.

            The browser client supplies explicit URLs and/or current visual
            context after checking the local capture permission toggle.
            """
            current_account = self._get_account_id(token)
            target_session = session_id or self.manager.account_default_sessions.get(current_account) or "default"
            if target_session in self.manager.sessions and not self.manager.session_belongs_to_account(target_session, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")

            urls = list(request.urls or [])
            if request.visual_context and len(urls) < request.max_panels:
                try:
                    vc = request.visual_context or {}
                    panels = []
                    if isinstance(vc.get("dashboard_panels"), list):
                        panels.extend(vc.get("dashboard_panels") or [])
                    dashboard = vc.get("dashboard") or {}
                    if isinstance(dashboard, dict) and isinstance(dashboard.get("panels"), list):
                        panels.extend(dashboard.get("panels") or [])
                    if isinstance(vc.get("active_dashboard"), dict) and isinstance(vc["active_dashboard"].get("panels"), list):
                        panels.extend(vc["active_dashboard"].get("panels") or [])
                    for panel in panels:
                        if not isinstance(panel, dict):
                            continue
                        iframe = panel.get("iframe") or {}
                        url = panel.get("url") or iframe.get("src") or iframe.get("url")
                        panel_id = panel.get("id") or panel.get("key") or panel.get("panel_id")
                        if request.panel_ids and panel_id not in request.panel_ids:
                            continue
                        if url and url not in urls:
                            urls.append(url)
                        if len(urls) >= request.max_panels:
                            break
                except Exception:
                    pass

            results = []
            for url in urls[: int(request.max_panels or 1)]:
                one = CaptureUrlRequest(
                    url=url,
                    viewport=request.viewport,
                    format=request.format,
                    quality=request.quality,
                    full_page=request.full_page,
                    wait_until=request.wait_until,
                    extra_wait_ms=request.extra_wait_ms,
                    timeout_ms=request.timeout_ms,
                    reason=request.reason,
                    metadata=request.metadata,
                )
                results.append((await capture_url_async(one, session_id=target_session)).model_dump(mode="json"))
            out = {"ok": all(r.get("ok") for r in results) if results else False, "count": len(results), "results": results}
            try:
                await self.manager.send_message_to_session({
                    "type": "gui_capture_audit",
                    "content": f"GUI workspace screenshot capture completed for {len(results)} URL(s).",
                    "ok": out["ok"],
                    "count": len(results),
                    "timestamp": datetime.now().isoformat(),
                    "session_id": target_session,
                }, target_session)
            except Exception:
                pass
            return out

        @self.app.websocket("/ws/{account_id}/{session_id}")
        async def websocket_endpoint(
            websocket: WebSocket,
            account_id: str,
            session_id: str,
            token: str = Query(...),
            participant_id: Optional[str] = Query(None),
            participant_role: str = Query("user"),
            client_type: str = Query("tui"),
        ):
            try:
                current_account = self._get_account_id(token)
            except HTTPException:
                await websocket.close(code=4001, reason="Unauthorized")
                return

            if current_account != account_id:
                await websocket.close(code=4003, reason="Forbidden")
                return

            if session_id in self.manager.sessions and not self.manager.session_belongs_to_account(session_id, account_id):
                await websocket.close(code=4003, reason="Forbidden")
                return

            participant_id = await self.manager.connect(websocket, account_id, session_id, client_type, participant_id, participant_role)

            try:
                await self.manager.send_message_to_session(
                    {
                        "type": "presence",
                        "event": "joined",
                        "participant_id": participant_id,
                        "participant_role": participant_role,
                        "client_type": client_type,
                        "content": f"{participant_id} joined session {session_id} as {participant_role}.",
                        "timestamp": datetime.now().isoformat(),
                        "session_id": session_id,
                    },
                    session_id,
                )
                await websocket.send_json(
                    {
                        "type": "system",
                        "content": f"Connected to WOLF Gateway V3. Account: {account_id}. Session: {session_id}. Participant: {participant_id}.",
                        "timestamp": datetime.now().isoformat(),
                        "session_id": session_id,
                        "participant_id": participant_id,
                    }
                )

                while True:
                    try:
                        data = await asyncio.wait_for(websocket.receive_json(), timeout=300.0)
                        msg_type = data.get("type")

                        if msg_type == "chat":
                            visual_context = data.get("visual_context")
                            if visual_context is None and isinstance(data.get("metadata"), dict):
                                visual_context = data.get("metadata", {}).get("visual_context")
                            runtime = self.manager.get_runtime(session_id)
                            control = self._run_control_for(runtime) if runtime else {}
                            active_statuses = {"running", "pause_requested", "paused", "resume_requested", "stop_requested"}
                            if runtime and control.get("status") in active_statuses and not data.get("force_new_run"):
                                await self._handle_agent_control(
                                    {
                                        "command": "reassess_after_step",
                                        "content": data.get("content", ""),
                                        "sender": data.get("sender") or participant_id,
                                        "visual_context": visual_context,
                                    },
                                    session_id,
                                    participant_id,
                                )
                            else:
                                task = asyncio.create_task(self._handle_chat_message(
                                    data.get("content", ""),
                                    session_id,
                                    sender=data.get("sender") or participant_id,
                                    visual_context=visual_context,
                                ))
                                if runtime is not None:
                                    runtime["active_task"] = task
                        elif msg_type == "agent_control":
                            await self._handle_agent_control(data, session_id, participant_id)
                        elif msg_type == "gui_client_hello":
                            requested = str(data.get("requested_route") or data.get("gui_action_route") or "auto").strip().lower()
                            gui_url = data.get("gui_url")
                            probe = self._probe_gui_api_sync(gui_url) if requested != "client_event" else {"reachable": False, "gui_url": gui_url, "skipped": "client_event requested"}
                            probe["requested_route"] = requested
                            resolved = self._set_gui_route(session_id, probe)
                            await self.manager.send_message_to_session(
                                {
                                    "type": "gui_route_resolved",
                                    "content": f"GUI action route resolved: {resolved.get('route')}",
                                    "route": resolved.get("route"),
                                    "gui_url": resolved.get("gui_url"),
                                    "reachable": resolved.get("reachable"),
                                    "probe": resolved.get("probe"),
                                    "timestamp": datetime.now().isoformat(),
                                    "session_id": session_id,
                                },
                                session_id,
                            )
                        elif msg_type == "gui_command_result":
                            gui_result_event = {
                                "type": "gui_command_result",
                                "command_id": data.get("command_id"),
                                "action": data.get("action"),
                                "ok": data.get("ok"),
                                "content": data.get("content") or ("GUI command completed." if data.get("ok") else "GUI command failed."),
                                "result": data.get("result"),
                                "error": data.get("error"),
                                "timestamp": datetime.now().isoformat(),
                                "session_id": session_id,
                            }

                            # Important bridge: deferred GUI commands execute in the
                            # browser client after the workflow step has already
                            # returned. Broadcasting the websocket event is not
                            # enough; append the result into the WOLF workflow
                            # history so the next agent turn can use capture IDs,
                            # image paths, errors, and GUI command metadata.
                            try:
                                runtime = self.manager.get_runtime(session_id)
                                if runtime:
                                    wf = runtime.get("wf")
                                    infra = runtime.get("infra")
                                    result_text = json.dumps(gui_result_event, indent=2, sort_keys=True, default=str)[:40000]
                                    action_label = data.get("action") or "gui_command_result"
                                    history_payload = {
                                        "action": "gui_command_result",
                                        "gui_action": action_label,
                                        "command_id": data.get("command_id"),
                                        "ok": data.get("ok"),
                                    }
                                    if infra is not None and hasattr(infra, "append_chat_history"):
                                        infra.append_chat_history(
                                            actor="system",
                                            content=f"[GUI COMMAND RESULT] {action_label}:\n{result_text}",
                                            action=history_payload,
                                            log_console=True,
                                        )
                                    elif wf is not None and hasattr(wf, "update_history"):
                                        wf.update_history(
                                            actor="system",
                                            content=f"[GUI COMMAND RESULT] {action_label}:\n{result_text}",
                                            action=history_payload,
                                            log_console=True,
                                        )

                                    def _collect_capture_artifacts(value):
                                        found = []
                                        if isinstance(value, dict):
                                            image_path = value.get("image_path")
                                            capture_id = value.get("capture_id")
                                            if image_path or capture_id:
                                                found.append({
                                                    "ok": value.get("ok"),
                                                    "status": value.get("status"),
                                                    "capture_id": capture_id,
                                                    "source_url": value.get("source_url"),
                                                    "image_path": image_path,
                                                    "metadata_path": value.get("metadata_path"),
                                                    "width": value.get("width"),
                                                    "height": value.get("height"),
                                                    "format": value.get("format"),
                                                    "error": value.get("error"),
                                                })
                                            for key in ("results", "captures", "items"):
                                                nested = value.get(key)
                                                if isinstance(nested, list):
                                                    for item in nested:
                                                        found.extend(_collect_capture_artifacts(item))
                                        elif isinstance(value, list):
                                            for item in value:
                                                found.extend(_collect_capture_artifacts(item))
                                        return found

                                    capture_artifacts = _collect_capture_artifacts(data.get("result"))
                                    capture_artifacts = [a for a in capture_artifacts if a.get("image_path") or a.get("capture_id")]
                                    if capture_artifacts:
                                        runtime.setdefault("pending_gui_capture_artifacts", []).extend(capture_artifacts)
                                        gui_result_event["capture_artifacts"] = capture_artifacts
                                        gui_result_event["image_references"] = [
                                            {"name": Path(str(a.get("image_path") or "")).name, "path": a.get("image_path")}
                                            for a in capture_artifacts
                                            if a.get("image_path")
                                        ]
                                    if wf is not None and hasattr(wf, "save_session_state"):
                                        wf.save_session_state()
                            except Exception as bridge_exc:
                                gui_result_event["history_bridge_error"] = f"{type(bridge_exc).__name__}: {bridge_exc}"

                            await self.manager.send_message_to_session(gui_result_event, session_id)

                            try:
                                runtime = self.manager.get_runtime(session_id)
                                pending_command = None
                                if runtime:
                                    pending = runtime.setdefault("pending_gui_commands", {})
                                    command_id = data.get("command_id")
                                    if command_id:
                                        pending_command = pending.pop(command_id, None)
                                action_label = data.get("action") or (pending_command or {}).get("action")
                                should_continue = bool((pending_command or {}).get("auto_continue")) or self._should_auto_continue_gui_command(action_label)
                                if runtime and should_continue:
                                    existing_continue = runtime.get("gui_auto_continue_task")
                                    if existing_continue is not None and not existing_continue.done():
                                        await self.manager.send_message_to_session(
                                            {
                                                "type": "workflow_status",
                                                "status": "queued",
                                                "content": "GUI command result received while an agent continuation is already running; result has been appended to history.",
                                                "action": action_label,
                                                "command_id": data.get("command_id"),
                                                "timestamp": datetime.now().isoformat(),
                                                "session_id": session_id,
                                            },
                                            session_id,
                                        )
                                    else:
                                        previous_task = runtime.get("active_task")
                                        task = asyncio.create_task(
                                            self._auto_continue_after_gui_command_result(
                                                session_id,
                                                action=action_label,
                                                command_id=data.get("command_id"),
                                                ok=data.get("ok"),
                                                previous_task=previous_task,
                                            )
                                        )
                                        runtime["gui_auto_continue_task"] = task
                                        runtime["active_task"] = task
                            except Exception as auto_exc:
                                await self.manager.send_message_to_session(
                                    {
                                        "type": "workflow_error",
                                        "status": "error",
                                        "content": f"GUI command result stored, but auto-continuation could not be scheduled: {type(auto_exc).__name__}: {auto_exc}",
                                        "error": str(auto_exc),
                                        "timestamp": datetime.now().isoformat(),
                                        "session_id": session_id,
                                    },
                                    session_id,
                                )
                        elif msg_type == "participant_message":
                            await self._handle_participant_message(data, session_id, participant_id)
                        elif msg_type == "ping":
                            await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
                        elif msg_type == "pong":
                            continue
                        else:
                            await self.manager.send_message_to_session(
                                {
                                    "type": "error",
                                    "content": f"Unsupported message type: {msg_type}",
                                    "timestamp": datetime.now().isoformat(),
                                    "session_id": session_id,
                                },
                                session_id,
                            )
                    except asyncio.TimeoutError:
                        await websocket.send_json({"type": "ping", "timestamp": datetime.now().isoformat(), "session_id": session_id})

            except WebSocketDisconnect:
                console.print(f"[Audit] WebSocketDisconnect for session {session_id}")
                self.manager.disconnect(account_id, session_id, participant_id)
            except Exception as e:
                console.print(f"[!] CRITICAL WebSocket handler error: {e}")
                console.print(traceback.format_exc())
                self.manager.disconnect(account_id, session_id, participant_id)

    def _run_control_for(self, runtime: Dict[str, Any]) -> Dict[str, Any]:
        control = runtime.setdefault("run_control", _default_run_control())
        for key, value in _default_run_control().items():
            control.setdefault(key, copy.deepcopy(value))
        return control

    def _run_control_snapshot(self, session_id: str, control: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "run_control_state",
            "session_id": session_id,
            "run_id": control.get("run_id"),
            "status": control.get("status") or "idle",
            "pause_requested": bool(control.get("pause_requested")),
            "stop_requested": bool(control.get("stop_requested")),
            "reassess_requested": bool(control.get("reassess_requested")),
            "pending_user_message_count": len(control.get("pending_user_messages") or []),
            "step": int(control.get("step") or 0),
            "timestamp": datetime.now().isoformat(),
            "content": f"Agent run status: {control.get('status') or 'idle'}",
        }

    async def _broadcast_run_control(self, session_id: str, control: Dict[str, Any], content: Optional[str] = None):
        event = self._run_control_snapshot(session_id, control)
        if content:
            event["content"] = content
        await self.manager.send_message_to_session(event, session_id)

    def _should_auto_continue_gui_command(self, action: Any) -> bool:
        """Return true for deferred GUI commands whose result should feed a follow-up agent turn."""
        return str(action or "").strip() in {"gui_get_visual_context", "gui_capture_url", "gui_capture_workspace"}

    def _gui_command_continuation_prompt(self, action: Any, command_id: Any, ok: Any) -> str:
        status = "succeeded" if ok else "failed"
        return (
            "[SYSTEM CONTINUATION: A deferred GUI browser command result is now available in the "
            "conversation history.\n"
            f"Command: {action or 'gui_command'}\n"
            f"Command id: {command_id or 'unknown'}\n"
            f"Status: {status}\n\n"
            "Use the most recent [GUI COMMAND RESULT] entry to answer the user's original request. "
            "Do not repeat the same GUI command unless the result is missing or unusable. "
            "If the result reports cross-origin iframe, DOM, pixel, or permission limitations, briefly "
            "explain the limitation and answer using the available dashboard/workspace metadata. "
            "Respond to the user with a normal send_message action.]"
        )

    async def _auto_continue_after_gui_command_result(
        self,
        session_id: str,
        *,
        action: Any,
        command_id: Any,
        ok: Any,
        previous_task: Any = None,
    ) -> None:
        """Start a follow-up agent turn after browser-deferred GUI context/capture results.

        Deferred GUI commands complete after the workflow step that requested them.
        Without this continuation, the command result is stored in history but the
        agent never gets another turn to consume it, leaving the user with a silent
        "GUI command completed" notice and no assistant response.
        """
        runtime = self.manager.get_runtime(session_id)
        if not runtime:
            await self.manager.send_message_to_session(
                {
                    "type": "workflow_error",
                    "status": "error",
                    "content": "GUI command result arrived, but no runtime exists to continue the agent turn.",
                    "error": "No runtime configured for GUI command auto-continuation.",
                    "timestamp": datetime.now().isoformat(),
                    "session_id": session_id,
                },
                session_id,
            )
            return

        try:
            current = asyncio.current_task()
            if previous_task is not None and previous_task is not current and not previous_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(previous_task), timeout=15)
                except asyncio.TimeoutError:
                    await self.manager.send_message_to_session(
                        {
                            "type": "workflow_status",
                            "status": "waiting",
                            "content": "GUI command result is ready; waiting for the current agent step lock before continuing.",
                            "timestamp": datetime.now().isoformat(),
                            "session_id": session_id,
                        },
                        session_id,
                    )
                except Exception:
                    # The previous task may have failed; still attempt to continue so
                    # the agent can explain the GUI command result or failure.
                    pass

            control = self._run_control_for(runtime)
            control["status"] = "running"
            control["run_id"] = f"run_{uuid.uuid4().hex[:12]}"
            control["pause_requested"] = False
            control["stop_requested"] = False
            control["reassess_requested"] = False
            control["updated_at"] = datetime.now().isoformat()
            await self._broadcast_run_control(session_id, control, content=f"GUI command result received; continuing agent response for {action}.")
            await self.manager.send_message_to_session(
                {
                    "type": "workflow_status",
                    "status": "continuing",
                    "content": f"GUI command result received; asking agent to answer using {action} result.",
                    "action": action,
                    "command_id": command_id,
                    "timestamp": datetime.now().isoformat(),
                    "session_id": session_id,
                },
                session_id,
            )

            prompt = self._gui_command_continuation_prompt(action, command_id, ok)
            await self._handle_chat_message(prompt, session_id, sender="system_gui_continuation", visual_context=None)
        except Exception as exc:
            try:
                await self.manager.send_message_to_session(
                    {
                        "type": "workflow_error",
                        "status": "error",
                        "content": f"GUI command auto-continuation failed: {type(exc).__name__}: {exc}",
                        "error": str(exc),
                        "timestamp": datetime.now().isoformat(),
                        "session_id": session_id,
                    },
                    session_id,
                )
            except Exception:
                pass
        finally:
            runtime = self.manager.get_runtime(session_id)
            if runtime and runtime.get("gui_auto_continue_task") is asyncio.current_task():
                runtime.pop("gui_auto_continue_task", None)

    async def _handle_agent_control(self, data: Dict[str, Any], session_id: str, participant_id: str = "gui"):
        runtime = self.manager.get_runtime(session_id)
        if not runtime:
            await self.manager.send_message_to_session({"type": "error", "content": "No runtime configured for agent control.", "timestamp": datetime.now().isoformat(), "session_id": session_id}, session_id)
            return
        control = self._run_control_for(runtime)
        command = str(data.get("command") or data.get("action") or "state_request").strip().lower()
        now = datetime.now().isoformat()
        control["updated_at"] = now
        content = "Agent control state requested."

        if command in {"pause", "pause_after_step"}:
            control["pause_requested"] = True
            if control.get("status") in {"idle", "completed", "failed", "stopped"}:
                content = "No active run to pause."
            else:
                control["status"] = "pause_requested"
                content = "Pause requested; agent will pause at the next safe step boundary."
        elif command in {"resume", "resume_run"}:
            control["pause_requested"] = False
            control["status"] = "running" if control.get("run_id") else "idle"
            content = "Resume requested."
        elif command in {"stop", "stop_after_step", "cancel", "cancel_after_step"}:
            control["stop_requested"] = True
            if control.get("status") in {"idle", "completed", "failed", "stopped"}:
                control["status"] = "stopped"
                content = "No active run; marked stopped."
            else:
                control["status"] = "stop_requested"
                content = "Stop requested; agent will stop at the next safe step boundary."
        elif command in {"reassess", "reassess_after_step", "append_user_message"}:
            msg = str(data.get("content") or data.get("message") or "").strip()
            if msg:
                control.setdefault("pending_user_messages", []).append({
                    "content": msg,
                    "sender": data.get("sender") or participant_id,
                    "visual_context": data.get("visual_context") if isinstance(data.get("visual_context"), dict) else {},
                    "created_at": now,
                })
                control["reassess_requested"] = True
                if control.get("status") == "idle":
                    content = "Reassessment message queued, but no run is active."
                else:
                    content = "Reassessment queued; agent will incorporate the message at the next safe checkpoint."
            else:
                content = "No reassessment message content supplied."
        elif command in {"state", "state_request", "status"}:
            content = "Agent control state."
        else:
            await self.manager.send_message_to_session({"type": "error", "content": f"Unsupported agent control command: {command}", "timestamp": now, "session_id": session_id}, session_id)
            return

        await self._broadcast_run_control(session_id, control, content=content)

    async def _handle_chat_message(self, content: str, session_id: str, sender: str = "user", visual_context: Optional[Dict[str, Any]] = None):
        runtime = self.manager.get_runtime(session_id)
        if not runtime:
            await self.manager.send_message_to_session(
                {"type": "error", "content": "No runtime configured.", "timestamp": datetime.now().isoformat(), "session_id": session_id},
                session_id,
            )
            return

        await self.manager.send_message_to_session(
            {"type": "user_echo", "content": content, "sender": sender, "timestamp": datetime.now().isoformat(), "session_id": session_id},
            session_id,
        )

        visual_context = visual_context if isinstance(visual_context, dict) else {}
        workflow_content = content
        if visual_context:
            try:
                vc_text = json.dumps(visual_context, indent=2, sort_keys=True)[:60000]
            except Exception:
                vc_text = str(visual_context)[:60000]
            workflow_content = (
                f"{content}\n\n"
                "[Wolf GUI visual workspace context attached by the user. "
                "Use this context when answering questions about what is visible in the GUI. "
                "If capture_capabilities says cross-origin iframe pixels/DOM are unavailable, explain that limitation and use available metadata.]\n"
                f"{vc_text}"
            )

        # Deferred GUI capture commands complete after the workflow step that
        # requested them.  Store their image artifacts in the runtime, then
        # attach them to the next agent turn as normal multimodal <input>
        # references so a vision-capable model can inspect actual pixels.
        pending_capture_artifacts = runtime.pop("pending_gui_capture_artifacts", []) or []
        if pending_capture_artifacts:
            artifact_lines = [
                "",
                "[Deferred GUI capture artifact(s) from the previous GUI command result are attached below. "
                "Use these image pixels to answer the user's question about what is visible. "
                "If your model lacks vision capability, report the artifact metadata and image path instead.]",
            ]
            for idx, artifact in enumerate(pending_capture_artifacts, start=1):
                if not isinstance(artifact, dict):
                    continue
                compact = {
                    k: artifact.get(k)
                    for k in ("capture_id", "source_url", "image_path", "metadata_path", "width", "height", "status", "ok", "error")
                    if artifact.get(k) is not None
                }
                try:
                    artifact_lines.append(f"capture_artifact_{idx}: {json.dumps(compact, sort_keys=True)}")
                except Exception:
                    artifact_lines.append(f"capture_artifact_{idx}: {compact}")
                image_path = str(artifact.get("image_path") or "").strip()
                if image_path:
                    artifact_lines.append(f"<input> {image_path} </input>")
            workflow_content = f"{workflow_content}\n" + "\n".join(artifact_lines)

        config = runtime.get("config", {}) or {}
        action_names = self._resolve_action_names(config)
        execution_policy = self._resolve_execution_policy(config)
        gui_route = runtime.get("gui_route") or {}
        execution_policy["gui_action_route"] = gui_route.get("route") or config.get("gui_action_route") or "direct"
        execution_policy["gui_url"] = gui_route.get("gui_url") or config.get("gui_url")
        execution_policy["gui_api_reachable"] = gui_route.get("reachable")
        mode = config.get("mode") or GATEWAY_DEFAULT_MODE
        max_steps = int(config.get("max_steps") or GATEWAY_DEFAULT_MAX_STEPS)

        await self.manager.send_message_to_session(
            {
                "type": "policy_resolved",
                "content": f"Gateway policy={config.get('action_policy', 'limited')} actions={action_names}",
                "action_policy": config.get("action_policy", "limited"),
                "resolved_action_names": action_names,
                "resolved_execution_policy": execution_policy,
                "visual_context_attached": bool(visual_context),
                "visual_context_schema": visual_context.get("schema_version") if isinstance(visual_context, dict) else None,
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id,
            },
            session_id,
        )

        control = self._run_control_for(runtime)
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        control.update({
            "run_id": run_id,
            "status": "running",
            "pause_requested": False,
            "stop_requested": False,
            "reassess_requested": False,
            "pending_user_messages": [],
            "step": 0,
            "updated_at": datetime.now().isoformat(),
        })
        await self._broadcast_run_control(session_id, control, content="Agent run started.")

        try:
            async with runtime["lock"]:
                wf: GatewayActionWorkflow = runtime["wf"]
                events = await wf.process_user_message(
                    workflow_content,
                    user_name="user",
                    action_names=action_names,
                    mode=mode,
                    max_steps=max_steps,
                    log_console=False,
                    execution_policy=execution_policy,
                    control_state=control,
                )
            for event in events:
                if event.get("step") is not None:
                    try:
                        control["step"] = max(int(control.get("step") or 0), int(event.get("step") or 0))
                    except Exception:
                        pass
                event.setdefault("session_id", session_id)
                await self.manager.send_message_to_session(event, session_id)
                gui_command = self._gui_command_from_workflow_event(event)
                if gui_command:
                    gui_command.setdefault("session_id", session_id)
                    try:
                        pending = runtime.setdefault("pending_gui_commands", {})
                        command_id = gui_command.get("command_id")
                        if command_id:
                            pending[command_id] = {
                                "command_id": command_id,
                                "action": gui_command.get("action"),
                                "payload": gui_command.get("payload") if isinstance(gui_command.get("payload"), dict) else {},
                                "workflow_event": event,
                                "auto_continue": self._should_auto_continue_gui_command(gui_command.get("action")),
                                "created_at": datetime.now().isoformat(),
                            }
                    except Exception:
                        pass
                    await self.manager.send_message_to_session(gui_command, session_id)
            stop_reason = None
            for event in reversed(events):
                if isinstance(event, dict) and event.get("type") == "workflow_status" and event.get("status") == "done":
                    stop_reason = str(event.get("stop_reason") or "").strip().lower()
                    break
            saw_workflow_error = any(isinstance(event, dict) and event.get("type") == "workflow_error" for event in events)

            if control.get("status") not in {"stopped", "failed"}:
                if stop_reason == "error" or saw_workflow_error:
                    control["status"] = "failed"
                else:
                    control["status"] = "completed"
            control["pause_requested"] = False
            control["stop_requested"] = False
            control["reassess_requested"] = False
            control["run_id"] = None
            control["updated_at"] = datetime.now().isoformat()
            final_content = "Agent run failed." if control.get("status") == "failed" else "Agent run complete."
            await self._broadcast_run_control(session_id, control, content=final_content)
        except Exception as e:
            control["status"] = "failed"
            control["run_id"] = None
            control["updated_at"] = datetime.now().isoformat()
            try:
                await self._broadcast_run_control(session_id, control, content=f"Agent run failed: {str(e)}")
            except Exception:
                pass
            await self.manager.send_message_to_session(
                {"type": "error", "content": f"Workflow error: {str(e)}", "timestamp": datetime.now().isoformat(), "session_id": session_id},
                session_id,
            )

    async def _handle_participant_message(self, data: Dict[str, Any], session_id: str, participant_id: str):
        """Broadcast non-orchestrating participant messages into the shared session.

        This is the first join-session primitive: joined entities can exchange
        messages with the session without yet taking over the workflow action
        loop. Agent-only bridge clients can use this for observation, testing,
        and coordination.
        """
        content = data.get("content", "")
        msg = {
            "type": "participant_message",
            "content": content,
            "sender": data.get("sender") or participant_id,
            "participant_id": participant_id,
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
        }
        await self.manager.send_message_to_session(msg, session_id)

    def run(self):
        console.print(f"[*] Starting WOLF Gateway V3 on {self.host}:{self.port}")
        uvicorn.run(self.app, host=self.host, port=self.port)


if __name__ == "__main__":
    gateway = WolfGateway()
    gateway.run()
