from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, quote, urlencode, urlparse
from typing import Any, Dict, Optional

from framework.cli.config_loader import print_launch_config


def launch_cli(config: Dict[str, Any], *, dry_run: bool = False, explain: bool = False) -> int:
    from framework.utils.config_tools import CliSession
    from framework.workflows.workflow_space import get_workflow_class

    workflow_name = config.get("workflow") or "FastTurnBasedWorkflow"
    workflow_cls = get_workflow_class(workflow_name)
    user_name = config.get("user_name") or "user"
    resume_session = config.get("resume_session")
    session_params = config["session"]

    if explain or dry_run:
        print("Launch plan:")
        print(f"  mode: cli")
        print(f"  workflow: {workflow_name} ({workflow_cls.__module__}.{workflow_cls.__name__})")
        print(f"  user_name: {user_name}")
        print(f"  resume_session: {resume_session}")
        print(f"  configured universes: {len(session_params.get('universes', []) or [])}")
        print(f"  configured LLMs: {len(session_params.get('LLMs', {}) or {})}")
    if dry_run:
        return 0

    cli_session = CliSession(session_params=session_params, db_client=None)
    cli_session.create_session(resume_session=resume_session, workflow_cls=workflow_cls)
    cli_session.session["wf"].run(user_name=user_name)
    return 0


def launch_api(config: Dict[str, Any], *, host: str = "0.0.0.0", port: int = 8000, dry_run: bool = False, explain: bool = False) -> int:
    if explain or dry_run:
        print("Launch plan:")
        print("  mode: api")
        print("  implementation: framework.workflows.custom_workflows.async_api_workflow:app")
        print(f"  host: {host}")
        print(f"  port: {port}")
    if dry_run:
        return 0
    import uvicorn
    from framework.workflows.custom_workflows.async_api_workflow import app

    uvicorn.run(app, host=host, port=port)
    return 0


def launch_gateway(
    config: Dict[str, Any],
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    static_dir: str = "./framework/pack/webapp",
    default_agent_config: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    explain: bool = False,
) -> int:
    if explain or dry_run:
        print("Launch plan:")
        print("  mode: gateway")
        print("  implementation: framework.pack.gateway:WolfGateway")
        print(f"  host: {host}")
        print(f"  port: {port}")
        print(f"  static_dir: {static_dir}")
        if default_agent_config:
            safe = {k: ("***REDACTED***" if "key" in str(k).lower() or "token" in str(k).lower() else v) for k, v in default_agent_config.items() if v not in (None, [], "")}
            print(f"  default_agent_config: {safe}")
    if dry_run:
        return 0
    from framework.pack.gateway import WolfGateway

    gateway = WolfGateway(host=host, port=port, static_dir=static_dir, default_agent_config=default_agent_config)
    gateway.run()
    return 0


def launch_tui(config: Dict[str, Any], *, gateway_url: str = "http://127.0.0.1:8000", session_id: Optional[str] = None, dry_run: bool = False, explain: bool = False) -> int:
    if explain or dry_run:
        print("Launch plan:")
        print("  mode: tui")
        print("  implementation: framework.ui.tui_client:WolfTUIClient")
        print(f"  gateway_url: {gateway_url}")
        print(f"  session_id: {session_id}")
    if dry_run:
        return 0
    from framework.ui.tui_client import WolfTUIClient

    client = WolfTUIClient(gateway_url=gateway_url, session_id=session_id)
    asyncio.run(client.run())
    return 0


def launch_gui(config: Dict[str, Any], *, dry_run: bool = False, explain: bool = False) -> int:
    host = str(config.get("gui", {}).get("host") or "127.0.0.1")
    port = int(config.get("gui", {}).get("port") or 8765)
    open_browser = bool(config.get("gui", {}).get("open_browser", True))
    gateway_url = config.get("gui", {}).get("gateway_url")
    auto_connect_gateway = bool(config.get("gui", {}).get("auto_connect_gateway", False))
    gui_action_route = config.get("gui", {}).get("gui_action_route")
    if explain or dry_run:
        print("Launch plan:")
        print("  mode: gui")
        print("  implementation: framework.gui.server:start_gui_server")
        print(f"  host: {host}")
        print(f"  port: {port}")
        print(f"  open_browser: {open_browser}")
        print(f"  gateway_url: {gateway_url}")
        print(f"  auto_connect_gateway: {auto_connect_gateway}")
        print(f"  gui_action_route: {gui_action_route}")
        print("  concept: visual browser workspace with floating agent panel")
    if dry_run:
        return 0
    from framework.gui.server import start_gui_server

    return start_gui_server(config, host=host, port=port, open_browser=open_browser)


def _parse_join_invite_url(invite_url: Optional[str]) -> Dict[str, str]:
    if not invite_url:
        return {}
    parsed = urlparse(invite_url)
    query = parse_qs(parsed.query)
    return {k: v[-1] for k, v in query.items() if v}


def _gateway_ws_base(gateway: str) -> str:
    base = str(gateway or "http://127.0.0.1:8000").rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):]
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):]
    if base.startswith("ws://") or base.startswith("wss://"):
        return base
    return "ws://" + base


