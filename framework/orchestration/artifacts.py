from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
import json
import shutil
import time
import uuid


@dataclass(slots=True)
class ArtifactRecord:
    artifact_id: str
    task_id: str
    path: str
    kind: str
    created_at: float
    metadata: Dict[str, Any]


class ArtifactStore:
    def __init__(self, root: str = '.gateway/artifacts', inline_limit: int = 512) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.inline_limit = inline_limit
        self._records: Dict[str, list[ArtifactRecord]] = {}

    def task_dir(self, task_id: str) -> Path:
        path = self.root / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def put_text(self, task_id: str, name: str, content: str, *, kind: str = 'text', metadata: Optional[Dict[str, Any]] = None) -> ArtifactRecord:
        path = self.task_dir(task_id) / name
        path.write_text(content, encoding='utf-8')
        return self._register(task_id, path, kind=kind, metadata=metadata)

    def put_json(self, task_id: str, name: str, payload: Any, *, kind: str = 'json', metadata: Optional[Dict[str, Any]] = None) -> ArtifactRecord:
        path = self.task_dir(task_id) / name
        path.write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
        return self._register(task_id, path, kind=kind, metadata=metadata)

    def put_file(self, task_id: str, src_path: str, *, name: Optional[str] = None, kind: str = 'file', metadata: Optional[Dict[str, Any]] = None) -> ArtifactRecord:
        src = Path(src_path)
        dst = self.task_dir(task_id) / (name or src.name)
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        return self._register(task_id, dst, kind=kind, metadata=metadata)

    def inline_or_ref(self, task_id: str, payload: Any, name_hint: str = 'payload') -> Any:
        text = str(payload)
        if len(text) <= self.inline_limit:
            return payload
        safe = name_hint.replace('/', '_').replace(' ', '_')
        record = self.put_text(task_id, f'{safe}_{uuid.uuid4().hex[:8]}.txt', text, metadata={'source': 'inline_or_ref'})
        return f'artifact://{record.task_id}/{Path(record.path).name}'

    def list_task_artifacts(self, task_id: str) -> Iterable[ArtifactRecord]:
        return list(self._records.get(task_id, []))

    def _register(self, task_id: str, path: Path, *, kind: str, metadata: Optional[Dict[str, Any]] = None) -> ArtifactRecord:
        record = ArtifactRecord(
            artifact_id=str(uuid.uuid4()),
            task_id=task_id,
            path=str(path),
            kind=kind,
            created_at=time.time(),
            metadata=metadata or {},
        )
        self._records.setdefault(task_id, []).append(record)
        return record
