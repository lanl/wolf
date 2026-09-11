from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from framework.universes.data_models import BaseUniverseParams


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class UniverseRuntimeSpec:
    """Backend-agnostic request to launch one Universe runtime."""

    name: str
    params: BaseUniverseParams
    system: str = "local"
    cors: list[str] | None = None
    startup_timeout_s: float = 60.0
    runtime_dir: str | Path | None = None
    env: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UniverseEndpoint:
    scheme: str = "http"
    host: str = "127.0.0.1"
    port: int = 0
    url: str | None = None

    def __post_init__(self) -> None:
        self.port = int(self.port or 0)
        if not self.url and self.port > 0:
            self.url = f"{self.scheme}://{self.host}:{self.port}"

    @property
    def usable(self) -> bool:
        return bool(self.host) and int(self.port or 0) > 0

    def as_dict(self) -> Dict[str, Any]:
        return {"scheme": self.scheme, "host": self.host, "port": self.port, "url": self.url}


@dataclass
class UniverseStatus:
    name: str
    backend: str
    state: str = "unknown"
    endpoint: UniverseEndpoint | None = None
    pid: int | None = None
    message: str | None = None
    error: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=utc_now_iso)

    def as_dict(self) -> Dict[str, Any]:
        data = {
            "name": self.name,
            "backend": self.backend,
            "state": self.state,
            "pid": self.pid,
            "message": self.message,
            "error": self.error,
            "metadata": self.metadata,
            "updated_at": self.updated_at,
        }
        if self.endpoint is not None:
            data["endpoint"] = self.endpoint.as_dict()
        return data


@dataclass
class UniverseHandle:
    name: str
    backend: str
    state: str = "starting"
    endpoint: UniverseEndpoint | None = None
    runtime_spec: UniverseRuntimeSpec | None = None
    process: Any = None
    files: Dict[str, str] = field(default_factory=dict)
    logs: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def legacy_entry(self, params_serializable: Dict[str, Any]) -> Dict[str, Any]:
        """Return the existing infra.managed_deployments entry shape.

        Keeping this shim lets existing list/terminate code, snapshots, tests,
        and downstream UI continue to work while Phase 2 introduces backends.
        """
        meta = dict(self.metadata or {})
        meta.setdefault("type", "universe")
        meta.setdefault("status", self.state)
        meta.setdefault("deployment_type", self.backend)
        meta.setdefault("backend", self.backend)
        meta.setdefault("created_at", self.created_at)
        if self.process is not None and hasattr(self.process, "pid"):
            meta.setdefault("subprocess_pid", self.process.pid)
        if self.endpoint is not None and self.endpoint.usable:
            meta.update({"host": self.endpoint.host, "port": self.endpoint.port, "url": self.endpoint.url})

        entry = {
            "handle": self.process if self.process is not None else self,
            "backend_handle": self,
            "backend": self.backend,
            "params": params_serializable,
            "meta_data": meta,
        }
        for key, value in self.files.items():
            entry[key] = value
        for key, value in self.logs.items():
            entry[key] = value
        if self.endpoint is not None:
            entry["endpoint"] = self.endpoint.as_dict()
        return entry
