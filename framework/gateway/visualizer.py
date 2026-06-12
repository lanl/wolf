from __future__ import annotations

from typing import Any, Iterable
from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from framework.orchestration.models import TaskNode


STATUS_STYLES = {
    'running': 'bold bright_yellow',
    'waiting': 'bold magenta',
    'ready': 'bold bright_cyan',
    'completed': 'bold bright_green',
    'failed': 'bold bright_red',
    'paused': 'bold blue',
    'blocked': 'bold red',
    'cancelled': 'grey62',
    'pending': 'cyan',
}

PANEL_BORDER = 'bright_green'
TITLE_STYLE = 'bold bright_cyan'
HEADER_STYLE = 'bold bright_green'
DIM_STYLE = 'grey70'


def _status_text(status: str) -> str:
    style = STATUS_STYLES.get(status, 'white')
    return f'[{style}]{status}[/{style}]'


def build_task_tree(tasks: Iterable[TaskNode], focused_task_id: str | None = None) -> Tree:
    tasks = list(tasks)
    by_id = {t.id: t for t in tasks}
    children: dict[str, list[TaskNode]] = {}
    roots: list[TaskNode] = []
    for t in tasks:
        pid = t.spec.parent_id
        if pid and pid in by_id:
            children.setdefault(pid, []).append(t)
        else:
            roots.append(t)
    roots.sort(key=lambda t: t.created_at)
    tree = Tree(Text('MISSION DAG', style='bold bright_green'))

    def add_node(parent, task: TaskNode):
        style = STATUS_STYLES.get(task.status.value, 'white')
        label = f"[{style}]{task.spec.name}[/{style}] [{_status_text(task.status.value)}]"
        if task.leased_agent_name:
            label += f" [bright_cyan]lease={task.leased_agent_name}[/bright_cyan]"
        if task.owner_agent_name:
            label += f" [green]owner={task.owner_agent_name}[/green]"
        label += f" [grey62]id={task.id[:8]}[/grey62]"
        if focused_task_id and task.id == focused_task_id:
            label = f"[bold reverse]{label}[/bold reverse]"
        node = parent.add(label)
        for child in sorted(children.get(task.id, []), key=lambda x: x.created_at):
            add_node(node, child)

    for root in roots:
        add_node(tree, root)
    return tree


def build_agent_table(agent_rows: list[dict[str, Any]]) -> Table:
    table = Table(title='AGENT POOL', box=box.HEAVY_HEAD, header_style=HEADER_STYLE, border_style=PANEL_BORDER)
    table.add_column('name')
    table.add_column('busy')
    table.add_column('current_task_id')
    table.add_column('capabilities')
    table.add_column('model_family')
    table.add_column('context_window')
    for row in agent_rows:
        busy_style = 'bright_red' if row['busy'] else 'bright_green'
        table.add_row(
            str(row['name']),
            f"[{busy_style}]{row['busy']}[/{busy_style}]",
            str(row['current_task_id']),
            ','.join(row['capabilities']),
            str(row['model_family']),
            str(row['context_window']),
        )
    return table


def build_event_table(events: list[Any], limit: int = 12) -> Table:
    table = Table(title='RECENT EVENTS', box=box.SIMPLE_HEAVY, header_style=HEADER_STYLE, border_style=PANEL_BORDER)
    table.add_column('type')
    table.add_column('task')
    table.add_column('actor')
    table.add_column('payload')
    for ev in events[-limit:]:
        table.add_row(ev.type, ev.task_id[:8], ev.actor, str(ev.payload)[:120])
    if not events:
        table.add_row('-', '-', '-', '-')
    return table


def build_queue_table(title: str, tasks: list[TaskNode]) -> Table:
    table = Table(title=title.upper(), box=box.MINIMAL_HEAVY_HEAD, header_style=HEADER_STYLE, border_style=PANEL_BORDER)
    table.add_column('task')
    table.add_column('status')
    table.add_column('lease')
    for t in tasks[:10]:
        table.add_row(t.spec.name, _status_text(t.status.value), str(t.leased_agent_name))
    if not tasks:
        table.add_row('-', '-', '-')
    return table


def build_artifact_table(artifacts: dict[str, list[dict[str, Any]]]) -> Table:
    table = Table(title='RECENT ARTIFACTS', box=box.SIMPLE_HEAVY, header_style=HEADER_STYLE, border_style=PANEL_BORDER)
    table.add_column('task')
    table.add_column('kind')
    table.add_column('path')
    added = 0
    for task_id, rows in artifacts.items():
        for row in rows[-2:]:
            table.add_row(task_id[:8], str(row.get('kind')), str(row.get('path')))
            added += 1
            if added >= 8:
                return table
    if added == 0:
        table.add_row('-', '-', '-')
    return table