async def _join_session_loop(
    gateway: str,
    account_id: Optional[str],
    session_id: str,
    token: Optional[str],
    participant_id: str,
    *,
    invite_token: Optional[str] = None,
    approval_token: Optional[str] = None,
    join_request_id: Optional[str] = None,
    request_approval: bool = False,
    role: str = "assistant_agent",
    client_type: str = "wolf_cli",
    reason: Optional[str] = None,
    mode: str = "message",
) -> int:
    """Collaborative websocket participant bridge for ./wolf join-session."""
    import json
    import websockets
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout

    if mode != "message":
        print(f"[warn] join mode {mode!r} is planned but not active yet; using message bridge mode.")
    if not session_id:
        raise SystemExit("join-session requires --session-id or --invite-url with session_id")

    qs: Dict[str, str] = {"participant_id": participant_id, "participant_role": role, "client_type": client_type, "join_mode": str(mode or "message").replace("-", "_")}
    if token:
        qs["token"] = token
        account = account_id or "acc_123"
    elif invite_token:
        qs["invite_token"] = invite_token
        account = account_id or "invite"
    elif approval_token:
        qs["approval_token"] = approval_token
        if join_request_id:
            qs["join_request_id"] = join_request_id
        account = account_id or "approved"
    elif request_approval:
        qs["request_join"] = "true"
        if reason:
            qs["reason"] = reason
        account = account_id or "public"
    else:
        raise SystemExit("join-session requires one auth source: --token, --invite-token, --approval-token, or --request-approval")

    ws_url = f"{_gateway_ws_base(gateway)}/ws/{quote(str(account))}/{quote(str(session_id))}?{urlencode(qs)}"
    session = PromptSession()

    async with websockets.connect(ws_url) as ws:
        first = json.loads(await ws.recv())
        print(f"[{first.get('type')}] {first.get('content', '')}")
        if first.get("type") == "join_pending":
            async for raw in ws:
                data = json.loads(raw)
                print(f"[{data.get('type')}] {data.get('content', '')}")
                if data.get("type") == "join_approved" and data.get("approval_token"):
                    print("[join] approved; reconnecting with approval token...")
                    return await _join_session_loop(
                        gateway,
                        account_id or "approved",
                        session_id,
                        None,
                        participant_id,
                        approval_token=data.get("approval_token"),
                        join_request_id=data.get("request_id"),
                        role=data.get("role") or role,
                        client_type=client_type,
                        mode=mode,
                    )
                if data.get("type") in {"join_rejected", "error"}:
                    return 1
            return 1

        async def receiver():
            async for raw in ws:
                data = json.loads(raw)
                print(f"[{data.get('type')}] {data.get('sender') or data.get('participant_id') or ''}: {data.get('content', '')}")

        def _participant_message_payload(line: str) -> Dict[str, object]:
            text = str(line or "")
            stripped = text.strip()
            payload: Dict[str, object] = {
                "type": "participant_message",
                "content": text,
                "sender": participant_id,
            }
            if stripped.startswith("/dm ") or stripped.startswith("/to "):
                parts = stripped.split(maxsplit=2)
                if len(parts) < 3:
                    raise ValueError("usage: /dm <participant_id> <message>")
                payload.update({
                    "content": parts[2],
                    "to_participant_id": parts[1],
                    "visibility": "direct",
                })
            elif stripped.startswith("/role "):
                parts = stripped.split(maxsplit=2)
                if len(parts) < 3:
                    raise ValueError("usage: /role <role> <message>")
                payload.update({
                    "content": parts[2],
                    "to_role": parts[1],
                    "visibility": "direct",
                })
            elif stripped.startswith("/private "):
                payload.update({
                    "content": stripped[len("/private "):],
                    "visibility": "private",
                })
            return payload

        print("[join] commands: /dm <participant> <msg>, /role <role> <msg>, /private <msg>, /quit")
        recv_task = asyncio.create_task(receiver())
        try:
            while True:
                with patch_stdout(raw=True):
                    line = await session.prompt_async(f"[{participant_id}] » ")
                if line.strip() in {"/quit", "/exit", "quit", "exit"}:
                    break
                try:
                    await ws.send(json.dumps(_participant_message_payload(line)))
                except ValueError as exc:
                    print(f"[join] {exc}")
        finally:
            recv_task.cancel()
    return 0


def launch_join_session(
    config: Dict[str, Any],
    *,
    gateway: Optional[str] = None,
    account_id: Optional[str] = None,
    session_id: Optional[str] = None,
    token: Optional[str] = None,
    invite_token: Optional[str] = None,
    invite_url: Optional[str] = None,
    approval_token: Optional[str] = None,
    join_request_id: Optional[str] = None,
    request_approval: bool = False,
    role: str = "assistant_agent",
    client_type: str = "wolf_cli",
    participant_id: str = "wolf_cli_agent",
    reason: Optional[str] = None,
    mode: str = "message",
    dry_run: bool = False,
    explain: bool = False,
) -> int:
    parsed = _parse_join_invite_url(invite_url)
    gateway = gateway or parsed.get("gateway") or "http://127.0.0.1:8000"
    session_id = session_id or parsed.get("session_id")
    invite_token = invite_token or parsed.get("invite_token")
    role = role or parsed.get("role") or "assistant_agent"
    if explain or dry_run:
        print("Launch plan:")
        print("  mode: cli join-session")
        print(f"  gateway: {gateway}")
        print(f"  account_id: {account_id or '<derived>'}")
        print(f"  session_id: {session_id}")
        print(f"  participant_id: {participant_id}")
        print(f"  role: {role}")
        print(f"  client_type: {client_type}")
        print(f"  auth: {'token' if token else 'invite_token' if invite_token else 'approval_token' if approval_token else 'request_approval' if request_approval else 'none'}")
        print(f"  join_mode: {mode}")
        print("  current status: message-level participant bridge; agent-backed modes are planned")
    if dry_run:
        return 0
    return asyncio.run(_join_session_loop(
        gateway,
        account_id,
        str(session_id or ""),
        token,
        participant_id,
        invite_token=invite_token,
        approval_token=approval_token,
        join_request_id=join_request_id,
        request_approval=request_approval,
        role=role,
        client_type=client_type,
        reason=reason,
        mode=mode,
    ))
