from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar

from pydantic import BaseModel

from .tool_models import (
    ToolSpec, ToolSource, ToolPackage, ToolDeployment, ToolEndpoint,
    ToolRun, ReadinessReport, utc_now,
)

T = TypeVar('T', bound=BaseModel)


def model_to_dict(obj: BaseModel) -> Dict[str, Any]:
    if hasattr(obj, 'model_dump'):
        return obj.model_dump(mode='json')  # pydantic v2
    return obj.dict()


class JsonRegistry(Generic[T]):
    """Small rehydratable JSON registry for Toolbox v4 MVP.

    This is intentionally simple and dependency-light. It can later be replaced
    by SQLite/Postgres/artifact stores without changing the facade API.
    """

    def __init__(self, path: str | Path, model_cls: Type[T]):
        self.path = Path(path)
        self.model_cls = model_cls
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            self._data = payload.get('items', {}) if isinstance(payload, dict) else {}
        except Exception:
            self._data = {}

    def save(self) -> None:
        payload = {
            'schema': 'wolf.toolbox.v4.registry/1',
            'model': self.model_cls.__name__,
            'updated_at': utc_now(),
            'items': self._data,
        }
        tmp = self.path.with_suffix(self.path.suffix + '.tmp')
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
        tmp.replace(self.path)

    def upsert(self, obj: T) -> T:
        obj_id = getattr(obj, 'id')
        self._data[obj_id] = model_to_dict(obj)
        self.save()
        return obj

    def get(self, obj_id: str) -> Optional[T]:
        data = self._data.get(obj_id)
        if data is None:
            return None
        return self.model_cls(**data)

    def require(self, obj_id: str) -> T:
        obj = self.get(obj_id)
        if obj is None:
            raise KeyError(f'{self.model_cls.__name__} not found: {obj_id}')
        return obj

    def delete(self, obj_id: str) -> bool:
        existed = obj_id in self._data
        if existed:
            del self._data[obj_id]
            self.save()
        return existed

    def list(self) -> List[T]:
        return [self.model_cls(**data) for data in self._data.values()]

    def raw_items(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._data)

    def find(self, **filters: Any) -> List[T]:
        out: List[T] = []
        for obj in self.list():
            ok = True
            for key, value in filters.items():
                if getattr(obj, key, None) != value:
                    ok = False
                    break
            if ok:
                out.append(obj)
        return out

    def update_fields(self, obj_id: str, **fields: Any) -> T:
        obj = self.require(obj_id)
        data = model_to_dict(obj)
        data.update(fields)
        if 'updated_at' in data:
            data['updated_at'] = utc_now()
        new_obj = self.model_cls(**data)
        return self.upsert(new_obj)

    def count(self) -> int:
        return len(self._data)


class ToolRegistry(JsonRegistry[ToolSpec]):
    def __init__(self, path: str | Path):
        super().__init__(path, ToolSpec)

    def get_by_name(self, name: str, version: Optional[str] = None) -> Optional[ToolSpec]:
        candidates = [t for t in self.list() if t.name == name]
        if version is not None:
            candidates = [t for t in candidates if t.version == version]
        return candidates[0] if candidates else None

    def search_text(self, query: str) -> List[ToolSpec]:
        q = query.lower().strip()
        if not q:
            return self.list()
        hits = []
        for t in self.list():
            hay = ' '.join([t.name, t.description, ' '.join(t.capabilities), t.status]).lower()
            if q in hay or all(part in hay for part in q.split()):
                hits.append(t)
        return hits


class SourceRegistry(JsonRegistry[ToolSource]):
    def __init__(self, path: str | Path):
        super().__init__(path, ToolSource)


class PackageRegistry(JsonRegistry[ToolPackage]):
    def __init__(self, path: str | Path):
        super().__init__(path, ToolPackage)


class DeploymentRegistry(JsonRegistry[ToolDeployment]):
    def __init__(self, path: str | Path):
        super().__init__(path, ToolDeployment)


class EndpointRegistry(JsonRegistry[ToolEndpoint]):
    def __init__(self, path: str | Path):
        super().__init__(path, ToolEndpoint)


class RunRegistry(JsonRegistry[ToolRun]):
    def __init__(self, path: str | Path):
        super().__init__(path, ToolRun)


class ReadinessRegistry(JsonRegistry[ReadinessReport]):
    def __init__(self, path: str | Path):
        super().__init__(path, ReadinessReport)


class ToolboxRegistries:
    def __init__(self, registry_dir: str | Path):
        d = Path(registry_dir)
        d.mkdir(parents=True, exist_ok=True)
        self.tools = ToolRegistry(d / 'tools.json')
        self.sources = SourceRegistry(d / 'sources.json')
        self.packages = PackageRegistry(d / 'packages.json')
        self.deployments = DeploymentRegistry(d / 'deployments.json')
        self.endpoints = EndpointRegistry(d / 'endpoints.json')
        self.runs = RunRegistry(d / 'runs.json')
        self.readiness = ReadinessRegistry(d / 'readiness.json')

    def stats(self) -> Dict[str, int]:
        return {
            'tools': self.tools.count(),
            'sources': self.sources.count(),
            'packages': self.packages.count(),
            'deployments': self.deployments.count(),
            'endpoints': self.endpoints.count(),
            'runs': self.runs.count(),
            'readiness_reports': self.readiness.count(),
        }
