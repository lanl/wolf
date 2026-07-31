from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from framework.cli.config_loader import print_launch_config


def launch_cli(config: Dict[str, Any], *, dry_run: bool = False, explain: bool = False) -> int:
    from framework.utils.config_tools import CliSession
    from framework.workflows.workflow_space import get_workflow_class

    workflow_name = config.get("workflow") or "TurnBasedWorkflow"
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
    static_dir: str = "./framework/ui/webapp",
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


async def _join_session_loop(gateway: str, account_id: str, session_id: str, token: str, participant_id: str, agent_name: str = "joined_agent") -> int:
    """Minimal websocket participant bridge for ./wolf cli --join-session.

    This first version is intentionally message-level: it joins the gateway
    session as an entity and can send/receive participant messages. A later
    milestone can attach a full local workflow/agent loop.
    """
    import json
    import websockets
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout

    base = gateway.rstrip("/").replace("http://", "").replace("https://", "")
    ws_url = f"ws://{base}/ws/{account_id}/{session_id}?token={token}&participant_id={participant_id}&participant_role=agent&client_type=wolf_cli"
    session = PromptSession()

    async with websockets.connect(ws_url) as ws:
        print(await ws.recv())

        async def receiver():
            async for raw in ws:
                data = json.loads(raw)
                print(f"[{data.get('type')}] {data.get('sender') or data.get('participant_id') or ''}: {data.get('content', '')}")

        recv_task = asyncio.create_task(receiver())
        try:
            while True:
                with patch_stdout(raw=True):
                    line = await session.prompt_async(f"[{participant_id}] » ")
                if line.strip() in {"/quit", "/exit", "quit", "exit"}:
                    break
                await ws.send(json.dumps({
                    "type": "participant_message",
                    "content": line,
                    "sender": participant_id,
                }))
        finally:
            recv_task.cancel()
    return 0


def launch_join_session(config: Dict[str, Any], *, gateway: str, account_id: str, session_id: str, token: str, participant_id: str, dry_run: bool = False, explain: bool = False) -> int:
    if explain or dry_run:
        print("Launch plan:")
        print("  mode: cli join-session")
        print(f"  gateway: {gateway}")
        print(f"  account_id: {account_id}")
        print(f"  session_id: {session_id}")
        print(f"  participant_id: {participant_id}")
        print("  current status: message-level participant bridge")
    if dry_run:
        return 0
    return asyncio.run(_join_session_loop(gateway, account_id, session_id, token, participant_id))
