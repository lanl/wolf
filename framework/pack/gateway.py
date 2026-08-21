"""FastAPI Gateway V3 for WOLF workflow/action interaction.

The gateway is intentionally a transport/session layer. It owns auth,
websocket fanout, account/session ownership, runtime registry, and per-session
locks. WOLF orchestration lives in GatewayActionWorkflow.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import json
import re
import os
import secrets
import traceback
import urllib.error
import urllib.request
from urllib.parse import urlparse
import uuid
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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
from framework.gui.capture_policy import CapturePolicy
from framework.gui.capture_storage import CaptureStorage
from framework.pack.orchestration_session import GatewayOrchestrationSession
from framework.pack.infrastructure_snapshot import build_infrastructure_snapshot


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

GATEWAY_ORCHESTRATION_ACTIONS = {"create_subtasks", "wait_for_tasks", "complete_task", "publish_progress", "fail_task"}
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
    orchestration_enabled: bool = False
    orchestration_worker_count: int = 1
    orchestration_max_active_tasks: int = 4
    orchestration_max_total_tasks: int = 128
    agent_profiles: List[Dict[str, Any]] = []
    agent_pool_mix: Dict[str, int] = {}
    agent_pool_scope: str = "session"
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
    gui_command_timeout_seconds: int = 60


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


class LiveGuiCaptureUpload(BaseModel):
    """Permissioned screenshot bytes captured in the user's live browser client."""

    image_data: str
    format: str = "png"
    capture_id: Optional[str] = None
    capture_scope: Optional[str] = None
    source_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
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


class CollaborationInviteCreate(BaseModel):
    """Request body for creating a limited collaboration invite."""

    role: str = "human"
    permissions: Optional[Dict[str, Any]] = None
    participant_id_hint: Optional[str] = None
    client_type_hint: Optional[str] = None
    expires_in_seconds: Optional[int] = 3600
    max_uses: int = 1
    metadata: Optional[Dict[str, Any]] = None


class JoinRequestDecision(BaseModel):
    """Owner/controller decision for a pending no-token join request."""

    role: Optional[str] = None
    permissions: Optional[Dict[str, Any]] = None
    expires_in_seconds: Optional[int] = 120
    reason: Optional[str] = None


class A2AHandshakeRequest(BaseModel):
    """Minimal A2A-compatible handshake for non-WOLF agents."""

    protocol: str = "a2a"
    protocol_version: Optional[str] = "0.1"
    session_id: str
    agent_id: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    capabilities: Optional[List[str]] = None
    endpoints: Optional[Dict[str, Any]] = None
    auth: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    invite_token: Optional[str] = None
    approval_token: Optional[str] = None
    join_request_id: Optional[str] = None
    request_approval: bool = False
    requested_role: Optional[str] = "assistant_agent"
    reason: Optional[str] = None


class A2AHeartbeatRequest(BaseModel):
    """Heartbeat/status update from an admitted A2A peer."""

    session_id: str
    peer_token: str
    status: Optional[str] = "active"
    load: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class A2AMessageRequest(BaseModel):
    """A2A message/send-compatible relay request with passive task/result metadata."""

    session_id: str
    peer_id: str
    peer_token: str
    content: Any = None
    kind: Optional[str] = None
    task_id: Optional[str] = None
    request_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    status: Optional[str] = None
    result: Optional[Any] = None
    artifacts: Optional[List[Dict[str, Any]]] = None
    to_participant_id: Optional[str] = None
    to_role: Optional[str] = None
    visibility: Optional[str] = None
    thread_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class A2APassiveTaskRequest(BaseModel):
    """Owner/controller-created passive request queued for an A2A peer."""

    content: Any
    task_id: Optional[str] = None
    request_id: Optional[str] = None
    thread_id: Optional[str] = None
    instructions: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