def build_counts_panel(counts: dict[str, int]) -> Panel:
    body = '  '.join(f'[bright_green]{k}[/bright_green]=[white]{v}[/white]' for k, v in counts.items() if v)
    return Panel(body or '[grey70]no tasks[/grey70]', title='TASK COUNTS', border_style=PANEL_BORDER, title_align='left')


def build_task_history_panel(detail: dict[str, Any] | None) -> Panel:
    if not detail:
        return Panel('No task selected', title='TASK HISTORY', border_style=PANEL_BORDER)
    task = detail['task']
    table = Table(title=f'Conversation + Substeps: {task.spec.name}', box=box.SIMPLE_HEAVY, header_style=HEADER_STYLE, border_style=PANEL_BORDER)
    table.add_column('kind', width=10)
    table.add_column('who/step', width=18)
    table.add_column('status', width=12)
    table.add_column('content')

    children = detail.get('children', [])
    if children:
        for child in children:
            table.add_row('subtask', f"{child['name']} [{child['id'][:8]}]", child['status'], child.get('objective', '')[:80])
    messages = detail.get('local_messages', [])
    if messages:
        for m in messages[-12:]:
            table.add_row('message', str(m.get('role', '-')), '-', str(m.get('content', ''))[:140])
    events = detail.get('events', [])
    if events:
        for ev in events[-8:]:
            table.add_row('event', str(ev.get('type', '-')), '-', str(ev.get('payload', ''))[:120])
    if not children and not messages and not events:
        table.add_row('-', '-', '-', '-')
    return Panel(table, title='TASK HISTORY', border_style=PANEL_BORDER)


def build_task_detail_panel(detail: dict[str, Any] | None) -> Panel:
    if not detail:
        return Panel('No task selected', title='TASK DETAIL', border_style=PANEL_BORDER)
    task = detail['task']
    table = Table(title=f'Task Detail: {task.spec.name}', box=box.SIMPLE_HEAVY, header_style=HEADER_STYLE, border_style=PANEL_BORDER)
    table.add_column('field')
    table.add_column('value')
    table.add_row('task_id', task.id)
    table.add_row('status', task.status.value)
    table.add_row('objective', str(task.spec.objective)[:200])
    table.add_row('workflow', task.spec.workflow_type)
    table.add_row('owner', str(task.owner_agent_name))
    table.add_row('lease', str(task.leased_agent_name))
    table.add_row('compressed', ' | '.join(detail.get('compressed_history', [])[-4:]) or '-')
    if detail.get('child_summaries'):
        table.add_row('child_summaries', '; '.join(f"{k[:8]}={v}" for k, v in list(detail['child_summaries'].items())[-4:]))
    if detail.get('local_messages'):
        msgs = detail['local_messages'][-4:]
        table.add_row('messages', ' | '.join(f"{m['role']}: {str(m['content'])[:40]}" for m in msgs))
    if detail.get('children'):
        table.add_row('subtasks', '; '.join(f"{c['name']}[{c['id'][:8]}]={c['status']}" for c in detail['children'][-6:]))
    return Panel(table, title='FOCUSED TASK', border_style=PANEL_BORDER)


def build_dashboard(snapshot: dict[str, Any], focused_task_id: str | None = None, focused_detail: dict[str, Any] | None = None) -> Panel:
    task_tree = build_task_tree(snapshot.get('tasks', []), focused_task_id=focused_task_id)
    agent_table = build_agent_table(snapshot.get('agent_pool', []))
    event_table = build_event_table(snapshot.get('events', []))
    running = build_queue_table('Running Tasks', snapshot.get('queues', {}).get('running', []))
    waiting = build_queue_table('Waiting Tasks', snapshot.get('queues', {}).get('waiting', []))
    artifact_table = build_artifact_table(snapshot.get('artifacts', {}))
    counts = build_counts_panel(snapshot.get('task_counts', {}))
    detail_panel = build_task_detail_panel(focused_detail)
    history_panel = build_task_history_panel(focused_detail)
    title = f"NATIONAL MISSION GATEWAY // SESSION {snapshot.get('session_id', '')[:8]}"
    return Panel(Group(task_tree, counts, running, waiting, detail_panel, history_panel, agent_table, artifact_table, event_table), title=title, border_style=PANEL_BORDER, title_align='left')
