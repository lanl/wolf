from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .client import GatewayClient
from .server import GatewayServer
from .visualizer import build_dashboard, build_task_detail_panel, build_task_history_panel


HELP_TEXT = """Commands:
  /submit <objective>              submit a new root task
  /focus <task_id|task_name>       focus a task for follow-up messages
  /msg <text>                      send a user message to the focused task
  /session <text>                  submit a new root task from session-level text
  /pause [task]                    pause focused or specified task
  /resume [task]                   resume focused or specified task
  /cancel [task]                   cancel focused or specified task
  /retry [task]                    retry focused or specified task
  /detail [task]                   show task details
  /history [task]                  show task chat/substeps/history
  /graph                           show dashboard snapshot
  /tasks                           list tasks in session
  /help                            show this help
  /quit                            exit

Bare text is sent to the focused task. If no task is focused, it becomes a new root task.
"""


class GatewayCompleter(Completer):
    def __init__(self, tui: 'GatewayTUI') -> None:
        self.tui = tui
        self.commands = [
            '/submit', '/focus', '/msg', '/session', '/pause', '/resume', '/cancel', '/retry',
            '/detail', '/history', '/graph', '/tasks', '/help', '/quit',
        ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text:
            for cmd in self.commands:
                yield Completion(cmd, start_position=0)
            return
        if text.startswith('/'):
            parts = text.split()
            if len(parts) <= 1 and not text.endswith(' '):
                for cmd in self.commands:
                    if cmd.startswith(parts[0]):
                        yield Completion(cmd, start_position=-len(parts[0]))
                return
            cmd = parts[0]
            token = '' if text.endswith(' ') else parts[-1]
            if cmd in {'/focus', '/detail', '/history', '/pause', '/resume', '/cancel', '/retry'}:
                for opt in self.tui.current_task_tokens():
                    if not token or opt.startswith(token):
                        yield Completion(opt, start_position=-len(token))


@dataclass(slots=True)
class GatewayTUI:
    gateway: GatewayServer
    refresh_hz: int = 2
    state_root: str = '.gateway/tui_state'
    console: Console = field(init=False, repr=False)
    _stop: asyncio.Event = field(init=False, repr=False)
    _focused_task_id: Optional[str] = field(init=False, default=None, repr=False)
    _session_id: Optional[str] = field(init=False, default=None, repr=False)
    _last_snapshot: dict = field(init=False, default_factory=dict, repr=False)
    _last_detail: dict | None = field(init=False, default=None, repr=False)
    _completer: GatewayCompleter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.console = Console()
        self._stop = asyncio.Event()
        self._focused_task_id = None
        self._session_id = None
        self._last_snapshot = {}
        self._last_detail = None
        self._completer = GatewayCompleter(self)

    def current_task_tokens(self) -> list[str]:
        tokens: list[str] = []
        for task in self._last_snapshot.get('tasks', []):
            tokens.extend([task.id[:8], task.id, task.spec.name])
        seen = set()
        out = []
        for tok in tokens:
            if tok and tok not in seen:
                seen.add(tok)
                out.append(tok)
        return out

    def _state_path(self, session_id: str) -> Path:
        root = Path(self.state_root)
        root.mkdir(parents=True, exist_ok=True)
        return root / f'{session_id}.json'

    def _history_path(self, session_id: str) -> Path:
        root = Path(self.state_root)
        root.mkdir(parents=True, exist_ok=True)
        return root / f'{session_id}.history'

    def _load_state(self, session_id: str) -> dict:
        path = self._state_path(session_id)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return {}
        return {}

    def _save_state(self) -> None:
        if not self._session_id:
            return
        payload = {'focused_task_id': self._focused_task_id}
        self._state_path(self._session_id).write_text(json.dumps(payload, indent=2))

    async def _restore_focus(self, client: GatewayClient) -> None:
        state = self._load_state(client.session_id)
        snap = await client.snapshot()
        self._last_snapshot = snap
        wanted = state.get('focused_task_id')
        tasks = snap.get('tasks', [])
        by_id = {t.id: t for t in tasks}
        if wanted in by_id:
            self._focused_task_id = wanted
            return
        roots = [t for t in tasks if not t.spec.parent_id]
        roots.sort(key=lambda t: t.created_at)
        if roots:
            self._focused_task_id = roots[0].id
            self._save_state()

    async def run(self, session_id: str, duration: float | None = None) -> None:
        self._session_id = session_id
        client = GatewayClient(self.gateway, session_id)
        await self._restore_focus(client)
        session = PromptSession(
            message=HTML('<ansigreen>  » </ansigreen>'),
            completer=self._completer,
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            history=FileHistory(str(self._history_path(session_id))),
            style=Style.from_dict({
                'completion-menu.completion.current': 'bg:#005f5f #ffffff',
                'completion-menu.completion': 'bg:#002b36 #93a1a1',
                'bottom-toolbar': 'bg:#001b1b #7fffd4',
                'prompt': 'bold #00ffaf',
            }),
            bottom_toolbar=self._bottom_toolbar,
            reserve_space_for_menu=8,
        )
        self.console.print(Panel(HELP_TEXT.strip(), title='NATIONAL MISSION GATEWAY // TUI', border_style='bright_green'))
        await self._print_dashboard(client)
        watcher = asyncio.create_task(self._event_watcher(client))
        timer = None
        if duration is not None:
            timer = asyncio.create_task(self._auto_stop(duration))
        try:
            with patch_stdout(raw=True):
                while not self._stop.is_set():
                    try:
                        line = await session.prompt_async()
                    except (EOFError, KeyboardInterrupt):
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        should_continue = await self._handle_command(client, line)
                    except Exception as exc:
                        self.console.print(f'[red]error[/red] {exc}')
                        should_continue = True
                    if not should_continue:
                        break
        finally:
            self._stop.set()
            self._save_state()
            watcher.cancel()
            if timer is not None:
                timer.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            if timer is not None:
                await asyncio.gather(timer, return_exceptions=True)

    def _bottom_toolbar(self):
        focus = self._focused_task_id[:8] if self._focused_task_id else 'none'
        counts = self._last_snapshot.get('task_counts', {}) if self._last_snapshot else {}
        bits = [f' session={self._session_id[:8] if self._session_id else "-"} ', f' focus={focus} ']
        for key in ('running', 'waiting', 'ready', 'paused', 'blocked'):
            if counts.get(key):
                bits.append(f' {key}={counts[key]} ')
        bits.append(' TAB-complete · history · task-centric chat ')
        return HTML('<b>' + ' | '.join(bits) + '</b>')

    async def _auto_stop(self, duration: float) -> None:
        await asyncio.sleep(duration)
        self._stop.set()

    async def _event_watcher(self, client: GatewayClient) -> None:
        async for event in client.events():
            if self._stop.is_set():
                break
            # refresh cached snapshot for completion/focus decisions
            self._last_snapshot = await client.snapshot()
            if self._focused_task_id and event.task_id == self._focused_task_id:
                try:
                    self._last_detail = await client.detail(self._focused_task_id)
                except Exception:
                    self._last_detail = None
            task = event.task_id[:8] if event.task_id else '-'
            prefix = '[bright_green][stream][/bright_green]'
            if event.type in {'task_failed', 'task_paused', 'user_input_requested'}:
                prefix = '[bright_red][stream][/bright_red]'
            elif event.type in {'task_completed', 'summary_generated', 'assistant_message'}:
                prefix = '[bright_cyan][stream][/bright_cyan]'
            self.console.print(f"{prefix} {event.type} task={task} actor={event.actor} payload={event.payload}")

    async def _handle_command(self, client: GatewayClient, line: str) -> bool:
        if not line.startswith('/'):
            await self._send_default(client, line)
            return True
        cmd, _, rest = line.partition(' ')
        rest = rest.strip()
        if cmd == '/quit':
            return False
        if cmd == '/help':
            self.console.print(Panel(HELP_TEXT.strip(), title='Gateway TUI', border_style='bright_green'))
            return True
        if cmd == '/submit':
            task_id = await client.submit(rest or 'new task', name='user-task')
            self._focused_task_id = task_id
            self._save_state()
            self.console.print(f'[green]submitted[/green] task={task_id}')
            return True
        if cmd == '/session':
            task_id = await client.submit(rest or 'session task', name='session-task')
            self._focused_task_id = task_id
            self._save_state()
            self.console.print(f'[green]submitted[/green] task={task_id}')
            return True
        if cmd == '/focus':
            task_id = await self._resolve_task_id(client, rest)
            self._focused_task_id = task_id
            self._save_state()
            self.console.print(f'[yellow]focused[/yellow] task={task_id}')
            return True
        if cmd == '/msg':
            await self._send_to_focused(client, rest)
            return True
        if cmd == '/pause':
            await client.pause(await self._resolve_task_id(client, rest or self._focused_task_id or ''))
            return True
        if cmd == '/resume':
            await client.resume(await self._resolve_task_id(client, rest or self._focused_task_id or ''))
            return True
        if cmd == '/cancel':
            await client.cancel(await self._resolve_task_id(client, rest or self._focused_task_id or ''))
            return True
        if cmd == '/retry':
            await client.retry(await self._resolve_task_id(client, rest or self._focused_task_id or ''))
            return True
        if cmd == '/detail':
            task_id = await self._resolve_task_id(client, rest or self._focused_task_id or '')
            detail = await client.detail(task_id)
            self.console.print(build_task_detail_panel(detail))
            return True
        if cmd == '/history':
            task_id = await self._resolve_task_id(client, rest or self._focused_task_id or '')
            detail = await client.detail(task_id)
            self.console.print(build_task_history_panel(detail))
            return True
        if cmd == '/graph':
            await self._print_dashboard(client)
            return True
        if cmd == '/tasks':
            await self._print_task_list(client)
            return True
        self.console.print(f'[red]unknown command[/red] {cmd}')
        return True

    async def _send_default(self, client: GatewayClient, content: str) -> None:
        if self._focused_task_id:
            await client.message(content, task_id=self._focused_task_id)
            self.console.print(f'[green]message sent[/green] task={self._focused_task_id}')
        else:
            task_id = await client.submit(content, name='chat-root')
            self._focused_task_id = task_id
            self._save_state()
            self.console.print(f'[green]submitted[/green] task={task_id}')

    async def _send_to_focused(self, client: GatewayClient, content: str) -> None:
        if not self._focused_task_id:
            self.console.print('[red]no focused task[/red]')
            return
        await client.message(content, task_id=self._focused_task_id)
        self.console.print(f'[green]message sent[/green] task={self._focused_task_id}')

    async def _resolve_task_id(self, client: GatewayClient, token: str) -> str:
        token = token.strip()
        if not token:
            raise ValueError('task id or name required')
        snap = await client.snapshot()
        self._last_snapshot = snap
        for task in snap.get('tasks', []):
            if task.id == token or task.id.startswith(token) or task.spec.name == token:
                return task.id
        raise ValueError(f'unknown task: {token}')

    async def _print_dashboard(self, client: GatewayClient) -> None:
        self._last_snapshot = await client.snapshot()
        detail = None
        if self._focused_task_id:
            try:
                detail = await client.detail(self._focused_task_id)
                self._last_detail = detail
            except Exception:
                detail = None
                self._last_detail = None
        self.console.print(build_dashboard(self._last_snapshot, focused_task_id=self._focused_task_id, focused_detail=detail))

    async def _print_task_list(self, client: GatewayClient) -> None:
        snap = await client.snapshot()
        self._last_snapshot = snap
        table = Table(title='Tasks')
        table.add_column('task_id')
        table.add_column('name')
        table.add_column('status')
        table.add_column('focused')
        for task in snap.get('tasks', []):
            table.add_row(task.id[:8], task.spec.name, task.status.value, '*' if task.id == self._focused_task_id else '')
        self.console.print(table)
