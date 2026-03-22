from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from framework.orchestration.artifacts import ArtifactRecord
from framework.orchestration.models import TaskNode, TaskStatus
from enum import Enum


class SqliteStore:
    def __init__(self, path: str = '.gateway/gateway.db') -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute('create table if not exists sessions (id text primary key, created_at real, metadata text)')
            self._conn.execute('create table if not exists tasks (id text primary key, session_id text, status text, payload text, updated_at real)')
            self._conn.execute('create table if not exists events (id text primary key, session_id text, task_id text, event_type text, actor text, ts real, payload text)')
            self._conn.execute('create table if not exists artifacts (artifact_id text primary key, task_id text, session_id text, kind text, path text, created_at real, metadata text)')

    def put_session(self, session_id: str, created_at: float, metadata: Dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute('insert or replace into sessions(id, created_at, metadata) values (?, ?, ?)', (session_id, created_at, json.dumps(metadata)))

    def put_task(self, task: TaskNode) -> None:
        payload = dataclass_to_jsonable(task)
        session_id = task.spec.session_id or ''
        with self._lock, self._conn:
            self._conn.execute('insert or replace into tasks(id, session_id, status, payload, updated_at) values (?, ?, ?, ?, ?)', (task.id, session_id, task.status.value if isinstance(task.status, TaskStatus) else str(task.status), json.dumps(payload), task.updated_at))

    def put_event(self, session_id: str, event: Any) -> None:
        payload = dataclass_to_jsonable(event.payload)
        with self._lock, self._conn:
            self._conn.execute('insert or replace into events(id, session_id, task_id, event_type, actor, ts, payload) values (?, ?, ?, ?, ?, ?, ?)', (event.id, session_id, event.task_id, event.type, event.actor, event.ts, json.dumps(payload)))

    def put_artifact(self, session_id: str, record: ArtifactRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                'insert or replace into artifacts(artifact_id, task_id, session_id, kind, path, created_at, metadata) values (?, ?, ?, ?, ?, ?, ?)',
                (record.artifact_id, record.task_id, session_id, record.kind, record.path, record.created_at, json.dumps(record.metadata)),
            )

    def list_events(self, session_id: Optional[str] = None, limit: int = 200) -> List[sqlite3.Row]:
        cur = self._conn.cursor()
        if session_id:
            cur.execute('select * from events where session_id=? order by ts desc limit ?', (session_id, limit))
        else:
            cur.execute('select * from events order by ts desc limit ?', (limit,))
        return list(cur.fetchall())

    def list_artifacts(self, session_id: Optional[str] = None, task_id: Optional[str] = None, limit: int = 200) -> List[sqlite3.Row]:
        cur = self._conn.cursor()
        if task_id:
            cur.execute('select * from artifacts where task_id=? order by created_at desc limit ?', (task_id, limit))
        elif session_id:
            cur.execute('select * from artifacts where session_id=? order by created_at desc limit ?', (session_id, limit))
        else:
            cur.execute('select * from artifacts order by created_at desc limit ?', (limit,))
        return list(cur.fetchall())


def dataclass_to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        return {k: dataclass_to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): dataclass_to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [dataclass_to_jsonable(v) for v in obj]
    return obj