COLLABORATION_ROLES = {"owner", "controller", "human", "observer", "assistant_agent", "tool_agent", "gui_client", "tui_client", "gateway_web", "a2a_agent", "unknown"}
COLLABORATION_SECRET = "***REDACTED***"
COLLABORATION_DEFAULT_STATE_PATH = ".gateway/collaboration_state.json"
COLLABORATION_JOIN_RATE_LIMIT_WINDOW_SECONDS = 120
COLLABORATION_JOIN_RATE_LIMIT_MAX = 5
COLLABORATION_PENDING_TTL_SECONDS = 120
COLLABORATION_REST_CAPABILITY_RULES = [
    ("POST", re.compile(r"^/sessions/[^/]+/configure$"), "can_manage_session_params"),
    ("GET", re.compile(r"^/sessions/[^/]+/params$"), "can_manage_session_params"),
    ("PATCH", re.compile(r"^/sessions/[^/]+/params$"), "can_manage_session_params"),
    ("GET", re.compile(r"^/sessions/[^/]+/policy$"), "can_manage_policy"),
    ("GET", re.compile(r"^/sessions/[^/]+/participants$"), "can_read_session_events"),
    ("GET", re.compile(r"^/sessions/[^/]+/collaboration/snapshot$"), "can_read_session_events"),
    ("GET", re.compile(r"^/sessions/[^/]+/a2a/peers$"), "can_read_session_events"),
    ("POST", re.compile(r"^/sessions/[^/]+/a2a/peers/[^/]+/task-requests$"), "can_send_direct_message"),
    ("GET", re.compile(r"^/sessions/[^/]+/invites$"), "can_manage_invites"),
    ("POST", re.compile(r"^/sessions/[^/]+/invites$"), "can_manage_invites"),
    ("DELETE", re.compile(r"^/sessions/[^/]+/invites/[^/]+$"), "can_manage_invites"),
    ("GET", re.compile(r"^/sessions/[^/]+/join-requests$"), "can_approve_join_requests"),
    ("POST", re.compile(r"^/sessions/[^/]+/join-requests/[^/]+/(approve|reject)$"), "can_approve_join_requests"),
    ("GET", re.compile(r"^/sessions/[^/]+/infrastructure/snapshot$"), "can_view_infrastructure_snapshot"),
    ("GET", re.compile(r"^/sessions/[^/]+/orchestration/snapshot$"), "can_request_orchestration_snapshot"),
    ("GET", re.compile(r"^/sessions/[^/]+/orchestration/agent_pool$"), "can_manage_agent_pool"),
    ("PATCH", re.compile(r"^/sessions/[^/]+/orchestration/agent_pool/mix$"), "can_manage_agent_pool"),
    ("GET", re.compile(r"^/sessions/[^/]+/orchestration/tasks/[^/]+$"), "can_request_orchestration_snapshot"),
    ("POST", re.compile(r"^/sessions/[^/]+/orchestration/tasks/[^/]+/.+$"), "can_manage_orchestration"),
    ("POST", re.compile(r"^/sessions/[^/]+/reset$"), "can_execute_agent_control"),
    ("POST", re.compile(r"^/api/gui/capture/(url|live|workspace)$"), "can_upload_gui_results"),
    ("GET", re.compile(r"^/api/gui/capture/[^/]+$"), "can_upload_gui_results"),
]


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
        self.session_invites: Dict[str, Dict[str, Any]] = {}
        self.join_requests: Dict[str, Dict[str, Any]] = {}
        self.a2a_peers: Dict[str, Dict[str, Any]] = {}
        self.a2a_task_queues: Dict[str, List[Dict[str, Any]]] = {}
        self.collaboration_audit_events: Dict[str, List[Dict[str, Any]]] = {}
        self.pending_join_sockets: Dict[str, WebSocket] = {}
        self.join_request_approval_tokens: Dict[str, str] = {}
        self.join_request_attempts: Dict[str, List[float]] = {}
        self.collaboration_state_path = Path(os.environ.get("WOLF_GATEWAY_COLLAB_STATE", COLLABORATION_DEFAULT_STATE_PATH))
        self._load_collaboration_state()
        self.orchestration_resolve_action_names = None
        self.orchestration_resolve_execution_policy = None
        self.orchestration_gui_command_from_workflow_event = None
        self.orchestration_should_auto_continue_gui_command = None
        self.default_agent_config = self._merged_default_agent_config(default_agent_config)

    def _load_collaboration_state(self) -> None:
        """Load persisted collaboration invite/join metadata without raw secrets."""
        try:
            if not self.collaboration_state_path.exists():
                return
            payload = json.loads(self.collaboration_state_path.read_text(encoding="utf-8"))
            if isinstance(payload.get("session_invites"), dict):
                self.session_invites.update(payload.get("session_invites") or {})
            if isinstance(payload.get("join_requests"), dict):
                self.join_requests.update(payload.get("join_requests") or {})
            if isinstance(payload.get("a2a_peers"), dict):
                self.a2a_peers.update(payload.get("a2a_peers") or {})
            if isinstance(payload.get("a2a_task_queues"), dict):
                self.a2a_task_queues.update(payload.get("a2a_task_queues") or {})
            if isinstance(payload.get("collaboration_audit_events"), dict):
                self.collaboration_audit_events.update(payload.get("collaboration_audit_events") or {})
            self.cleanup_expired_collaboration_state(persist=False)
        except Exception as exc:
            console.print(f"[!] Could not load collaboration state: {exc}")

    def _persist_collaboration_state(self) -> None:
        """Persist redacted collaboration state atomically enough for local Gateway use."""
        try:
            self.collaboration_state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "type": "gateway_collaboration_state",
                "version": 1,
                "updated_at": datetime.now().isoformat(),
                "session_invites": self.session_invites,
                "join_requests": self.join_requests,
                "a2a_peers": self.a2a_peers,
                "a2a_task_queues": self.a2a_task_queues,
                "collaboration_audit_events": self.collaboration_audit_events,
            }
            tmp = self.collaboration_state_path.with_suffix(self.collaboration_state_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
            tmp.replace(self.collaboration_state_path)
        except Exception as exc:
            console.print(f"[!] Could not persist collaboration state: {exc}")

    def cleanup_expired_collaboration_state(self, *, persist: bool = True) -> Dict[str, int]:
        """Mark expired pending/approved requests and invites; prune stale rate buckets."""
        now = datetime.now()
        changed = False
        stats = {"expired_join_requests": 0, "expired_invites": 0, "rate_buckets_pruned": 0}
        for req in self.join_requests.values():
            status = str(req.get("status") or "")
            expires_at = req.get("expires_at")
            if status in {"pending", "approved"} and expires_at:
                try:
                    if datetime.fromisoformat(str(expires_at)) < now:
                        req["status"] = "expired"
                        req["expired_at"] = now.isoformat()
                        stats["expired_join_requests"] += 1
                        changed = True
                except Exception:
                    pass
        for invite in self.session_invites.values():
            expires_at = invite.get("expires_at")
            if expires_at and not invite.get("expired_at"):
                try:
                    if datetime.fromisoformat(str(expires_at)) < now:
                        invite["expired_at"] = now.isoformat()
                        stats["expired_invites"] += 1
                        changed = True
                except Exception:
                    pass
        cutoff = time.time() - COLLABORATION_JOIN_RATE_LIMIT_WINDOW_SECONDS
        for key in list(self.join_request_attempts.keys()):
            kept = [t for t in self.join_request_attempts.get(key, []) if t >= cutoff]
            if kept:
                self.join_request_attempts[key] = kept
            else:
                self.join_request_attempts.pop(key, None)
                stats["rate_buckets_pruned"] += 1
        if changed and persist:
            self._persist_collaboration_state()
        return stats

    def _check_join_request_rate_limit(self, *, session_id: str, requested_participant_id: str, origin: Optional[Dict[str, Any]] = None) -> None:
        now = time.time()
        origin_key = str((origin or {}).get("client") or (origin or {}).get("account_path") or "unknown")[:80]
        key = f"{session_id}:{requested_participant_id}:{origin_key}"
        cutoff = now - COLLABORATION_JOIN_RATE_LIMIT_WINDOW_SECONDS
        attempts = [t for t in self.join_request_attempts.get(key, []) if t >= cutoff]
        if len(attempts) >= COLLABORATION_JOIN_RATE_LIMIT_MAX:
            raise PermissionError("join request rate limit exceeded")
        attempts.append(now)
        self.join_request_attempts[key] = attempts

    def participant_can(self, session_id: str, participant_id: Optional[str], capability: str) -> bool:
        if not participant_id:
            return True
        participant = (self.session_participants.get(session_id) or {}).get(participant_id)
        if not participant:
            return False
        return bool((participant.get("permissions") or {}).get(capability, False))

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

    def _safe_participant_id(self, value: Optional[str], *, prefix: str = "participant") -> str:
        raw = str(value or "").strip() or f"{prefix}_{uuid.uuid4().hex[:8]}"
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
        return (safe or f"{prefix}_{uuid.uuid4().hex[:8]}")[:80]

    def _normalize_collaboration_role(self, role: Optional[str]) -> str:
        candidate = str(role or "human").strip().lower()
        return candidate if candidate in COLLABORATION_ROLES else "human"

    def collaboration_permissions_for_role(self, role: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        role = self._normalize_collaboration_role(role)
        perms = {
            "can_read_session_events": True,
            "can_send_participant_message": role not in {"unknown"},
            "can_send_direct_message": role in {"owner", "controller", "human", "assistant_agent", "tool_agent", "gui_client", "tui_client", "gateway_web", "a2a_agent"},
            "can_receive_direct_message": role not in {"unknown"},
            "can_send_chat_to_agent": role in {"owner", "controller", "human", "gateway_web", "tui_client"},
            "can_request_orchestration_snapshot": role in {"owner", "controller", "human", "gateway_web", "tui_client"},
            "can_view_infrastructure_snapshot": role in {"owner", "controller"},
            "can_execute_agent_control": role in {"owner", "controller", "gateway_web"},
            "can_manage_orchestration": role in {"owner", "controller", "gateway_web"},
            "can_manage_agent_pool": role in {"owner", "controller", "gateway_web"},
            "can_manage_session_params": role in {"owner", "controller"},
            "can_manage_policy": role in {"owner"},
            "can_manage_invites": role in {"owner", "controller"},
            "can_approve_join_requests": role in {"owner", "controller"},
            "can_upload_gui_results": role in {"owner", "controller", "gui_client", "gateway_web"},
        }
        if role == "observer":
            perms.update({"can_send_participant_message": False, "can_send_direct_message": False, "can_send_chat_to_agent": False})
        if role in {"assistant_agent", "tool_agent", "a2a_agent"}:
            perms.update({"can_send_chat_to_agent": False, "can_request_orchestration_snapshot": False})
        if overrides:
            for key, value in overrides.items():
                if key.startswith("can_"):
                    perms[key] = bool(value)
        return perms

    def _hash_collaboration_token(self, token: str) -> str:
        return hashlib.sha256(str(token or "").encode()).hexdigest()

    def _invite_public(self, invite: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: v for k, v in invite.items() if k != "token_hash"}
        out["invite_token"] = COLLABORATION_SECRET
        return out

    def create_invite(self, *, session_id: str, owner_account_id: str, body: Dict[str, Any], gateway_url: Optional[str] = None, created_by_participant_id: Optional[str] = None) -> Dict[str, Any]:
        role = self._normalize_collaboration_role(body.get("role") or "human")
        max_uses = max(1, int(body.get("max_uses") or 1))
        ttl = body.get("expires_in_seconds", 3600)
        expires_at = None
        if ttl is not None:
            expires_at = (datetime.now() + timedelta(seconds=max(1, int(ttl)))).isoformat()
        raw_token = secrets.token_urlsafe(32)
        invite_id = f"inv_{uuid.uuid4().hex[:12]}"
        invite = {
            "invite_id": invite_id,
            "session_id": session_id,
            "owner_account_id": owner_account_id,
            "token_hash": self._hash_collaboration_token(raw_token),
            "token_preview": f"{raw_token[:4]}...{raw_token[-4:]}",
            "role": role,
            "permissions": self.collaboration_permissions_for_role(role, body.get("permissions") if isinstance(body.get("permissions"), dict) else None),
            "participant_id_hint": body.get("participant_id_hint"),
            "client_type_hint": body.get("client_type_hint"),
            "max_uses": max_uses,
            "used_count": 0,
            "expires_at": expires_at,
            "created_at": datetime.now().isoformat(),
            "created_by_participant_id": created_by_participant_id,
            "revoked": False,
            "revoked_at": None,
            "metadata": body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
        }
        self.session_invites[invite_id] = invite
        self._persist_collaboration_state()
        base = (gateway_url or "").rstrip("/")
        invite_url = f"wolf://join?gateway={base}&session_id={session_id}&invite_token={raw_token}&role={role}" if base else None
        command = f"./wolf join-session --gateway {base or '<gateway>'} --session-id {session_id} --invite-token {raw_token} --role {role}"
        return {**self._invite_public(invite), "invite_token": raw_token, "invite_url": invite_url, "command": command}

    def list_invites(self, session_id: str) -> List[Dict[str, Any]]:
        self.cleanup_expired_collaboration_state()
        now = datetime.now()
        rows = []
        for invite in self.session_invites.values():
            if invite.get("session_id") != session_id:
                continue
            row = self._invite_public(invite)
            expires_at = row.get("expires_at")
            expired = False
            if expires_at:
                try:
                    expired = datetime.fromisoformat(str(expires_at)) < now
                except Exception:
                    expired = False
            row["expired"] = expired
            row["status"] = "revoked" if row.get("revoked") else ("expired" if expired else "active")
            rows.append(row)
        return sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)

    def revoke_invite(self, session_id: str, invite_id: str) -> Dict[str, Any]:
        invite = self.session_invites.get(invite_id)
        if not invite or invite.get("session_id") != session_id:
            raise KeyError(invite_id)
        invite["revoked"] = True
        invite["revoked_at"] = datetime.now().isoformat()
        self._persist_collaboration_state()
        return self._invite_public(invite)

    def validate_invite_token(self, session_id: str, invite_token: str, requested_role: Optional[str] = None) -> Dict[str, Any]:
        self.cleanup_expired_collaboration_state()
        token_hash = self._hash_collaboration_token(invite_token)
        for invite in self.session_invites.values():
            if invite.get("session_id") != session_id or invite.get("token_hash") != token_hash:
                continue
            if invite.get("revoked"):
                raise ValueError("invite revoked")
            expires_at = invite.get("expires_at")
            if expires_at and datetime.fromisoformat(str(expires_at)) < datetime.now():
                raise ValueError("invite expired")
            if int(invite.get("used_count") or 0) >= int(invite.get("max_uses") or 1):
                raise ValueError("invite max uses exceeded")
            role = self._normalize_collaboration_role(requested_role or invite.get("role"))
            if role != self._normalize_collaboration_role(invite.get("role")) and invite.get("role") not in {"controller", "owner"}:
                role = self._normalize_collaboration_role(invite.get("role"))
            invite["used_count"] = int(invite.get("used_count") or 0) + 1
            self._persist_collaboration_state()
            return {"invite": self._invite_public(invite), "role": role, "owner_account_id": invite.get("owner_account_id"), "permissions": invite.get("permissions") or self.collaboration_permissions_for_role(role)}
        raise ValueError("invalid invite token")

    def create_join_request(self, *, session_id: str, requested_participant_id: Optional[str], requested_role: Optional[str], client_type: str, reason: Optional[str], origin: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.cleanup_expired_collaboration_state()
        if session_id not in self.sessions:
            raise KeyError(session_id)
        safe_requested_id = self._safe_participant_id(requested_participant_id, prefix=client_type or "guest")
        self._check_join_request_rate_limit(session_id=session_id, requested_participant_id=safe_requested_id, origin=origin)
        request_id = f"join_{uuid.uuid4().hex[:12]}"
        request_poll_token = secrets.token_urlsafe(24)
        req = {
            "request_id": request_id,
            "session_id": session_id,
            "owner_account_id": self.sessions[session_id].account_id,
            "requested_participant_id": safe_requested_id,
            "requested_role": self._normalize_collaboration_role(requested_role or "human"),
            "client_type": client_type or "unknown",
            "reason": reason,
            "origin": origin or {},
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(seconds=COLLABORATION_PENDING_TTL_SECONDS)).isoformat(),
            "decided_at": None,
            "decided_by_participant_id": None,
            "approval_token_hash": None,
            "approval_token_preview": None,
            "approval_token_used_at": None,
            "approval_token_used_by": None,
            "request_poll_token_hash": self._hash_collaboration_token(request_poll_token),
            "request_poll_token_preview": f"{request_poll_token[:4]}...{request_poll_token[-4:]}",
            "approved_role": None,
            "permissions": None,
        }
        self.join_requests[request_id] = req
        self._persist_collaboration_state()
        return {k: v for k, v in req.items() if k not in {"approval_token_hash", "request_poll_token_hash"}} | {"request_poll_token": request_poll_token}

    def list_join_requests(self, session_id: str) -> List[Dict[str, Any]]:
        self.cleanup_expired_collaboration_state()
        rows = []
        now = datetime.now()
        for req in self.join_requests.values():
            if req.get("session_id") != session_id:
                continue
            row = {k: v for k, v in req.items() if k not in {"approval_token_hash", "request_poll_token_hash"}}
            if row.get("status") == "pending" and row.get("expires_at"):
                try:
                    if datetime.fromisoformat(str(row.get("expires_at"))) < now:
                        row["status"] = "expired"
                        req["status"] = "expired"
                except Exception:
                    pass
            rows.append(row)
        return sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)

    def approve_join_request(self, session_id: str, request_id: str, *, decided_by_participant_id: Optional[str] = None, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        req = self.join_requests.get(request_id)
        if not req or req.get("session_id") != session_id:
            raise KeyError(request_id)
        if req.get("status") not in {"pending", "approved"}:
            raise ValueError(f"cannot approve request in status {req.get('status')}")
        body = body or {}
        ttl = max(30, int(body.get("expires_in_seconds") or 120))
        token = secrets.token_urlsafe(32)
        role = self._normalize_collaboration_role(body.get("role") or req.get("requested_role"))
        req.update({
            "status": "approved",
            "decided_at": datetime.now().isoformat(),
            "decided_by_participant_id": decided_by_participant_id,
            "approval_token_hash": self._hash_collaboration_token(token),
            "approval_token_preview": f"{token[:4]}...{token[-4:]}",
            "approved_role": role,
            "permissions": self.collaboration_permissions_for_role(role, body.get("permissions") if isinstance(body.get("permissions"), dict) else None),
            "expires_at": (datetime.now() + timedelta(seconds=ttl)).isoformat(),
        })
        self.join_request_approval_tokens[request_id] = token
        self._persist_collaboration_state()
        return {k: v for k, v in req.items() if k not in {"approval_token_hash", "request_poll_token_hash"}} | {"approval_token": token}

    def reject_join_request(self, session_id: str, request_id: str, *, decided_by_participant_id: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        req = self.join_requests.get(request_id)
        if not req or req.get("session_id") != session_id:
            raise KeyError(request_id)
        req.update({"status": "rejected", "decided_at": datetime.now().isoformat(), "decided_by_participant_id": decided_by_participant_id, "rejection_reason": reason})
        self._persist_collaboration_state()
        return {k: v for k, v in req.items() if k not in {"approval_token_hash", "request_poll_token_hash"}}

    def a2a_join_request_status(self, *, session_id: str, request_id: str, request_poll_token: str, consume_approval_token: bool = True) -> Dict[str, Any]:
        self.cleanup_expired_collaboration_state()
        req = self.join_requests.get(request_id)
        if not req or req.get("session_id") != session_id:
            raise ValueError("invalid join request")
        if not req.get("request_poll_token_hash") or req.get("request_poll_token_hash") != self._hash_collaboration_token(request_poll_token):
            raise ValueError("invalid join request poll token")
        safe = {k: v for k, v in req.items() if k not in {"approval_token_hash", "request_poll_token_hash"}}
        approval_token = None
        if req.get("status") == "approved":
            approval_token = self.join_request_approval_tokens.get(request_id)
            if approval_token and consume_approval_token:
                self.join_request_approval_tokens.pop(request_id, None)
        return {"session_id": session_id, "request_id": request_id, "status": req.get("status"), "join_request": safe, "approval_token": approval_token}

    def _a2a_capabilities(self, capabilities: Optional[List[str]]) -> List[str]:
        allowed = {"text", "text_generation", "structured_output", "tool_use", "vision", "code", "planning", "retrieval", "jsonrpc", "message_send"}
        out = []
        for item in capabilities or []:
            cap = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(item or "").strip().lower())[:80]
            if cap and (cap in allowed or len(out) < 16):
                out.append(cap)
        return sorted(set(out))

    def _a2a_peer_public(self, peer: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: v for k, v in peer.items() if k not in {"peer_token_hash"}}
        out["peer_token"] = COLLABORATION_SECRET
        return out

    def list_a2a_peers(self, session_id: str) -> List[Dict[str, Any]]:
        rows = [self._a2a_peer_public(p) for p in self.a2a_peers.values() if p.get("session_id") == session_id]
        return sorted(rows, key=lambda r: str(r.get("connected_at") or r.get("created_at") or ""), reverse=True)

    def register_a2a_peer(self, *, session_id: str, owner_account_id: str, handshake: Dict[str, Any], auth_mode: str, role: str, permissions: Optional[Dict[str, Any]] = None, invite_id: Optional[str] = None, approval_request_id: Optional[str] = None) -> Dict[str, Any]:
        safe_agent_id = self._safe_participant_id(handshake.get("agent_id") or handshake.get("display_name"), prefix="a2a")
        participant_id = self._safe_participant_id(safe_agent_id, prefix="a2a")
        peer_id = f"a2a_{uuid.uuid4().hex[:12]}"
        raw_peer_token = secrets.token_urlsafe(32)
        now = datetime.now().isoformat()
        role = self._normalize_collaboration_role(role or "a2a_agent")
        if role in {"human", "unknown"}:
            role = "a2a_agent"
        base_perms = self.collaboration_permissions_for_role(role)
        if permissions and role == "a2a_agent":
            # A2A peers may be admitted via human/controller-shaped invites, but
            # non-WOLF agents should remain passive by default and must not inherit
            # broader chat/control powers accidentally.  Keep only capabilities
            # allowed by both the source auth grant and the A2A role baseline.
            perms = {k: bool(base_perms.get(k, False) and permissions.get(k, False)) for k in set(base_perms) | set(permissions)}
        else:
            perms = permissions or base_perms
        peer = {
            "peer_id": peer_id,
            "session_id": session_id,
            "owner_account_id": owner_account_id,
            "participant_id": participant_id,
            "agent_id": safe_agent_id,
            "display_name": str(handshake.get("display_name") or safe_agent_id)[:120],
            "description": str(handshake.get("description") or "")[:500],
            "protocol": str(handshake.get("protocol") or "a2a")[:40],
            "protocol_version": str(handshake.get("protocol_version") or "0.1")[:40],
            "capabilities": self._a2a_capabilities(handshake.get("capabilities") if isinstance(handshake.get("capabilities"), list) else []),
            "endpoints": self._redact_a2a_metadata(handshake.get("endpoints") if isinstance(handshake.get("endpoints"), dict) else {}),
            "metadata": self._redact_a2a_metadata(handshake.get("metadata") if isinstance(handshake.get("metadata"), dict) else {}),
            "auth_mode": auth_mode,
            "invite_id": invite_id,
            "approval_request_id": approval_request_id,
            "role": role,
            "permissions": perms,
            "status": "active",
            "connected_at": now,
            "last_seen_at": now,
            "last_heartbeat_at": None,
            "last_message_at": None,
            "peer_token_hash": self._hash_collaboration_token(raw_peer_token),
            "peer_token_preview": f"{raw_peer_token[:4]}...{raw_peer_token[-4:]}",
            "locality": "remote_agent",
            "entity_type": "a2a_agent",
        }
        self.a2a_peers[peer_id] = peer
        self.session_participants.setdefault(session_id, {})[participant_id] = {
            "participant_id": participant_id,
            "display_name": peer["display_name"],
            "role": role,
            "participant_role": role,
            "client_type": "a2a",
            "entity_type": "a2a_agent",
            "auth_mode": auth_mode,
            "account_id": None,
            "invite_id": invite_id,
            "approval_request_id": approval_request_id,
            "a2a_peer_id": peer_id,
            "a2a_agent_id": safe_agent_id,
            "capabilities": peer["capabilities"],
            "permissions": perms,
            "join_mode": "a2a",
            "agent_collaboration": {"mode": "a2a", "active_enabled": False, "passive_enabled": True},
            "connected_at": now,
            "last_seen_at": now,
            "active": True,
            "locality": "remote_agent",
        }
        self._persist_collaboration_state()
        return self._a2a_peer_public(peer) | {"peer_token": raw_peer_token}

    def _redact_a2a_metadata(self, value: Any) -> Any:
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                kl = str(k).lower()
                if any(secret in kl for secret in ("token", "secret", "password", "authorization", "api_key")):
                    out[k] = COLLABORATION_SECRET if v not in (None, "") else v
                else:
                    out[k] = self._redact_a2a_metadata(v)
            return out
        if isinstance(value, list):
            return [self._redact_a2a_metadata(v) for v in value[:50]]
        return value

    def record_collaboration_audit_event(
        self,
        session_id: str,
        event_type: str,
        *,
        message: Optional[str] = None,
        severity: str = "info",
        actor_participant_id: Optional[str] = None,
        actor_peer_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append a redacted collaboration audit event for snapshots/UI history."""
        safe_type = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(event_type or "collaboration_event"))[:120]
        safe_severity = str(severity or "info").lower()
        if safe_severity not in {"info", "warning", "error"}:
            safe_severity = "info"
        event = {
            "event_id": f"audit_{uuid.uuid4().hex[:12]}",
            "type": safe_type,
            "severity": safe_severity,
            "session_id": session_id,
            "message": str(message or safe_type)[:1000],
            "actor_participant_id": actor_participant_id,
            "actor_peer_id": actor_peer_id,
            "metadata": self._redact_a2a_metadata(metadata or {}),
            "timestamp": datetime.now().isoformat(),
        }
        rows = self.collaboration_audit_events.setdefault(session_id, [])
        rows.append(event)
        if len(rows) > 300:
            del rows[:-300]
        self._persist_collaboration_state()
        return event

    def list_collaboration_audit_events(self, session_id: str, limit: int = 80) -> List[Dict[str, Any]]:
        rows = list(self.collaboration_audit_events.get(session_id, []) or [])
        rows = sorted(rows, key=lambda r: str(r.get("timestamp") or ""), reverse=True)
        return rows[: max(1, min(int(limit or 80), 300))]

    def collaboration_audit_summary(self, session_id: str) -> Dict[str, Any]:
        rows = list(self.collaboration_audit_events.get(session_id, []) or [])
        counts_by_type: Dict[str, int] = {}
        severity_counts: Dict[str, int] = {}
        for ev in rows:
            t = str(ev.get("type") or "event")
            sev = str(ev.get("severity") or "info")
            counts_by_type[t] = counts_by_type.get(t, 0) + 1
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        return {
            "total_events": len(rows),
            "counts_by_type": counts_by_type,
            "severity_counts": severity_counts,
            "recent_events": self.list_collaboration_audit_events(session_id, limit=12),
        }

    def validate_a2a_peer_token(self, session_id: str, peer_id: str, peer_token: str) -> Dict[str, Any]:
        peer = self.a2a_peers.get(peer_id)
        if not peer or peer.get("session_id") != session_id:
            raise ValueError("invalid A2A peer")
        if peer.get("peer_token_hash") != self._hash_collaboration_token(peer_token):
            raise ValueError("invalid A2A peer token")
        return peer

    def update_a2a_heartbeat(self, *, session_id: str, peer_id: str, peer_token: str, status: Optional[str] = None, load: Optional[Dict[str, Any]] = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        peer = self.validate_a2a_peer_token(session_id, peer_id, peer_token)
        now = datetime.now().isoformat()
        peer["last_seen_at"] = now
        peer["last_heartbeat_at"] = now
        peer["status"] = str(status or "active")[:40]
        if isinstance(load, dict):
            peer["load"] = self._redact_a2a_metadata(load)
        if isinstance(metadata, dict):
            peer["metadata"] = {**(peer.get("metadata") or {}), **self._redact_a2a_metadata(metadata)}
        participant_id = peer.get("participant_id")
        participant = (self.session_participants.get(session_id) or {}).get(participant_id)
        if participant:
            participant["last_seen_at"] = now
            participant["active"] = peer["status"] not in {"offline", "disconnected"}
        self._persist_collaboration_state()
        return self._a2a_peer_public(peer)

    def normalize_a2a_message_kind(self, kind: Optional[str], status: Optional[str] = None, result: Optional[Any] = None) -> str:
        raw = str(kind or "").strip().lower().replace("-", "_").replace("/", "_")
        aliases = {
            "": "message",
            "message_send": "message",
            "task_request": "task_request",
            "request": "task_request",
            "task": "task_request",
            "task_result": "task_result",
            "result": "task_result",
            "response": "task_result",
            "task_response": "task_result",
            "status": "status",
            "progress": "status",
            "error": "error",
            "failure": "error",
        }
        normalized = aliases.get(raw, "message")
        if normalized == "message" and result is not None:
            normalized = "task_result"
        if normalized == "message" and status and str(status).lower() in {"running", "completed", "failed", "blocked", "working"}:
            normalized = "status"
        return normalized

    def normalize_a2a_content(self, value: Any) -> str:
        if isinstance(value, str):
            return value[:12000]
        if value is None:
            return ""
        return json.dumps(self._redact_a2a_metadata(value), sort_keys=True, default=str)[:12000]

    def normalize_a2a_artifacts(self, artifacts: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        rows = []
        for artifact in artifacts or []:
            if not isinstance(artifact, dict):
                continue
            safe = self._redact_a2a_metadata(artifact)
            rows.append({
                "artifact_id": str(safe.get("artifact_id") or safe.get("id") or f"artifact_{uuid.uuid4().hex[:8]}")[:120],
                "name": str(safe.get("name") or safe.get("title") or "artifact")[:200],
                "mime_type": str(safe.get("mime_type") or safe.get("type") or "application/octet-stream")[:120],
                "uri": safe.get("uri") or safe.get("url"),
                "summary": str(safe.get("summary") or safe.get("description") or "")[:1000],
                "metadata": self._redact_a2a_metadata(safe.get("metadata") if isinstance(safe.get("metadata"), dict) else {}),
            })
        return rows[:20]

    def queue_a2a_task_request(self, *, session_id: str, peer_id: str, body: Dict[str, Any], actor_participant_id: Optional[str] = None) -> Dict[str, Any]:
        peer = self.a2a_peers.get(peer_id)
        if not peer or peer.get("session_id") != session_id:
            raise ValueError("invalid A2A peer")
        if not (peer.get("permissions") or {}).get("can_receive_direct_message", False):
            raise PermissionError("A2A peer cannot receive direct task requests")
        request_id = str(body.get("request_id") or f"a2atask_{uuid.uuid4().hex[:12]}")[:120]
        content = self.normalize_a2a_content(body.get("content"))
        now = datetime.now().isoformat()
        item = {
            "type": "a2a_task_request",
            "request_id": request_id,
            "task_id": str(body.get("task_id") or "")[:120] or None,
            "thread_id": str(body.get("thread_id") or request_id)[:120],
            "session_id": session_id,
            "peer_id": peer_id,
            "to_participant_id": peer.get("participant_id"),
            "from_participant_id": actor_participant_id,
            "content": content,
            "instructions": str(body.get("instructions") or "")[:4000] or None,
            "metadata": self._redact_a2a_metadata(body.get("metadata") if isinstance(body.get("metadata"), dict) else {}),
            "status": "queued",
            "created_at": now,
            "delivered_at": None,
        }
        queue = self.a2a_task_queues.setdefault(peer_id, [])
        queue.append(item)
        if len(queue) > 200:
            del queue[:-200]
        peer["last_task_request_at"] = now
        self._persist_collaboration_state()
        return item

    def list_a2a_task_requests(self, *, session_id: str, peer_id: str, peer_token: str, mark_delivered: bool = True, limit: int = 50) -> List[Dict[str, Any]]:
        peer = self.validate_a2a_peer_token(session_id, peer_id, peer_token)
        now = datetime.now().isoformat()
        rows = [dict(item) for item in self.a2a_task_queues.get(peer_id, []) if item.get("session_id") == session_id]
        rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)[: max(1, min(int(limit or 50), 200))]
        if mark_delivered:
            changed = False
            ids = {r.get("request_id") for r in rows}
            for item in self.a2a_task_queues.get(peer_id, []):
                if item.get("request_id") in ids and item.get("status") == "queued":
                    item["status"] = "delivered"
                    item["delivered_at"] = now
                    changed = True
            peer["last_seen_at"] = now
            if changed:
                self._persist_collaboration_state()
        return rows

    def collaboration_snapshot(self, session_id: str) -> Dict[str, Any]:
        """Return a redacted, read-only collaboration snapshot for a Gateway session."""
        participants = list((self.session_participants.get(session_id) or {}).values())
        invites = self.list_invites(session_id)
        join_requests = self.list_join_requests(session_id)
        a2a_peers = self.list_a2a_peers(session_id)
        audit_recent = self.list_collaboration_audit_events(session_id)
        audit_summary = self.collaboration_audit_summary(session_id)
        a2a_task_requests = [item for peer in a2a_peers for item in self.a2a_task_queues.get(peer.get("peer_id"), []) if item.get("session_id") == session_id]
        pending = [r for r in join_requests if str(r.get("status") or "").lower() == "pending"]
        active = [p for p in participants if p.get("active") is not False]
        role_counts: Dict[str, int] = {}
        auth_counts: Dict[str, int] = {}
        client_counts: Dict[str, int] = {}
        for row in participants:
            role_counts[str(row.get("role") or row.get("participant_role") or "unknown")] = role_counts.get(str(row.get("role") or row.get("participant_role") or "unknown"), 0) + 1
            auth_counts[str(row.get("auth_mode") or "unknown")] = auth_counts.get(str(row.get("auth_mode") or "unknown"), 0) + 1
            client_counts[str(row.get("client_type") or "unknown")] = client_counts.get(str(row.get("client_type") or "unknown"), 0) + 1
        warnings = []
        for req in pending:
            warnings.append({
                "severity": "warning",
                "code": "pending_join_request",
                "request_id": req.get("request_id"),
                "message": f"{req.get('requested_participant_id')} is waiting to join as {req.get('requested_role')}",
            })
        return {
            "type": "collaboration_snapshot",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "participants": participants,
            "invites": invites,
            "join_requests": join_requests,
            "a2a_peers": a2a_peers,
            "audit_recent": audit_recent,
            "audit_summary": audit_summary,
            "a2a_task_requests": sorted(a2a_task_requests, key=lambda r: str(r.get("created_at") or ""), reverse=True)[:80],
            "metrics": {
                "participants": len(participants),
                "active_participants": len(active),
                "invites": len(invites),
                "pending_join_requests": len(pending),
                "a2a_peers": len(a2a_peers),
                "audit_events": len(audit_recent),
                "a2a_task_requests": len(a2a_task_requests),
                "roles": role_counts,
                "auth_modes": auth_counts,
                "client_types": client_counts,
            },
            "warnings": warnings,
        }

    def validate_approval_token(self, session_id: str, request_id: str, approval_token: str) -> Dict[str, Any]:
        self.cleanup_expired_collaboration_state()
        req = self.join_requests.get(request_id)
        if not req or req.get("session_id") != session_id:
            raise ValueError("invalid approval request")
        if req.get("approval_token_used_at"):
            raise ValueError("approval token already used")
        if req.get("status") != "approved":
            raise ValueError("join request is not approved")
        expires_at = req.get("expires_at")
        if expires_at and datetime.fromisoformat(str(expires_at)) < datetime.now():
            raise ValueError("approval token expired")
        if req.get("approval_token_hash") != self._hash_collaboration_token(approval_token):
            raise ValueError("invalid approval token")
        role = self._normalize_collaboration_role(req.get("approved_role") or req.get("requested_role"))
        return {"request": {k: v for k, v in req.items() if k != "approval_token_hash"}, "role": role, "owner_account_id": req.get("owner_account_id"), "permissions": req.get("permissions") or self.collaboration_permissions_for_role(role)}

    def mark_approval_token_used(self, session_id: str, request_id: str, participant_id: Optional[str]) -> None:
        req = self.join_requests.get(request_id)
        if not req or req.get("session_id") != session_id:
            return
        req["approval_token_used_at"] = datetime.now().isoformat()
        req["approval_token_used_by"] = participant_id
        req["status"] = "joined"
        self._persist_collaboration_state()

    async def connect(
        self,
        websocket: WebSocket,
        account_id: str,
        session_id: str,
        client_type: str,
        participant_id: Optional[str] = None,
        participant_role: str = "user",
        auth_mode: str = "account_token",
        permissions: Optional[Dict[str, Any]] = None,
        invite_id: Optional[str] = None,
        approval_request_id: Optional[str] = None,
        join_mode: str = "message",
    ):
        join_mode = str(join_mode or "message").strip().lower().replace("-", "_")
        if join_mode not in {"message", "agent_passive", "agent_active"}:
            join_mode = "message"
        await websocket.accept()
        participant_id = self._safe_participant_id(participant_id, prefix=client_type or "participant")
        role = self._normalize_collaboration_role(participant_role if participant_role != "user" else ("owner" if auth_mode == "account_token" else "human"))
        self.account_sessions.setdefault(account_id, {}).setdefault(session_id, {})[participant_id] = websocket
        self.session_participants.setdefault(session_id, {})[participant_id] = {
            "participant_id": participant_id,
            "display_name": participant_id,
            "role": role,
            "participant_role": role,
            "client_type": client_type,
            "auth_mode": auth_mode,
            "account_id": account_id if auth_mode == "account_token" else None,
            "invite_id": invite_id,
            "approval_request_id": approval_request_id,
            "permissions": permissions or self.collaboration_permissions_for_role(role),
            "join_mode": join_mode,
            "agent_collaboration": {"mode": join_mode, "active_enabled": False, "passive_enabled": join_mode in {"agent_passive", "agent_active"}},
            "connected_at": datetime.now().isoformat(),
            "last_seen_at": datetime.now().isoformat(),
            "active": True,
            "locality": "same_account_client" if auth_mode == "account_token" else ("approved_external_client" if auth_mode == "approval_token" else "invited_external_client"),
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
            previous_runtime = self.session_runtimes.get(session_id)
            previous_orchestration = (previous_runtime or {}).get("orchestration")
            if previous_orchestration is not None:
                try:
                    asyncio.create_task(previous_orchestration.stop())
                except RuntimeError:
                    # No running loop during early startup/test import. The old
                    # object will be discarded with the runtime registry entry.
                    pass

            console.print(f"[!!][CREATE RUNTIME] Starting WOLF runtime for session {session_id}")
            params = self._session_params_from_config(session_id, account_id, config)
            session = setup_cli_session(session_params=params, workflow_cls=GatewayActionWorkflow)
            wf = session["wf"]
            infra = wf.infra
            orchestration = None
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
                "orchestration": orchestration,
                "session_dir": session["session_dir"],
                "db_client": session.get("db_client"),
                "session_params": params,
                "lock": asyncio.Lock(),
            }
            if config.get("orchestration_enabled"):
                orchestration = GatewayOrchestrationSession(
                    session_id=session_id,
                    account_id=account_id,
                    config=config,
                    session_dir=session["session_dir"],
                    broadcaster=self.send_message_to_session,
                    gateway_runtime=runtime,
                    resolve_action_names=self.orchestration_resolve_action_names,
                    resolve_execution_policy=self.orchestration_resolve_execution_policy,
                    gui_command_from_workflow_event=self.orchestration_gui_command_from_workflow_event,
                    should_auto_continue_gui_command=self.orchestration_should_auto_continue_gui_command,
                )
                runtime["orchestration"] = orchestration
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
        target_participant = message.get("to_participant_id")
        target_role = message.get("to_role")
        sender_participant = message.get("participant_id") or message.get("sender_participant_id") or message.get("sender")
        visibility = str(message.get("visibility") or ("direct" if target_participant or target_role else "broadcast")).lower()
        participants = self.session_participants.get(session_id, {}) or {}

        def _should_deliver(pid: str) -> bool:
            if exclude_participant and pid == exclude_participant:
                return False
            if visibility in {"broadcast", "public", "session"} and not target_participant and not target_role:
                return True
            meta = participants.get(pid, {})
            role = meta.get("role") or meta.get("participant_role")
            perms = meta.get("permissions") or {}
            can_receive_direct = bool(perms.get("can_receive_direct_message", role in {"owner", "controller"}))
            if target_participant:
                return pid == sender_participant or (pid == target_participant and can_receive_direct)
            if target_role:
                return pid == sender_participant or (role == target_role and can_receive_direct)
            if visibility in {"private", "direct"}:
                return pid == sender_participant or role in {"owner", "controller"}
            return True

        for _, sessions in self.account_sessions.items():
            if session_id in sessions:
                for participant_id, websocket in list(sessions[session_id].items()):
                    if not _should_deliver(participant_id):
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

    def __init__(self, host: str = "127.0.0.1", port: int = 8000, static_dir: str = "./framework/pack/webapp", default_agent_config: Optional[Dict[str, Any]] = None):
        self.app = FastAPI(title="WOLF Agent Gateway V3", version="3.0.0")
        self.host = host
        self.port = port
        self.static_dir = static_dir
        self.manager = ConnectionManager(default_agent_config=default_agent_config)
        self.manager.orchestration_resolve_action_names = self._resolve_action_names
        self.manager.orchestration_resolve_execution_policy = self._resolve_execution_policy
        self.manager.orchestration_gui_command_from_workflow_event = self._gui_command_from_workflow_event
        self.manager.orchestration_should_auto_continue_gui_command = self._should_auto_continue_gui_command

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._register_collaboration_rest_middleware()
        self._register_routes()

        try:
            self.app.mount("/static", StaticFiles(directory=static_dir), name="static")
        except Exception as e:
            console.print(f"[!] Warning: Could not mount static files: {e}")

    def _rest_capability_for_request(self, method: str, path: str) -> Optional[str]:
        method = str(method or "").upper()
        for rule_method, pattern, capability in COLLABORATION_REST_CAPABILITY_RULES:
            if rule_method == method and pattern.match(path):
                return capability
        return None

    def _session_id_from_rest_path(self, path: str, query_session_id: Optional[str] = None) -> Optional[str]:
        m = re.match(r"^/sessions/([^/]+)", path or "")
        if m:
            return m.group(1)
        return query_session_id

    def _register_collaboration_rest_middleware(self) -> None:
        """Enforce participant-role capabilities on REST calls that identify a participant.

        Account tokens remain the coarse authentication boundary. When clients include
        participant_id, this middleware applies the same collaboration permission model
        used by websockets to every mapped REST endpoint.
        """

        @self.app.middleware("http")
        async def collaboration_rest_role_enforcement(request, call_next):
            participant_id = request.query_params.get("participant_id")
            capability = self._rest_capability_for_request(request.method, request.url.path)
            if participant_id and capability:
                session_id = self._session_id_from_rest_path(request.url.path, request.query_params.get("session_id"))
                if session_id and not self.manager.participant_can(session_id, participant_id, capability):
                    self.manager.record_collaboration_audit_event(
                        session_id,
                        "collaboration_permission_denied",
                        severity="warning",
                        actor_participant_id=participant_id,
                        message=f"Participant {participant_id} was denied REST capability {capability}.",
                        metadata={"capability": capability, "method": request.method, "path": request.url.path},
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": f"Participant {participant_id} is not permitted to use {capability}.",
                            "type": "permission_denied",
                            "capability": capability,
                            "session_id": session_id,
                            "participant_id": participant_id,
                        },
                    )
            return await call_next(request)

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
        known = [a for a in ACTION_NAMES if a not in GATEWAY_ORCHESTRATION_ACTIONS]
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

    AGENT_PRESET_FILES = ["llms.json", "sample_llm_config.json"]
    AGENT_PRESET_GLOBS = ["JSONs/*.json"]

    def _safe_agent_preset_paths(self) -> List[Path]:
        """Return project-local JSON files that may contain LLM/agent presets."""
        root = Path.cwd().resolve()
        paths: List[Path] = []
        for rel in self.AGENT_PRESET_FILES:
            candidate = (root / rel).resolve()
            if candidate.exists() and candidate.is_file() and root in candidate.parents:
                paths.append(candidate)
        for pattern in self.AGENT_PRESET_GLOBS:
            for candidate in sorted(root.glob(pattern)):
                candidate = candidate.resolve()
                if candidate.exists() and candidate.is_file() and root in candidate.parents:
                    paths.append(candidate)
        # Stable de-duplication while preserving order.
        out: List[Path] = []
        seen = set()
        for path in paths:
            key = str(path)
            if key not in seen:
                out.append(path)
                seen.add(key)
        return out

    def _load_agent_preset_json(self, path: Path) -> Dict[str, Any]:
        """Load a preset JSON file. Falls back to Python-literal parsing for legacy commented files."""
        text = path.read_text(errors="replace")
        try:
            data = json.loads(text)
        except Exception:
            import ast
            data = ast.literal_eval(text)
        return data if isinstance(data, dict) else {}

    def _coerce_agent_preset_port(self, value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _normalize_agent_preset(self, key: str, entry: Dict[str, Any], source_file: str) -> Optional[Dict[str, Any]]:
        if not isinstance(entry, dict):
            return None
        model = entry.get("model")
        host = entry.get("host_address", entry.get("host"))
        if not model and not host:
            return None
        params: Dict[str, Any] = {}
        if model:
            params["model"] = model
        if host:
            params["host_address"] = host
        params["host_port"] = self._coerce_agent_preset_port(entry.get("host_port", entry.get("port")))
        for src, dst in [
            ("api_key_var", "api_key_var"),
            ("api_version", "api_version"),
            ("capabilities", "capabilities"),
            ("ctx_window_length", "ctx_window_length"),
            ("sys_prompt", "sys_prompt"),
            ("verbose", "verbose"),
            ("mode", "mode"),
            ("max_steps", "max_steps"),
            ("orchestration_enabled", "orchestration_enabled"),
            ("orchestration_worker_count", "orchestration_worker_count"),
            ("orchestration_max_active_tasks", "orchestration_max_active_tasks"),
            ("orchestration_max_total_tasks", "orchestration_max_total_tasks"),
            ("agent_profiles", "agent_profiles"),
            ("agent_pool_mix", "agent_pool_mix"),
            ("agent_pool_scope", "agent_pool_scope"),
        ]:
            if src in entry and entry.get(src) not in (None, ""):
                params[dst] = entry.get(src)
        # Use the preset key as a convenient runtime/LLM name, but never copy
        # actual API key material from preset files.
        params["agent_name"] = str(entry.get("agent_name") or key)
        params.pop("api_key", None)
        display_bits = [str(source_file), "::", str(key)]
        if model:
            display_bits.extend(["—", str(model)])
        return {
            "id": f"{source_file}::{key}",
            "key": str(key),
            "source_file": source_file,
            "display_name": " ".join(display_bits),
            "model": model,
            "host_address": host,
            "api_key_var": entry.get("api_key_var"),
            "capabilities": entry.get("capabilities") or [],
            "params": self._redact_config(params),
        }

    def _extract_agent_presets_from_data(self, data: Dict[str, Any], source_file: str) -> List[Dict[str, Any]]:
        # Supported shapes:
        #   {"alpha": {"model": ..., "host": ...}, ...}
        #   {"llms": {"alpha": {...}}} / {"LLMs": {...}}
        #   {"presets": {"alpha": {...}}} or {"presets": [{"name": "alpha", ...}]}
        candidate = data.get("llms") or data.get("LLMs") or data.get("presets") or data
        items: List[tuple[str, Any]] = []
        if isinstance(candidate, dict):
            items = [(str(k), v) for k, v in candidate.items()]
        elif isinstance(candidate, list):
            for idx, value in enumerate(candidate):
                if isinstance(value, dict):
                    key = str(value.get("name") or value.get("id") or value.get("agent_name") or f"preset_{idx+1}")
                    items.append((key, value))
        presets: List[Dict[str, Any]] = []
        for key, entry in items:
            preset = self._normalize_agent_preset(key, entry, source_file)
            if preset:
                presets.append(preset)
        return presets

    def _discover_agent_config_presets(self) -> Dict[str, Any]:
        root = Path.cwd().resolve()
        presets: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        for path in self._safe_agent_preset_paths():
            rel = str(path.relative_to(root))
            try:
                data = self._load_agent_preset_json(path)
                presets.extend(self._extract_agent_presets_from_data(data, rel))
            except Exception as exc:
                errors.append({"source_file": rel, "error": str(exc)})
        return {"presets": presets, "errors": errors, "count": len(presets)}

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

    def _normalize_capture_http_url(self, value: Any) -> Optional[str]:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = urlparse(raw)
        except Exception:
            return None
        if (parsed.scheme or "").lower() not in {"http", "https"} or not parsed.netloc:
            return None
        return raw

    def _extract_capture_http_urls_from_text(self, text: Any) -> List[str]:
        raw = str(text or "")
        if not raw:
            return []
        found: List[str] = []

        def add(candidate: Any):
            url = self._normalize_capture_http_url(candidate)
            if url and url not in found:
                found.append(url)

        for match in re.finditer(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', raw, flags=re.IGNORECASE):
            add(match.group(1))
        for match in re.finditer(r'https?://[^\s"\'<>\\)]+', raw, flags=re.IGNORECASE):
            add(match.group(0))
        return found

    def _capture_panels_from_visual_context(self, visual_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        panels: List[Dict[str, Any]] = []
        vc = visual_context or {}
        if isinstance(vc.get("dashboard_panels"), list):
            panels.extend([p for p in (vc.get("dashboard_panels") or []) if isinstance(p, dict)])
        dashboard = vc.get("dashboard") or {}
        if isinstance(dashboard, dict) and isinstance(dashboard.get("panels"), list):
            panels.extend([p for p in (dashboard.get("panels") or []) if isinstance(p, dict)])
        active = vc.get("active_dashboard") or {}
        if isinstance(active, dict) and isinstance(active.get("panels"), list):
            panels.extend([p for p in (active.get("panels") or []) if isinstance(p, dict)])
        return panels

    def _capture_command_effective_ok(self, transport_ok: Any, result: Any) -> bool:
        if transport_ok is False:
            return False
        if isinstance(result, dict):
            if result.get("ok") is False:
                return False
            child_results = result.get("results") or result.get("captures") or result.get("items")
            if isinstance(child_results, list) and child_results:
                return any(isinstance(item, dict) and item.get("ok") is True and bool(item.get("image_path")) for item in child_results)
        return bool(transport_ok)

    def _decode_live_capture_image(self, image_data: str, fmt: str) -> tuple[bytes, str]:
        raw = str(image_data or "").strip()
        if not raw:
            raise ValueError("image_data is required")
        requested_fmt = "jpeg" if str(fmt or "png").lower() in {"jpg", "jpeg"} else "png"
        if raw.startswith("data:"):
            header, sep, payload = raw.partition(",")
            if not sep:
                raise ValueError("Malformed data URL image_data")
            header_l = header.lower()
            if "image/jpeg" in header_l or "image/jpg" in header_l:
                requested_fmt = "jpeg"
            elif "image/png" in header_l:
                requested_fmt = "png"
            elif "image/" in header_l:
                raise ValueError(f"Unsupported live capture image MIME in {header!r}; use PNG or JPEG")
            raw = payload
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Invalid base64 image_data: {exc}") from exc
        if len(data) > 32 * 1024 * 1024:
            raise ValueError("Live capture image is too large (>32 MiB)")
        if requested_fmt == "png" and not data.startswith(bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])):
            raise ValueError("Live capture image_data is not a PNG image")
        if requested_fmt == "jpeg" and not data.startswith(bytes([0xFF, 0xD8])):
            raise ValueError("Live capture image_data is not a JPEG image")
        return data, requested_fmt

    def _box_to_capture_clip(self, box: Any, viewport: Any, padding_px: int = 0) -> Optional[Dict[str, int]]:
        if not isinstance(box, dict):
            return None
        try:
            vw = int(getattr(viewport, "width", 1440) or 1440)
            vh = int(getattr(viewport, "height", 900) or 900)
            pad = max(0, int(padding_px or 0))
            x = max(0, int(round(float(box.get("x") or 0))) - pad)
            y = max(0, int(round(float(box.get("y") or 0))) - pad)
            w = int(round(float(box.get("width") or 0))) + pad * 2
            h = int(round(float(box.get("height") or 0))) + pad * 2
            if w <= 0 or h <= 0:
                return None
            w = max(1, min(w, max(1, vw - x)))
            h = max(1, min(h, max(1, vh - y)))
            return {"x": x, "y": y, "width": w, "height": h}
        except Exception:
            return None

    def _annotation_clip_boxes(self, request: CaptureWorkspaceRequest) -> List[Dict[str, Any]]:
        vc = request.visual_context or {}
        targets = vc.get("annotation_targets") if isinstance(vc, dict) else []
        if not isinstance(targets, list):
            targets = []
        requested = {str(v) for v in (request.annotation_ids or [])}
        clips: List[Dict[str, Any]] = []
        for index, target in enumerate(targets):
            if not isinstance(target, dict):
                continue
            ann_id = str(target.get("id") or target.get("annotation_id") or target.get("key") or index)
            if requested and ann_id not in requested:
                continue
            clip = self._box_to_capture_clip(target.get("pixel_box"), request.viewport, request.padding_px)
            if clip:
                clips.append({"clip": clip, "annotation_id": ann_id, "target": target, "index": index})
            if len(clips) >= int(request.max_panels or 1):
                break
        if clips:
            return clips

        # Fallback for agent-minimized payloads. Agents often preserve only the
        # selected box from a prior gui_get_visual_context result instead of the
        # full annotation_targets list.
        fallback_boxes: List[Dict[str, Any]] = []
        if isinstance(vc, dict):
            for key in ("annotation_pixel_box", "pixel_box"):
                value = vc.get(key)
                if isinstance(value, dict):
                    fallback_boxes.append({"pixel_box": value, "source": f"visual_context.{key}"})
            value = vc.get("annotation_pixel_boxes")
            if isinstance(value, list):
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        pixel_box = item.get("pixel_box") if isinstance(item.get("pixel_box"), dict) else item
                        fallback_boxes.append({"pixel_box": pixel_box, "source": "visual_context.annotation_pixel_boxes", "target": item, "index": idx})
        for index, item in enumerate(fallback_boxes):
            if len(clips) >= int(request.max_panels or 1):
                break
            pixel_box = item.get("pixel_box")
            if not isinstance(pixel_box, dict):
                continue
            target = item.get("target") if isinstance(item.get("target"), dict) else {}
            default_id = str(request.annotation_ids[index]) if index < len(request.annotation_ids or []) else f"fallback_annotation_{index}"
            ann_id = str(target.get("id") or target.get("annotation_id") or target.get("key") or vc.get("annotation_id") or default_id)
            if requested and ann_id not in requested:
                continue
            clip = self._box_to_capture_clip(pixel_box, request.viewport, request.padding_px)
            if clip:
                clips.append({"clip": clip, "annotation_id": ann_id, "target": {**target, "pixel_box": pixel_box}, "index": index, "source": item.get("source")})
        return clips

    def _rendered_scope_clip_boxes(self, request: CaptureWorkspaceRequest) -> List[Dict[str, Any]]:
        vc = request.visual_context or {}
        scope = str(request.capture_scope or "").strip()
        viewport_box = {
            "x": 0,
            "y": 0,
            "width": int(getattr(request.viewport, "width", 1440) or 1440),
            "height": int(getattr(request.viewport, "height", 900) or 900),
            "visible": True,
        }
        if scope == "full_gui":
            return [{"clip": None, "label": "full_gui", "source": "viewport"}]
        if scope == "annotation_regions":
            return self._annotation_clip_boxes(request)
        surfaces = vc.get("visible_surfaces") if isinstance(vc, dict) else {}
        if not isinstance(surfaces, dict):
            surfaces = {}
        if scope == "active_dashboard":
            box = ((surfaces.get("dashboard_workspace") or {}) if isinstance(surfaces.get("dashboard_workspace"), dict) else {}).get("bounding_box") or viewport_box
            clip = self._box_to_capture_clip(box, request.viewport, request.padding_px)
            return [{"clip": clip, "label": "active_dashboard", "source": "visible_surfaces.dashboard_workspace"}] if clip else []
        if scope == "workspace":
            frame_surface = surfaces.get("workspace_frame") if isinstance(surfaces.get("workspace_frame"), dict) else None
            dash_surface = surfaces.get("dashboard_workspace") if isinstance(surfaces.get("dashboard_workspace"), dict) else None
            box = (frame_surface or dash_surface or {}).get("bounding_box") or viewport_box
            clip = self._box_to_capture_clip(box, request.viewport, request.padding_px)
            return [{"clip": clip, "label": "workspace", "source": "visible_surfaces.workspace_or_dashboard"}] if clip else []
        return []

    async def _capture_rendered_gui_scope(self, request: CaptureWorkspaceRequest, session_id: str) -> Dict[str, Any]:
        vc = request.visual_context or {}
        viewport = vc.get("viewport") if isinstance(vc, dict) else {}
        gui_url = None
        if isinstance(viewport, dict):
            gui_url = self._normalize_capture_http_url(viewport.get("location"))
        if not gui_url:
            gui_url = self._normalize_capture_http_url((request.metadata or {}).get("gui_url"))
        if not gui_url:
            gui_url = self._normalize_capture_http_url(os.environ.get("WOLF_GUI_URL") or "http://127.0.0.1:8765/")
        clips = self._rendered_scope_clip_boxes(request)
        skipped_targets: List[Dict[str, Any]] = []
        if not clips:
            skipped_targets.append({"source": "visual_context", "reason": "no_rendered_clip_targets", "capture_scope": request.capture_scope})
        results: List[Dict[str, Any]] = []
        for index, item in enumerate(clips[: int(request.max_panels or 1)]):
            meta = {**(request.metadata or {}), "capture_scope": request.capture_scope, "rendered_target": item}
            one = CaptureUrlRequest(
                url=gui_url,
                viewport=request.viewport,
                clip=item.get("clip"),
                format=request.format,
                quality=request.quality,
                full_page=bool(request.full_page and item.get("clip") is None),
                wait_until=request.wait_until,
                extra_wait_ms=request.extra_wait_ms,
                timeout_ms=request.timeout_ms,
                reason=request.reason,
                metadata=meta,
            )
            # Rendered workspace scopes intentionally screenshot the local Wolf GUI
            # surface (usually http://127.0.0.1:8765/) after the connected browser
            # client has enforced the user's capture toggle. Keep generic
            # gui_capture_url loopback-blocked, but allow loopback here so
            # annotation/workspace crops can capture the trusted GUI itself.
            rendered_policy = CapturePolicy(allow_localhost=True)
            captured = (await capture_url_async(one, session_id=session_id, policy=rendered_policy)).model_dump(mode="json")
            captured["rendered_scope"] = request.capture_scope
            captured["rendered_target"] = item
            captured["source_url"] = gui_url
            results.append(captured)
        ok = all(r.get("ok") for r in results) if results else False
        return {
            "ok": ok,
            "status": "success" if ok else ("partial" if any(r.get("ok") for r in results) else "no_targets" if not results else "failed"),
            "capture_scope": request.capture_scope,
            "rendered_gui_url": gui_url,
            "count": len(results),
            "results": results,
            "skipped_targets": skipped_targets,
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

        @self.app.get("/agent-config-presets")
        async def get_agent_config_presets(token: str = Query(...)):
            """Return project-local JSON-backed agent/LLM presets for the GUI selector."""
            self._get_account_id(token)
            return self._discover_agent_config_presets()

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

        @self.app.get("/.well-known/agent.json")
        async def a2a_agent_card():
            """Advertise the Gateway as a minimal A2A-compatible collaboration endpoint."""
            base = f"http://{self.host}:{self.port}"
            return {
                "name": "WOLF Gateway",
                "description": "WOLF collaborative session gateway with invite/approval-gated A2A participation.",
                "protocol": "a2a",
                "protocol_version": "0.1",
                "capabilities": ["message_send", "heartbeat", "collaboration_join", "jsonrpc"],
                "endpoints": {
                    "handshake": f"{base}/a2a/handshake",
                    "message_send": f"{base}/a2a/message/send",
                    "jsonrpc": f"{base}/a2a/jsonrpc",
                    "heartbeat_template": f"{base}/a2a/peers/{{peer_id}}/heartbeat",
                    "agent_card": f"{base}/.well-known/agent.json",
                },
                "auth": {"modes": ["invite_token", "approval_token"], "raw_tokens_in_lists": False},
            }

        @self.app.post("/a2a/handshake")
        async def a2a_handshake(handshake: A2AHandshakeRequest):
            payload = handshake.model_dump()
            session_id = handshake.session_id
            participant_hint = handshake.agent_id or handshake.display_name or "a2a_agent"
            role = handshake.requested_role or "a2a_agent"
            auth_mode = None
            owner_account_id = None
            permissions = None
            invite_id = None
            approval_request_id = None
            if handshake.invite_token:
                try:
                    resolved = self.manager.validate_invite_token(session_id, handshake.invite_token, role)
                except Exception as exc:
                    raise HTTPException(status_code=403, detail=f"A2A invite rejected: {exc}")
                owner_account_id = str(resolved.get("owner_account_id") or "a2a")
                role = str(resolved.get("role") or role or "a2a_agent")
                if role == "human":
                    role = "a2a_agent"
                permissions = resolved.get("permissions")
                invite_id = (resolved.get("invite") or {}).get("invite_id")
                auth_mode = "invite_token"
            elif handshake.approval_token and handshake.join_request_id:
                try:
                    resolved = self.manager.validate_approval_token(session_id, handshake.join_request_id, handshake.approval_token)
                except Exception as exc:
                    raise HTTPException(status_code=403, detail=f"A2A approval rejected: {exc}")
                owner_account_id = str(resolved.get("owner_account_id") or "a2a")
                role = str(resolved.get("role") or role or "a2a_agent")
                if role == "human":
                    role = "a2a_agent"
                permissions = resolved.get("permissions")
                approval_request_id = handshake.join_request_id
                auth_mode = "approval_token"
            elif handshake.request_approval:
                try:
                    req = self.manager.create_join_request(
                        session_id=session_id,
                        requested_participant_id=participant_hint,
                        requested_role=role,
                        client_type="a2a",
                        reason=handshake.reason or "A2A agent requested collaboration access.",
                        origin={"protocol": handshake.protocol, "agent_id": participant_hint, "client": "a2a"},
                    )
                except KeyError:
                    raise HTTPException(status_code=404, detail="Session not found")
                except PermissionError as exc:
                    raise HTTPException(status_code=429, detail=str(exc))
                await self.manager.send_message_to_session({
                    "type": "join_request_pending",
                    "request_id": req.get("request_id"),
                    "session_id": session_id,
                    "requested_participant_id": req.get("requested_participant_id"),
                    "requested_role": req.get("requested_role"),
                    "client_type": "a2a",
                    "reason": req.get("reason"),
                    "expires_at": req.get("expires_at"),
                    "content": f"A2A agent {req.get('requested_participant_id')} wants to join as {req.get('requested_role')}.",
                    "timestamp": datetime.now().isoformat(),
                }, session_id)
                self.manager.record_collaboration_audit_event(
                    session_id,
                    "collaboration_a2a_handshake_pending",
                    severity="warning",
                    message=f"A2A agent {req.get('requested_participant_id')} requested approval as {req.get('requested_role')}.",
                    metadata={"request_id": req.get("request_id"), "agent_id": participant_hint, "role": req.get("requested_role"), "reason": req.get("reason")},
                )
                return {"status": "pending", "session_id": session_id, "request_id": req.get("request_id"), "request_poll_token": req.get("request_poll_token"), "participant_id": None, "role": req.get("requested_role"), "permissions": [], "peer_token": None, "message": "A2A join request is pending owner approval. Poll /a2a/join-requests/{request_id}/status with the request_poll_token."}
            else:
                raise HTTPException(status_code=401, detail="A2A handshake requires invite_token, approval_token, or request_approval=true")

            peer = self.manager.register_a2a_peer(
                session_id=session_id,
                owner_account_id=owner_account_id or "a2a",
                handshake=payload,
                auth_mode=auth_mode or "unknown",
                role=role,
                permissions=permissions,
                invite_id=invite_id,
                approval_request_id=approval_request_id,
            )
            if auth_mode == "approval_token" and approval_request_id:
                self.manager.mark_approval_token_used(session_id, approval_request_id, peer.get("participant_id"))
            await self.manager.send_message_to_session({
                "type": "presence",
                "event": "joined",
                "participant_id": peer.get("participant_id"),
                "participant_role": peer.get("role"),
                "client_type": "a2a",
                "auth_mode": auth_mode,
                "a2a_peer_id": peer.get("peer_id"),
                "content": f"A2A agent {peer.get('display_name')} joined session {session_id}.",
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id,
            }, session_id)
            self.manager.record_collaboration_audit_event(
                session_id,
                "collaboration_a2a_handshake_admitted",
                actor_participant_id=peer.get("participant_id"),
                actor_peer_id=peer.get("peer_id"),
                message=f"A2A agent {peer.get('display_name')} admitted via {auth_mode}.",
                metadata={"peer_id": peer.get("peer_id"), "agent_id": peer.get("agent_id"), "auth_mode": auth_mode, "role": peer.get("role"), "capabilities": peer.get("capabilities")},
            )
            raw_peer_token = peer.get("peer_token")
            safe_peer = {**peer, "peer_token": COLLABORATION_SECRET}
            return {"status": "approved", "session_id": session_id, "peer": safe_peer, "participant_id": peer.get("participant_id"), "role": peer.get("role"), "permissions": [k for k, v in (peer.get("permissions") or {}).items() if v], "peer_token": raw_peer_token, "message": "A2A peer admitted."}

        @self.app.get("/sessions/{session_id}/a2a/peers")
        async def list_a2a_peers(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            return {"session_id": session_id, "a2a_peers": self.manager.list_a2a_peers(session_id)}

        @self.app.get("/a2a/join-requests/{request_id}/status")
        async def a2a_join_request_status(request_id: str, session_id: str = Query(...), request_poll_token: str = Query(...), consume_approval_token: bool = Query(True)):
            try:
                out = self.manager.a2a_join_request_status(session_id=session_id, request_id=request_id, request_poll_token=request_poll_token, consume_approval_token=consume_approval_token)
            except ValueError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            return out

        @self.app.post("/a2a/peers/{peer_id}/heartbeat")
        async def a2a_heartbeat(peer_id: str, heartbeat: A2AHeartbeatRequest):
            try:
                peer = self.manager.update_a2a_heartbeat(session_id=heartbeat.session_id, peer_id=peer_id, peer_token=heartbeat.peer_token, status=heartbeat.status, load=heartbeat.load, metadata=heartbeat.metadata)
            except ValueError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            self.manager.record_collaboration_audit_event(
                heartbeat.session_id,
                "collaboration_a2a_heartbeat",
                actor_participant_id=peer.get("participant_id"),
                actor_peer_id=peer.get("peer_id"),
                message=f"A2A peer {peer.get('display_name') or peer_id} heartbeat status {peer.get('status')}.",
                metadata={"peer_id": peer.get("peer_id"), "status": peer.get("status"), "load": heartbeat.load or {}},
            )
            return {"status": "ok", "peer": peer, "timestamp": datetime.now().isoformat()}

        @self.app.post("/sessions/{session_id}/a2a/peers/{peer_id}/task-requests")
        async def create_a2a_task_request(session_id: str, peer_id: str, task: A2APassiveTaskRequest, token: str = Query(...), participant_id: Optional[str] = Query(None)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            if participant_id and not self.manager.participant_can(session_id, participant_id, "can_send_direct_message"):
                raise HTTPException(status_code=403, detail="Participant is not permitted to send direct A2A task requests")
            try:
                item = self.manager.queue_a2a_task_request(session_id=session_id, peer_id=peer_id, body=task.model_dump(), actor_participant_id=participant_id)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            event = {
                "type": "participant_message",
                "transport": "gateway_a2a_task_request",
                "a2a_kind": "task_request",
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "session_id": session_id,
                "participant_id": participant_id or "gateway_web",
                "sender": participant_id or "gateway_web",
                "to_participant_id": item.get("to_participant_id"),
                "a2a_peer_id": peer_id,
                "content": item.get("content"),
                "visibility": "direct",
                "thread_id": item.get("thread_id"),
                "task_id": item.get("task_id"),
                "request_id": item.get("request_id"),
                "metadata": item.get("metadata") or {},
                "timestamp": datetime.now().isoformat(),
            }
            delivered = await self.manager.send_message_to_session(event, session_id)
            self.manager.record_collaboration_audit_event(
                session_id,
                "collaboration_a2a_task_request_queued",
                actor_participant_id=participant_id,
                actor_peer_id=peer_id,
                message=f"Passive A2A task request {item.get('request_id')} queued for peer {peer_id}.",
                metadata={"request_id": item.get("request_id"), "task_id": item.get("task_id"), "thread_id": item.get("thread_id"), "delivered_ws": delivered},
            )
            return {"status": "queued", "task_request": item, "delivered_ws": delivered}

        @self.app.get("/a2a/peers/{peer_id}/tasks")
        async def list_a2a_peer_tasks(peer_id: str, session_id: str = Query(...), peer_token: str = Query(...), mark_delivered: bool = Query(True), limit: int = Query(50)):
            try:
                tasks = self.manager.list_a2a_task_requests(session_id=session_id, peer_id=peer_id, peer_token=peer_token, mark_delivered=mark_delivered, limit=limit)
            except ValueError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            return {"session_id": session_id, "peer_id": peer_id, "tasks": tasks, "timestamp": datetime.now().isoformat()}

        @self.app.post("/a2a/message/send")
        async def a2a_message_send(message: A2AMessageRequest):
            try:
                peer = self.manager.validate_a2a_peer_token(message.session_id, message.peer_id, message.peer_token)
            except ValueError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            permissions = peer.get("permissions") or {}
            if not permissions.get("can_send_participant_message", False):
                self.manager.record_collaboration_audit_event(message.session_id, "collaboration_permission_denied", severity="warning", actor_participant_id=peer.get("participant_id"), actor_peer_id=peer.get("peer_id"), message="A2A peer was denied participant-message permission.", metadata={"capability": "can_send_participant_message", "peer_id": peer.get("peer_id")})
                raise HTTPException(status_code=403, detail="A2A peer is not permitted to send participant messages")
            direct = bool(message.to_participant_id or message.to_role or str(message.visibility or "").lower() in {"direct", "private"})
            if direct and not permissions.get("can_send_direct_message", False):
                self.manager.record_collaboration_audit_event(message.session_id, "collaboration_permission_denied", severity="warning", actor_participant_id=peer.get("participant_id"), actor_peer_id=peer.get("peer_id"), message="A2A peer was denied direct-message permission.", metadata={"capability": "can_send_direct_message", "peer_id": peer.get("peer_id"), "to_participant_id": message.to_participant_id, "to_role": message.to_role})
                raise HTTPException(status_code=403, detail="A2A peer is not permitted to send direct messages")
            a2a_kind = self.manager.normalize_a2a_message_kind(message.kind, message.status, message.result)
            content_source = message.content
            if content_source is None and message.result is not None:
                content_source = message.result
            content = self.manager.normalize_a2a_content(content_source)
            artifacts = self.manager.normalize_a2a_artifacts(message.artifacts)
            result_payload = self.manager._redact_a2a_metadata(message.result) if message.result is not None else None
            event = {
                "type": "participant_message",
                "transport": "a2a",
                "a2a_kind": a2a_kind,
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "session_id": message.session_id,
                "participant_id": peer.get("participant_id"),
                "sender": peer.get("participant_id"),
                "a2a_peer_id": peer.get("peer_id"),
                "content": content,
                "to_participant_id": message.to_participant_id,
                "to_role": message.to_role,
                "visibility": message.visibility or ("direct" if direct else "broadcast"),
                "thread_id": message.thread_id or message.request_id,
                "task_id": message.task_id,
                "request_id": message.request_id,
                "reply_to_message_id": message.reply_to_message_id,
                "status": message.status,
                "result": result_payload,
                "artifacts": artifacts,
                "metadata": self.manager._redact_a2a_metadata(message.metadata or {}),
                "timestamp": datetime.now().isoformat(),
            }
            peer["last_message_at"] = event["timestamp"]
            peer["last_seen_at"] = event["timestamp"]
            self.manager._persist_collaboration_state()
            delivered = await self.manager.send_message_to_session(event, message.session_id)
            self.manager.record_collaboration_audit_event(
                message.session_id,
                "collaboration_a2a_message_relayed",
                actor_participant_id=peer.get("participant_id"),
                actor_peer_id=peer.get("peer_id"),
                message=f"A2A peer {peer.get('display_name') or peer.get('peer_id')} sent {a2a_kind} {event.get('visibility')} message.",
                metadata={"message_id": event["message_id"], "a2a_kind": a2a_kind, "task_id": event.get("task_id"), "request_id": event.get("request_id"), "status": event.get("status"), "artifact_count": len(artifacts), "visibility": event.get("visibility"), "to_participant_id": event.get("to_participant_id"), "to_role": event.get("to_role"), "delivered": delivered},
            )
            if a2a_kind in {"task_request", "task_result", "status", "error"}:
                self.manager.record_collaboration_audit_event(
                    message.session_id,
                    f"collaboration_a2a_{a2a_kind}",
                    severity="error" if a2a_kind == "error" else ("warning" if a2a_kind == "status" and str(message.status or "").lower() in {"blocked", "failed", "error"} else "info"),
                    actor_participant_id=peer.get("participant_id"),
                    actor_peer_id=peer.get("peer_id"),
                    message=f"A2A {a2a_kind} from {peer.get('display_name') or peer.get('peer_id')} normalized for passive routing.",
                    metadata={"message_id": event["message_id"], "task_id": event.get("task_id"), "request_id": event.get("request_id"), "reply_to_message_id": event.get("reply_to_message_id"), "status": event.get("status"), "artifact_count": len(artifacts)},
                )
            return {"jsonrpc": "2.0", "result": {"accepted": True, "delivered": delivered, "message_id": event["message_id"]}}

        @self.app.post("/a2a/jsonrpc")
        async def a2a_jsonrpc(payload: Dict[str, Any]):
            method = payload.get("method")
            params = payload.get("params") or {}
            if method not in {"message/send", "message.send"}:
                return JSONResponse(status_code=400, content={"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32601, "message": "Method not found"}})
            try:
                req = A2AMessageRequest(**params)
            except Exception as exc:
                return JSONResponse(status_code=400, content={"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32602, "message": str(exc)}})
            result = await a2a_message_send(req)
            return {"jsonrpc": "2.0", "id": payload.get("id"), "result": result.get("result", result)}

        @self.app.post("/sessions/{session_id}/invites")
        async def create_session_invite(session_id: str, invite: CollaborationInviteCreate, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            out = self.manager.create_invite(
                session_id=session_id,
                owner_account_id=current_account,
                body=invite.model_dump(),
                gateway_url=f"http://{self.host}:{self.port}",
            )
            self.manager.record_collaboration_audit_event(session_id, "collaboration_invite_created", message=f"Invite created for role {out.get('role')}.", metadata={"invite_id": out.get("invite_id"), "role": out.get("role"), "max_uses": out.get("max_uses"), "expires_at": out.get("expires_at")})
            await self.manager.send_message_to_session({
                "type": "collaboration_invite_created",
                "content": f"Collaboration invite created for role {out.get('role')}.",
                "invite": {k: v for k, v in out.items() if k not in {"invite_token", "command", "invite_url"}},
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id,
            }, session_id)
            return out

        @self.app.get("/sessions/{session_id}/invites")
        async def list_session_invites(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            return {"session_id": session_id, "invites": self.manager.list_invites(session_id)}

        @self.app.delete("/sessions/{session_id}/invites/{invite_id}")
        async def revoke_session_invite(session_id: str, invite_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            try:
                out = self.manager.revoke_invite(session_id, invite_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="Invite not found")
            self.manager.record_collaboration_audit_event(session_id, "collaboration_invite_revoked", severity="warning", message=f"Invite {invite_id} revoked.", metadata={"invite_id": invite_id})
            await self.manager.send_message_to_session({
                "type": "collaboration_invite_revoked",
                "content": f"Collaboration invite {invite_id} revoked.",
                "invite_id": invite_id,
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id,
            }, session_id)
            return {"session_id": session_id, "invite": out, "status": "revoked"}

        @self.app.get("/sessions/{session_id}/join-requests")
        async def list_join_requests(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            return {"session_id": session_id, "join_requests": self.manager.list_join_requests(session_id)}

        @self.app.post("/sessions/{session_id}/join-requests/{request_id}/approve")
        async def approve_join_request(session_id: str, request_id: str, decision: JoinRequestDecision, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            try:
                out = self.manager.approve_join_request(session_id, request_id, body=decision.model_dump())
            except KeyError:
                raise HTTPException(status_code=404, detail="Join request not found")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            pending_ws = self.manager.pending_join_sockets.get(request_id)
            if pending_ws is not None:
                try:
                    await pending_ws.send_json({
                        "type": "join_approved",
                        "request_id": request_id,
                        "session_id": session_id,
                        "approval_token": out.get("approval_token"),
                        "role": out.get("approved_role") or out.get("requested_role"),
                        "content": "Join request approved. Reconnecting with approval token is allowed.",
                        "timestamp": datetime.now().isoformat(),
                    })
                except Exception:
                    pass
            self.manager.pending_join_sockets.pop(request_id, None)
            self.manager.record_collaboration_audit_event(session_id, "collaboration_join_approved", message=f"Join request {request_id} approved.", metadata={"request_id": request_id, "role": out.get("approved_role") or out.get("requested_role"), "client_type": out.get("client_type")})
            await self.manager.send_message_to_session({
                "type": "collaboration_join_approved",
                "content": f"Join request {request_id} approved.",
                "request_id": request_id,
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id,
            }, session_id)
            safe = {k: v for k, v in out.items() if k != "approval_token"}
            return {"session_id": session_id, "join_request": safe, "status": "approved"}

        @self.app.post("/sessions/{session_id}/join-requests/{request_id}/reject")
        async def reject_join_request(session_id: str, request_id: str, decision: JoinRequestDecision, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            try:
                out = self.manager.reject_join_request(session_id, request_id, reason=decision.reason)
            except KeyError:
                raise HTTPException(status_code=404, detail="Join request not found")
            pending_ws = self.manager.pending_join_sockets.get(request_id)
            if pending_ws is not None:
                try:
                    await pending_ws.send_json({
                        "type": "join_rejected",
                        "request_id": request_id,
                        "session_id": session_id,
                        "reason": decision.reason or "owner_rejected",
                        "content": "Join request rejected.",
                        "timestamp": datetime.now().isoformat(),
                    })
                    await pending_ws.close(code=4003, reason="Join request rejected")
                except Exception:
                    pass
            self.manager.pending_join_sockets.pop(request_id, None)
            self.manager.record_collaboration_audit_event(session_id, "collaboration_join_rejected", severity="warning", message=f"Join request {request_id} rejected.", metadata={"request_id": request_id, "reason": decision.reason})
            await self.manager.send_message_to_session({
                "type": "collaboration_join_rejected",
                "content": f"Join request {request_id} rejected.",
                "request_id": request_id,
                "reason": decision.reason,
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id,
            }, session_id)
            return {"session_id": session_id, "join_request": out, "status": "rejected"}

        @self.app.get("/sessions/{session_id}/collaboration/snapshot")
        async def get_collaboration_snapshot(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            return self.manager.collaboration_snapshot(session_id)

        @self.app.get("/sessions/{session_id}/infrastructure/snapshot")
        async def get_infrastructure_snapshot(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            runtime = self.manager.get_runtime(session_id)
            if runtime is None:
                cfg = self.manager.sessions[session_id].agent_config if session_id in self.manager.sessions else self.manager.default_config()
                runtime = self.manager.create_runtime_session(session_id, current_account, cfg or self.manager.default_config())
            orch = self._get_orchestration_session(session_id)
            return await build_infrastructure_snapshot(session_id=session_id, runtime=runtime, orch=orch)

        @self.app.get("/sessions/{session_id}/orchestration/snapshot")
        async def get_orchestration_snapshot(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            return await orch.snapshot()

        @self.app.get("/sessions/{session_id}/orchestration/agent_pool")
        async def get_orchestration_agent_pool(session_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            return await orch.agent_pool_snapshot()

        @self.app.patch("/sessions/{session_id}/orchestration/agent_pool/mix")
        async def patch_orchestration_agent_pool_mix(session_id: str, updates: Dict[str, Any], token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            snap = await orch.update_agent_pool_mix(updates or {})
            runtime = self.manager.get_runtime(session_id) or {}
            cfg = runtime.get("config") or self.manager.sessions[session_id].agent_config or self.manager.default_config()
            cfg["agent_profiles"] = snap.get("agent_profiles", [])
            cfg["agent_pool_mix"] = snap.get("agent_pool_mix", {})
            cfg["orchestration_worker_count"] = int(sum((snap.get("agent_pool_mix") or {}).values()) or 1)
            runtime["config"] = cfg
            if session_id in self.manager.sessions:
                self.manager.sessions[session_id].agent_config = cfg
            return snap

        @self.app.get("/sessions/{session_id}/orchestration/tasks/{task_id}")
        async def get_orchestration_task(session_id: str, task_id: str, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            try:
                return await orch.get_task_detail(task_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="Task not found")

        @self.app.post("/sessions/{session_id}/orchestration/tasks/{task_id}/message")
        async def message_orchestration_task(session_id: str, task_id: str, body: Dict[str, Any], token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            try:
                return await orch.inject_user_message(task_id, body.get("content") or body.get("message") or "", role=body.get("sender") or "user")
            except KeyError:
                raise HTTPException(status_code=404, detail="Task not found")

        @self.app.post("/sessions/{session_id}/orchestration/tasks/{task_id}/pause")
        async def pause_orchestration_task(session_id: str, task_id: str, body: Optional[Dict[str, Any]] = None, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            try:
                return await orch.pause_task(task_id, reason=(body or {}).get("reason") or "paused by user")
            except KeyError:
                raise HTTPException(status_code=404, detail="Task not found")

        @self.app.post("/sessions/{session_id}/orchestration/tasks/{task_id}/resume")
        async def resume_orchestration_task(session_id: str, task_id: str, body: Optional[Dict[str, Any]] = None, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            try:
                return await orch.resume_task(task_id, reason=(body or {}).get("reason") or "resumed by user")
            except KeyError:
                raise HTTPException(status_code=404, detail="Task not found")

        @self.app.post("/sessions/{session_id}/orchestration/tasks/{task_id}/cancel")
        async def cancel_orchestration_task(session_id: str, task_id: str, body: Optional[Dict[str, Any]] = None, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            try:
                return await orch.cancel_task(task_id, reason=(body or {}).get("reason") or "cancelled by user")
            except KeyError:
                raise HTTPException(status_code=404, detail="Task not found")

        @self.app.post("/sessions/{session_id}/orchestration/tasks/{task_id}/retry")
        async def retry_orchestration_task(session_id: str, task_id: str, body: Optional[Dict[str, Any]] = None, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            try:
                return await orch.retry_task(task_id, reason=(body or {}).get("reason") or "retried by user")
            except KeyError:
                raise HTTPException(status_code=404, detail="Task not found")

        @self.app.post("/sessions/{session_id}/orchestration/tasks/{task_id}/cancel_subtree")
        async def cancel_orchestration_subtree(session_id: str, task_id: str, body: Optional[Dict[str, Any]] = None, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            body = body or {}
            try:
                return await orch.cancel_subtree(task_id, reason=body.get("reason") or "cancelled subtree by user", include_root=bool(body.get("include_root", True)))
            except KeyError:
                raise HTTPException(status_code=404, detail="Task not found")

        @self.app.post("/sessions/{session_id}/orchestration/tasks/{task_id}/retry_subtree")
        async def retry_orchestration_subtree(session_id: str, task_id: str, body: Optional[Dict[str, Any]] = None, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            body = body or {}
            try:
                return await orch.retry_subtree(task_id, reason=body.get("reason") or "retried subtree by user", include_root=bool(body.get("include_root", True)), include_completed=bool(body.get("include_completed", False)))
            except KeyError:
                raise HTTPException(status_code=404, detail="Task not found")

        @self.app.post("/sessions/{session_id}/orchestration/tasks/{task_id}/replan")
        async def replan_orchestration_task(session_id: str, task_id: str, body: Optional[Dict[str, Any]] = None, token: str = Query(...)):
            current_account = self._get_account_id(token)
            self.manager.get_or_create_session(account_id=current_account, session_id=session_id)
            if not self.manager.session_belongs_to_account(session_id, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            orch = self._get_orchestration_session(session_id)
            if not orch:
                raise HTTPException(status_code=400, detail="Orchestration is not enabled for this session")
            body = body or {}
            try:
                return await orch.request_replan(task_id, reason=body.get("reason") or "replan requested by user", prompt=body.get("prompt"))
            except KeyError:
                raise HTTPException(status_code=404, detail="Task not found")

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


        @self.app.post("/api/gui/capture/live")
        async def gui_capture_live_endpoint(request: LiveGuiCaptureUpload, token: str = Query(...), session_id: Optional[str] = Query(None)):
            """Store a screenshot captured from the user's live browser surface.

            The browser can only produce this after explicit user permission via
            getDisplayMedia. This endpoint performs account/session checks,
            validates PNG/JPEG bytes, writes the artifact into the normal capture
            store, and returns the same image_path shape consumed by GUI command
            auto-continuation.
            """
            current_account = self._get_account_id(token)
            target_session = session_id or self.manager.account_default_sessions.get(current_account) or "default"
            if target_session in self.manager.sessions and not self.manager.session_belongs_to_account(target_session, current_account):
                raise HTTPException(status_code=403, detail="Forbidden")
            try:
                data, fmt = self._decode_live_capture_image(request.image_data, request.format)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            storage = CaptureStorage()
            capture_id = request.capture_id or storage.new_capture_id()
            image_path = storage.image_path(target_session, capture_id, fmt)
            image_path.write_bytes(data)
            metadata = {
                "capture_id": capture_id,
                "captured_at": datetime.now().isoformat(),
                "capture_mode": "live_client_surface",
                "capture_scope": request.capture_scope,
                "source_url": request.source_url,
                "format": fmt,
                "width": request.width,
                "height": request.height,
                "session_id": target_session,
                "metadata": request.metadata or {},
                "image_path": str(image_path),
                "ok": True,
                "status": "success",
            }
            metadata_path = storage.write_metadata(target_session, capture_id, metadata)
            result = {
                "ok": True,
                "status": "success",
                "capture_id": capture_id,
                "capture_mode": "live_client_surface",
                "capture_scope": request.capture_scope,
                "source_url": request.source_url,
                "image_path": str(image_path),
                "metadata_path": str(metadata_path),
                "width": request.width,
                "height": request.height,
                "format": fmt,
            }
            try:
                await self.manager.send_message_to_session({
                    "type": "gui_capture_audit",
                    "content": f"Live GUI browser-surface capture stored for scope {request.capture_scope or 'full_gui'}.",
                    "ok": True,
                    "status": "success",
                    "capture_id": capture_id,
                    "capture_mode": "live_client_surface",
                    "image_path": str(image_path),
                    "timestamp": datetime.now().isoformat(),
                    "session_id": target_session,
                }, target_session)
            except Exception:
                pass
            return result


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

            if request.capture_scope in {"workspace", "active_dashboard", "full_gui", "annotation_regions"}:
                out = await self._capture_rendered_gui_scope(request, target_session)
                try:
                    await self.manager.send_message_to_session({
                        "type": "gui_capture_audit",
                        "content": f"Rendered GUI capture completed for scope {request.capture_scope} with {out.get('count', 0)} capture(s).",
                        "ok": out.get("ok"),
                        "count": out.get("count", 0),
                        "capture_scope": request.capture_scope,
                        "timestamp": datetime.now().isoformat(),
                        "session_id": target_session,
                    }, target_session)
                except Exception:
                    pass
                return out

            skipped_targets = []
            urls = []
            for idx, url in enumerate(list(request.urls or [])):
                normalized = self._normalize_capture_http_url(url)
                if normalized and normalized not in urls:
                    urls.append(normalized)
                elif url:
                    skipped_targets.append({"source": "request.urls", "index": idx, "value": str(url), "reason": "not_capturable_http_url"})
            if request.visual_context and len(urls) < request.max_panels and request.capture_scope in {"active_dashboard_panels", "selected_panels", "url_list"}:
                try:
                    requested_panel_ids = {str(v) for v in request.panel_ids}
                    for panel in self._capture_panels_from_visual_context(request.visual_context or {}):
                        iframe = panel.get("iframe") or {}
                        panel_id = str(panel.get("id") or panel.get("key") or panel.get("panel_id") or "unknown_panel")
                        if requested_panel_ids and panel_id not in requested_panel_ids:
                            skipped_targets.append({"source": "visual_context.dashboard_panels", "panel_id": panel_id, "reason": "panel_not_requested"})
                            continue
                        before = len(urls)
                        candidates = [panel.get("url"), iframe.get("src"), iframe.get("url")]
                        candidates.extend(self._extract_capture_http_urls_from_text(panel.get("inline_html_excerpt")))
                        candidates.extend(self._extract_capture_http_urls_from_text(iframe.get("src")))
                        for candidate in candidates:
                            normalized = self._normalize_capture_http_url(candidate)
                            if normalized and normalized not in urls:
                                urls.append(normalized)
                        if len(urls) == before:
                            skipped_targets.append({"source": "visual_context.dashboard_panels", "panel_id": panel_id, "reason": "no_capturable_http_url_found"})
                        if len(urls) >= request.max_panels:
                            break
                except Exception as exc:
                    skipped_targets.append({"source": "visual_context", "reason": "collection_error", "error": str(exc)})

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
            out = {
                "ok": all(r.get("ok") for r in results) if results else False,
                "status": "success" if results and all(r.get("ok") for r in results) else ("partial" if any(r.get("ok") for r in results) else "no_targets" if not results else "failed"),
                "capture_scope": request.capture_scope,
                "count": len(results),
                "urls": urls[: int(request.max_panels or 1)],
                "results": results,
                "skipped_targets": skipped_targets,
            }
            if not results and request.fail_if_no_targets:
                out["error"] = "No capturable HTTP(S) URL targets found for requested GUI workspace capture scope."
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
            token: Optional[str] = Query(None),
            invite_token: Optional[str] = Query(None),
            approval_token: Optional[str] = Query(None),
            join_request_id: Optional[str] = Query(None),
            request_join: bool = Query(False),
            reason: Optional[str] = Query(None),
            participant_id: Optional[str] = Query(None),
            participant_role: str = Query("user"),
            client_type: str = Query("tui"),
            join_mode: str = Query("message"),
        ):
            auth_mode = "account_token"
            permissions: Optional[Dict[str, Any]] = None
            invite_id: Optional[str] = None
            approval_request_id: Optional[str] = None
            current_account: Optional[str] = None

            if token:
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
                participant_role = "owner" if participant_role == "user" else participant_role
                permissions = self.manager.collaboration_permissions_for_role(participant_role)
            elif invite_token:
                try:
                    resolved = self.manager.validate_invite_token(session_id, invite_token, participant_role)
                except Exception as exc:
                    await websocket.close(code=4003, reason=f"Invite rejected: {exc}")
                    return
                current_account = str(resolved.get("owner_account_id") or account_id or "invite")
                account_id = current_account
                participant_role = str(resolved.get("role") or participant_role or "human")
                permissions = resolved.get("permissions")
                invite_id = (resolved.get("invite") or {}).get("invite_id")
                auth_mode = "invite_token"
            elif approval_token and join_request_id:
                try:
                    resolved = self.manager.validate_approval_token(session_id, join_request_id, approval_token)
                except Exception as exc:
                    await websocket.close(code=4003, reason=f"Approval rejected: {exc}")
                    return
                current_account = str(resolved.get("owner_account_id") or account_id or "approved")
                account_id = current_account
                participant_role = str(resolved.get("role") or participant_role or "human")
                permissions = resolved.get("permissions")
                approval_request_id = join_request_id
                auth_mode = "approval_token"
            elif request_join:
                await websocket.accept()
                try:
                    req = self.manager.create_join_request(
                        session_id=session_id,
                        requested_participant_id=participant_id,
                        requested_role=participant_role,
                        client_type=client_type,
                        reason=reason,
                        origin={"account_path": account_id},
                    )
                except KeyError:
                    await websocket.send_json({"type": "join_rejected", "content": "Session not found.", "reason": "session_not_found", "session_id": session_id})
                    await websocket.close(code=4004, reason="Session not found")
                    return
                except PermissionError as exc:
                    await websocket.send_json({"type": "join_rejected", "content": str(exc), "reason": "rate_limited", "session_id": session_id})
                    await websocket.close(code=4008, reason="Join request rate limited")
                    return
                request_id = req["request_id"]
                self.manager.pending_join_sockets[request_id] = websocket
                await websocket.send_json({
                    "type": "join_pending",
                    "request_id": request_id,
                    "session_id": session_id,
                    "expires_at": req.get("expires_at"),
                    "content": "Join request sent. Waiting for owner approval.",
                    "timestamp": datetime.now().isoformat(),
                })
                await self.manager.send_message_to_session({
                    "type": "join_request_pending",
                    "request_id": request_id,
                    "session_id": session_id,
                    "requested_participant_id": req.get("requested_participant_id"),
                    "requested_role": req.get("requested_role"),
                    "client_type": client_type,
                    "reason": reason,
                    "expires_at": req.get("expires_at"),
                    "content": f"{req.get('requested_participant_id')} wants to join as {req.get('requested_role')}.",
                    "timestamp": datetime.now().isoformat(),
                }, session_id)
                try:
                    while True:
                        await asyncio.wait_for(websocket.receive_text(), timeout=120.0)
                except Exception:
                    self.manager.pending_join_sockets.pop(request_id, None)
                    if self.manager.join_requests.get(request_id, {}).get("status") == "pending":
                        self.manager.join_requests[request_id]["status"] = "disconnected"
                    return
            else:
                await websocket.close(code=4001, reason="Unauthorized")
                return

            join_mode = str(join_mode or "message").strip().lower().replace("-", "_")
            if join_mode not in {"message", "agent_passive", "agent_active"}:
                join_mode = "message"

            participant_id = await self.manager.connect(
                websocket,
                account_id,
                session_id,
                client_type,
                participant_id,
                participant_role,
                auth_mode=auth_mode,
                permissions=permissions,
                invite_id=invite_id,
                approval_request_id=approval_request_id,
                join_mode=join_mode,
            )
            if auth_mode == "approval_token" and approval_request_id:
                self.manager.mark_approval_token_used(session_id, approval_request_id, participant_id)

            try:
                await self.manager.send_message_to_session(
                    {
                        "type": "presence",
                        "event": "joined",
                        "participant_id": participant_id,
                        "participant_role": participant_role,
                        "client_type": client_type,
                        "auth_mode": auth_mode,
                        "join_mode": join_mode,
                        "invite_id": invite_id,
                        "approval_request_id": approval_request_id,
                        "content": f"{participant_id} joined session {session_id} as {participant_role} via {auth_mode}.",
                        "timestamp": datetime.now().isoformat(),
                        "session_id": session_id,
                    },
                    session_id,
                )
                await websocket.send_json(
                    {
                        "type": "system",
                        "content": f"Connected to WOLF Gateway V3. Account: {account_id}. Session: {session_id}. Participant: {participant_id}. Auth mode: {auth_mode}.",
                        "timestamp": datetime.now().isoformat(),
                        "session_id": session_id,
                        "participant_id": participant_id,
                        "auth_mode": auth_mode,
                        "join_mode": join_mode,
                        "permissions": permissions,
                    }
                )
                if join_mode in {"agent_passive", "agent_active"}:
                    await websocket.send_json({
                        "type": "agent_collaboration_mode",
                        "mode": join_mode,
                        "active_enabled": False,
                        "passive_enabled": True,
                        "content": "Agent-backed collaboration mode is registered as passive metadata; active agent execution remains disabled in this phase.",
                        "timestamp": datetime.now().isoformat(),
                        "session_id": session_id,
                    })

                while True:
                    try:
                        data = await asyncio.wait_for(websocket.receive_json(), timeout=300.0)
                        msg_type = data.get("type")

                        participant_meta = self.manager.session_participants.get(session_id, {}).get(participant_id, {})
                        participant_meta["last_seen_at"] = datetime.now().isoformat()
                        participant_permissions = participant_meta.get("permissions") or {}

                        async def _permission_denied(capability: str):
                            self.manager.record_collaboration_audit_event(
                                session_id,
                                "collaboration_permission_denied",
                                severity="warning",
                                actor_participant_id=participant_id,
                                message=f"Participant {participant_id} was denied websocket capability {capability}.",
                                metadata={"capability": capability, "message_type": msg_type},
                            )
                            await websocket.send_json({
                                "type": "permission_denied",
                                "capability": capability,
                                "content": f"Participant {participant_id} is not permitted to use {capability}.",
                                "timestamp": datetime.now().isoformat(),
                                "session_id": session_id,
                            })

                        if msg_type == "chat":
                            if not participant_permissions.get("can_send_chat_to_agent", False):
                                await _permission_denied("can_send_chat_to_agent")
                                continue
                            visual_context = data.get("visual_context")
                            if visual_context is None and isinstance(data.get("metadata"), dict):
                                visual_context = data.get("metadata", {}).get("visual_context")
                            runtime = self.manager.get_runtime(session_id)
                            orch = self._get_orchestration_session(session_id)
                            if orch is not None:
                                await orch.submit_user_message(
                                    data.get("content", ""),
                                    sender=data.get("sender") or participant_id,
                                    visual_context=visual_context,
                                    target_task_id=data.get("target_task_id") or data.get("task_id"),
                                    force_new_root=bool(data.get("force_new_run") or data.get("force_new_root")),
                                )
                            else:
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
                            if not participant_permissions.get("can_execute_agent_control", False):
                                await _permission_denied("can_execute_agent_control")
                                continue
                            await self._handle_agent_control(data, session_id, participant_id)
                        elif msg_type == "gui_client_hello":
                            if not participant_permissions.get("can_upload_gui_results", False):
                                await _permission_denied("can_upload_gui_results")
                                continue
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
                            if not participant_permissions.get("can_upload_gui_results", False):
                                await _permission_denied("can_upload_gui_results")
                                continue
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

                            runtime = self.manager.get_runtime(session_id)
                            pending_command = None
                            if runtime:
                                pending = runtime.setdefault("pending_gui_commands", {})
                                command_id = data.get("command_id")
                                if command_id:
                                    pending_command = pending.pop(command_id, None)
                            action_label = data.get("action") or (pending_command or {}).get("action") or "gui_command_result"
                            orchestration_target_task_id = (pending_command or {}).get("target_task_id")
                            orchestration_source = (pending_command or {}).get("source") == "orchestration"
                            orch = self._get_orchestration_session(session_id) if orchestration_source else None
                            should_continue = bool((pending_command or {}).get("auto_continue")) or self._should_auto_continue_gui_command(action_label)
                            orchestration_continuation_prompt = None
                            if should_continue and orch is not None and orchestration_target_task_id:
                                orchestration_continuation_prompt = self._gui_command_continuation_prompt(
                                    action_label,
                                    data.get("command_id"),
                                    self._capture_command_effective_ok(data.get("ok"), data.get("result")),
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

                            try:
                                capture_artifacts = _collect_capture_artifacts(data.get("result"))
                                capture_artifacts = [a for a in capture_artifacts if a.get("image_path") or a.get("capture_id")]
                                if capture_artifacts:
                                    gui_result_event["capture_artifacts"] = capture_artifacts
                                    gui_result_event["image_references"] = [
                                        {"name": Path(str(a.get("image_path") or "")).name, "path": a.get("image_path")}
                                        for a in capture_artifacts
                                        if a.get("image_path")
                                    ]
                                    if runtime and not orchestration_source:
                                        runtime.setdefault("pending_gui_capture_artifacts", []).extend(capture_artifacts)
                            except Exception as capture_exc:
                                gui_result_event["capture_bridge_error"] = f"{type(capture_exc).__name__}: {capture_exc}"

                            # Important bridge: deferred GUI commands execute in the
                            # browser client after the workflow step has returned.
                            # For orchestration-origin commands, route the full result
                            # to the originating task-local worker history.  For normal
                            # session-global commands, append to the session workflow.
                            try:
                                if orch is not None and orchestration_target_task_id:
                                    attach_result = await orch.handle_gui_command_result(
                                        orchestration_target_task_id,
                                        gui_result_event,
                                        continuation_prompt=orchestration_continuation_prompt,
                                        wake=True,
                                    )
                                    gui_result_event["orchestration_attach"] = attach_result
                                elif runtime:
                                    wf = runtime.get("wf")
                                    infra = runtime.get("infra")
                                    result_text = json.dumps(gui_result_event, indent=2, sort_keys=True, default=str)[:40000]
                                    history_payload = {
                                        "action": "gui_command_result",
                                        "gui_action": action_label,
                                        "command_id": data.get("command_id"),
                                        "ok": self._capture_command_effective_ok(data.get("ok"), data.get("result")),
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
                                    if wf is not None and hasattr(wf, "save_session_state"):
                                        wf.save_session_state()
                            except Exception as bridge_exc:
                                gui_result_event["history_bridge_error"] = f"{type(bridge_exc).__name__}: {bridge_exc}"

                            await self.manager.send_message_to_session(gui_result_event, session_id)

                            try:
                                if runtime and should_continue and not (orch is not None and orchestration_target_task_id):
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
                                                ok=self._capture_command_effective_ok(data.get("ok"), data.get("result")),
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
                            if not participant_permissions.get("can_send_participant_message", False):
                                await _permission_denied("can_send_participant_message")
                                continue
                            requested_visibility = str(data.get("visibility") or "").lower()
                            is_direct_message = bool(data.get("to_participant_id") or data.get("to_role") or requested_visibility in {"direct", "private"})
                            if is_direct_message and not participant_permissions.get("can_send_direct_message", False):
                                await _permission_denied("can_send_direct_message")
                                continue
                            await self._handle_participant_message(data, session_id, participant_id)
                        elif msg_type == "orchestration_snapshot_request":
                            if not participant_permissions.get("can_request_orchestration_snapshot", False):
                                await _permission_denied("can_request_orchestration_snapshot")
                                continue
                            orch = self._get_orchestration_session(session_id)
                            if orch is None:
                                await websocket.send_json({"type": "error", "content": "Orchestration is not enabled for this session", "timestamp": datetime.now().isoformat(), "session_id": session_id})
                            else:
                                await websocket.send_json(await orch.snapshot())
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

    def _get_orchestration_session(self, session_id: str) -> Optional[GatewayOrchestrationSession]:
        runtime = self.manager.get_runtime(session_id)
        if not runtime:
            return None
        orch = runtime.get("orchestration")
        if orch is None:
            return None
        config = runtime.get("config") or {}
        if not config.get("orchestration_enabled"):
            return None
        return orch

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
            "to_participant_id": data.get("to_participant_id"),
            "to_role": data.get("to_role"),
            "thread_id": data.get("thread_id"),
            "message_id": data.get("message_id") or f"msg_{uuid.uuid4().hex[:12]}",
            "visibility": data.get("visibility") or ("direct" if data.get("to_participant_id") else "broadcast"),
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
        }
        delivered = await self.manager.send_message_to_session(msg, session_id)
        self.manager.record_collaboration_audit_event(
            session_id,
            "collaboration_participant_message",
            actor_participant_id=participant_id,
            message=f"Participant {participant_id} sent {msg.get('visibility')} message.",
            metadata={"message_id": msg["message_id"], "visibility": msg.get("visibility"), "to_participant_id": msg.get("to_participant_id"), "to_role": msg.get("to_role"), "delivered": delivered},
        )
        return {"ok": delivered, "message_id": msg["message_id"], "visibility": msg["visibility"], "to_participant_id": msg.get("to_participant_id"), "to_role": msg.get("to_role")}

    def run(self):
        console.print(f"[*] Starting WOLF Gateway V3 on {self.host}:{self.port}")
        uvicorn.run(self.app, host=self.host, port=self.port)


if __name__ == "__main__":
    gateway = WolfGateway()
    gateway.run()
