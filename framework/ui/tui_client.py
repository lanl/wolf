"""Terminal User Interface (TUI) Client V5 for WOLF Agent Gateway.

Polished UX goals:
- cleaner theme and spacing
- stable wrapping across terminal widths
- immediate async rendering while typing
- compact/comfortable density toggle
"""

import asyncio
import uuid
import sys
import os
import json
import shlex
import threading
from datetime import datetime
from typing import Optional

# Load .env for API Key
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import websockets
except ImportError:
    print("Error: websockets package not installed. Install with: pip install websockets")
    sys.exit(1)

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.patch_stdout import patch_stdout
except Exception as e:
    print("Error importing prompt_toolkit components.")
    print(f"Underlying exception: {type(e).__name__}: {e}")
    print(f"Python executable: {sys.executable}")
    print("Try: uv sync --all-extras or pip install prompt_toolkit")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich.text import Text
    from rich.prompt import Prompt as RichPrompt, Confirm
    from rich.table import Table
except ImportError:
    print("Error: rich package not installed. Install with: pip install rich")
    sys.exit(1)


class WolfCommandCompleter(Completer):
    def __init__(self):
        self.commands = {
            "/show": {"agent": {"params": {}}},
            "/config": {"agent": {"params": {}}},
            "/theme": {},
            "/reset": {},
            "/quit": {},
            "/exit": {},
            "/help": {},
        }
        self.agent_params = [
            "model", "host_address", "host_port", "api_key", "sys_prompt", "verbose", "api_version"
        ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        parts = text.split()

        if not parts or (len(parts) == 1 and not text.endswith(" ")):
            current = text
            for cmd in self.commands:
                if cmd.startswith(current):
                    yield Completion(cmd[len(current):], start_position=-len(current))
            return

        cursor_part = parts[-1] if not text.endswith(" ") else ""
        depth = len(parts) - 1

        node = self.commands
        valid_path = True
        for i in range(depth):
            p = parts[i]
            if node and p in node:
                node = node[p]
            else:
                valid_path = False
                break

        if not valid_path:
            return

        if len(parts) >= 3 and parts[0] == "/config" and parts[1] == "agent" and parts[2] == "params":
            if depth > 3 or (depth == 3 and not text.endswith(" ")):
                last_word = cursor_part
                for param in self.agent_params:
                    if param.startswith(last_word):
                        yield Completion(param[len(last_word):], start_position=-len(last_word))
            else:
                for param in self.agent_params:
                    yield Completion(f"{param}=", start_position=0)
            return

        if parts and parts[0] == "/theme":
            for opt in ["compact", "comfortable"]:
                if opt.startswith(cursor_part):
                    yield Completion(opt[len(cursor_part):], start_position=-len(cursor_part))
            return

        if isinstance(node, dict):
            for key in node:
                if key.startswith(cursor_part):
                    yield Completion(key[len(cursor_part):], start_position=-len(cursor_part))


class WolfTUIClient:
    def __init__(self, gateway_url: str = "http://127.0.0.1:8000", session_id: Optional[str] = None):
        self.gateway_url = gateway_url.rstrip("/")
        self.session_id = session_id or str(uuid.uuid4())
        self.console = Console(soft_wrap=True, highlight=False)
        self.websocket = None
        self.connected = False
        self.agent_configured = False
        self.agent_name = "WOLF"
        self.account_id: Optional[str] = None
        self.token: Optional[str] = None
        self.current_session_id: Optional[str] = None
        self.participant_id = "tui"
        self.env_api_key = os.getenv("API_KEY")
        self.print_lock = threading.Lock()

        self.compact_mode = True
        self.max_panel_width = 96

        self.prompt_session = PromptSession(
            history=FileHistory(".wolf_tui_history"),
            auto_suggest=AutoSuggestFromHistory(),
            completer=WolfCommandCompleter(),
        )

    def _safe_print(self, renderable):
        with self.print_lock:
            self.console.print(renderable)

    def _bubble(self, title: str, content: str, border_style: str = "cyan", markdown: bool = False):
        width = min(self.max_panel_width, max(72, self.console.size.width - 4))
        body = Markdown(content) if markdown else content
        panel = Panel(
            body,
            title=title,
            border_style=border_style,
            width=width,
            expand=False,
            padding=(0 if self.compact_mode else 1, 1),
        )
        self._safe_print(panel)
        if not self.compact_mode:
            self._safe_print("")

    def _redact_value(self, key: str, value):
        key_l = str(key).lower()
        if any(secret in key_l for secret in ["api_key", "token", "password", "secret", "authorization"]):
            if value in (None, ""):
                return value
            text = str(value)
            return "***REDACTED***" if len(text) <= 8 else f"{text[:4]}...{text[-4:]}"
        return value

    def _redact_mapping(self, value):
        if isinstance(value, dict):
            return {k: self._redact_value(k, self._redact_mapping(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_mapping(v) for v in value]
        return value

    def _status_line(self) -> Text:
        t = Text()
        t.append("● ", style="bold green" if self.connected else "bold red")
        t.append("ONLINE" if self.connected else "OFFLINE", style="bold")
        t.append("  │  ", style="dim")
        t.append(f"acct: {self.account_id or '-'}", style="cyan")
        t.append("  │  ", style="dim")
        t.append(f"sid: {self.current_session_id or '-'}", style="magenta")
        t.append("  │  ", style="dim")
        t.append("compact" if self.compact_mode else "comfortable", style="yellow")
        return t

    async def _http_request(self, method: str, endpoint: str, json_data: dict = None, params: dict = None) -> dict:
        try:
            import requests
        except ImportError:
            self._safe_print("[red]Error: requests package not installed.[/red]")
            sys.exit(1)

        url = f"{self.gateway_url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        request_params = params or {}
        if self.token:
            request_params["token"] = self.token

        try:
            if method == "GET":
                response = requests.get(url, params=request_params, headers=headers)
            elif method == "POST":
                response = requests.post(url, json=json_data, params=request_params, headers=headers)
            elif method == "PATCH":
                response = requests.patch(url, json=json_data, params=request_params, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self._bubble("Error", f"HTTP request failed: {e}", border_style="red")
            return {}

    async def authenticate(self):
        username = RichPrompt.ask("Username")
        password = RichPrompt.ask("Password", password=True)

        self._safe_print("[yellow]Authenticating...[/yellow]")
        data = await self._http_request("POST", "/auth/login", {"username": username, "password": password})

        if not data or "token" not in data:
            self._bubble("Auth", "Authentication failed.", border_style="red")
            return False

        self.token = data["token"]
        self.account_id = data["account_id"]
        sessions = data.get("sessions", [])

        self._safe_print(f"[green]✓ Authenticated as {self.account_id}[/green]")

        if sessions:
            table = Table(title="Session History", show_header=True, header_style="bold cyan", expand=False)
            table.add_column("#", style="dim", width=3)
            table.add_column("Session ID", style="magenta")
            table.add_column("Created", style="cyan")
            table.add_column("State", style="green")
            for i, sess in enumerate(sessions, start=1):
                state = "Active" if sess.get("active") else "Inactive"
                table.add_row(str(i), str(sess.get("session_id", "")), str(sess.get("created_at", "")), state)
            self._safe_print(table)
            choice = RichPrompt.ask("Select session", choices=[str(i) for i in range(1, len(sessions) + 1)], default="1")
            self.current_session_id = sessions[int(choice) - 1]["session_id"]
        else:
            self._safe_print("[dim]No previous sessions found. Creating a new one.[/dim]")
            self.current_session_id = str(uuid.uuid4())

        return True

    async def sync_api_key(self):
        if not self.env_api_key:
            self._safe_print("[yellow]Warning: API_KEY not found in .env file.[/yellow]")
            return

        params = await self._http_request("GET", f"/sessions/{self.current_session_id}/params")
        if not params:
            self._bubble("Config", "Could not retrieve agent params from gateway.", border_style="red")
            return

        gw_api_key = params.get("api_key")
        if not gw_api_key:
            await self._http_request("PATCH", f"/sessions/{self.current_session_id}/params", {"api_key": self.env_api_key})
            self._safe_print("[green]Gateway API key initialized from .env.[/green]")
        elif gw_api_key != self.env_api_key:
            self._safe_print("[bold yellow]API key mismatch detected.[/bold yellow]")
            if Confirm.ask("Use API key from .env?"):
                await self._http_request("PATCH", f"/sessions/{self.current_session_id}/params", {"api_key": self.env_api_key})
                self._safe_print("[green]Gateway updated.[/green]")

    async def connect(self):
        if not self.token or not self.account_id or not self.current_session_id:
            self._bubble("Error", "Not authenticated. Please authenticate first.", border_style="red")
            return False

        try:
            ws_url = f"ws://{self.gateway_url.replace('http://', '').replace('https://', '')}/ws/{self.account_id}/{self.current_session_id}?token={self.token}&participant_id={self.participant_id}&participant_role=user&client_type=tui"
            self.websocket = await websockets.connect(ws_url)
            self.connected = True

            welcome_msg = await self.websocket.recv()
            data = json.loads(welcome_msg)
            self._bubble("System", data.get("content", "Connected."), border_style="cyan")
            self._safe_print(self._status_line())
            self.agent_configured = True
            return True
        except Exception as e:
            self._bubble("Error", f"Connection failed: {e}", border_style="red")
            return False

    async def disconnect(self):
        if self.websocket:
            await self.websocket.close()
        self.connected = False
        self._safe_print("[yellow]Connection closed[/yellow]")

    async def send_message(self, content: str):
        if not self.connected or not self.websocket:
            self._bubble("Error", "Not connected to gateway.", border_style="red")
            return
        await self.websocket.send(json.dumps({
            "type": "chat",
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "session_id": self.current_session_id,
        }))

    async def receive_messages(self):
        try:
            async for message in self.websocket:
                await self.handle_message(json.loads(message))
        except websockets.exceptions.ConnectionClosed:
            self._safe_print("[yellow]Connection closed by server[/yellow]")
            self.connected = False
        except Exception as e:
            self._bubble("Error", f"Receive loop failed: {e}", border_style="red")

    async def handle_message(self, data: dict):
        msg_type = data.get("type", "unknown")
        content = data.get("content", "")

        if msg_type == "system":
            self._bubble("System", content, border_style="cyan")
        elif msg_type == "user_echo":
            self._bubble("You", content, border_style="green")
        elif msg_type == "agent_response":
            agent_name = data.get("agent_name", self.agent_name)
            self._bubble(agent_name, content, border_style="magenta", markdown=True)
        elif msg_type == "workflow_status":
            status = data.get("status", "status")
            step = data.get("step", 0)
            if status in {"received", "thinking", "executing"}:
                self._safe_print(f"[dim]• workflow {status} (step {step})[/dim]")
            elif status == "done":
                self._safe_print(f"[dim]✓ {content or 'workflow done'}[/dim]")
            else:
                self._safe_print(f"[dim]workflow {status}: {content}[/dim]")
        elif msg_type == "workflow_action":
            action = data.get("action") or (data.get("payload") or {}).get("action", "action")
            payload = data.get("payload", {})
            formatted = json.dumps(payload, indent=2, ensure_ascii=False)
            self._bubble(f"Action: {action}", formatted, border_style="yellow")
        elif msg_type == "workflow_result":
            action = data.get("action", "action")
            if action == "send_message":
                self._bubble(self.agent_name, content, border_style="magenta", markdown=True)
            else:
                body = content
                if not body and "result" in data:
                    body = json.dumps(data.get("result"), indent=2, ensure_ascii=False)
                self._bubble(f"Result: {action}", str(body), border_style="blue", markdown=False)
        elif msg_type == "workflow_error":
            self._bubble("Workflow Error", content or data.get("error", "Unknown workflow error"), border_style="red")
        elif msg_type == "policy_resolved":
            policy = data.get("action_policy", "safe")
            actions = data.get("resolved_action_names", [])
            self._safe_print(f"[dim]policy: {policy} | actions: {actions}[/dim]")
        elif msg_type == "presence":
            self._safe_print(f"[dim]presence: {content}[/dim]")
        elif msg_type == "participant_message":
            sender = data.get("sender") or data.get("participant_id") or "participant"
            self._bubble(str(sender), content, border_style="white", markdown=True)
        elif msg_type == "error":
            self._bubble("Error", content, border_style="red")
        elif msg_type == "ping" and self.websocket:
            await self.websocket.send(json.dumps({"type": "pong", "timestamp": datetime.now().isoformat()}))

    def display_welcome(self):
        header = Text()
        header.append("\n╭──────────────────────────────────────────────────────╮\n", style="bold cyan")
        header.append("│                 WOLF Terminal V5                    │\n", style="bold white")
        header.append("╰──────────────────────────────────────────────────────╯\n", style="bold cyan")
        header.append("Commands: /show agent params | /config agent params key='value' | /theme compact|comfortable | /reset | /help | /quit\n", style="dim")
        self._safe_print(header)

    def _parse_config_updates(self, raw: str) -> dict:
        updates = {}
        for tok in shlex.split(raw):
            if "=" in tok:
                k, v = tok.split("=", 1)
                updates[k] = v
        return updates

    async def handle_command(self, user_input: str):
        line = user_input.strip()
        if not line:
            return

        if line in ["/quit", "/exit"]:
            self.connected = False
            return

        if line.startswith("/help"):
            self._bubble("Help", "Use tab completion for /show and /config. Toggle density with /theme compact or /theme comfortable.", border_style="blue")
            return

        if line.startswith("/theme"):
            parts = line.split()
            if len(parts) == 2 and parts[1] in ["compact", "comfortable"]:
                self.compact_mode = parts[1] == "compact"
                self._safe_print(self._status_line())
            else:
                self._safe_print("[yellow]Usage: /theme compact|comfortable[/yellow]")
            return

        if line.startswith("/reset"):
            data = await self._http_request("POST", f"/sessions/{self.current_session_id}/reset")
            self._safe_print("[green]✓ Agent context reset.[/green]" if data.get("status") == "reset" else "[red]Failed to reset agent context.[/red]")
            return

        if line.startswith("/show agent params"):
            params = await self._http_request("GET", f"/sessions/{self.current_session_id}/params")
            if params:
                formatted = "\n".join([f"**{k}**: {v}" for k, v in params.items()])
                self._bubble("Agent Params", formatted, border_style="blue", markdown=True)
            else:
                self._safe_print("[red]Could not retrieve parameters.[/red]")
            return

        if line.startswith("/config agent params"):
            raw = line.replace("/config agent params", "", 1).strip()
            updates = self._parse_config_updates(raw)
            if updates:
                data = await self._http_request("PATCH", f"/sessions/{self.current_session_id}/params", updates)
                safe_updates = self._redact_mapping(data.get("updated_params") or updates)
                self._safe_print(f"[green]✓ Updated: {safe_updates}[/green]" if data.get("status") == "updated" else "[red]Failed to update parameters.[/red]")
            else:
                self._safe_print("[yellow]Usage: /config agent params key='value'[/yellow]")
            return

        self._safe_print("[yellow]Unknown command. Type /help[/yellow]")

    async def heartbeat_loop(self):
        try:
            while self.connected and self.websocket:
                await asyncio.sleep(25)
                await self.websocket.send(json.dumps({"type": "ping", "timestamp": datetime.now().isoformat()}))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def run(self):
        self.display_welcome()

        if not await self.authenticate():
            return
        await self.sync_api_key()
        if not await self.connect():
            return

        receiver_task = asyncio.create_task(self.receive_messages())
        heartbeat_task = asyncio.create_task(self.heartbeat_loop())

        try:
            while self.connected:
                try:
                    with patch_stdout(raw=True):
                        user_input = await self.prompt_session.prompt_async("[you] » ")
                except (EOFError, KeyboardInterrupt):
                    break

                if not user_input or not user_input.strip():
                    continue

                if user_input.startswith("/"):
                    await self.handle_command(user_input)
                    continue

                if not self.agent_configured:
                    self._safe_print("[red]Please configure agent first.[/red]")
                    continue

                await self.send_message(user_input)

        finally:
            receiver_task.cancel()
            heartbeat_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await self.disconnect()
            self._safe_print("[cyan]Goodbye![/cyan]")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="WOLF Agent TUI Client V5")
    parser.add_argument("--gateway", default="http://127.0.0.1:8000", help="Gateway URL")
    parser.add_argument("--session-id", help="Session ID")
    args = parser.parse_args()

    client = WolfTUIClient(gateway_url=args.gateway, session_id=args.session_id)
    await client.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
