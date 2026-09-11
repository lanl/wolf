from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from framework.universes.deployment_backends.models import UniverseHandle, UniverseRuntimeSpec, UniverseStatus


class UniverseBackend(ABC):
    backend_name: str = "base"

    @abstractmethod
    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        raise NotImplementedError

    @abstractmethod
    def status(self, handle: UniverseHandle) -> UniverseStatus:
        raise NotImplementedError

    def discover_endpoint(self, handle: UniverseHandle):
        return handle.endpoint

    def logs(self, handle: UniverseHandle, tail: int = 200) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for label, path in (handle.logs or {}).items():
            try:
                lines = open(path, "r", encoding="utf-8", errors="replace").read().splitlines()
                out[label] = "\n".join(lines[-max(1, int(tail)):])
            except Exception as exc:
                out[label] = f"<unable to read {path}: {exc}>"
        return out

    @abstractmethod
    def terminate(self, handle: UniverseHandle, force: bool = False, timeout: float = 10.0) -> UniverseStatus:
        raise NotImplementedError

    def cleanup(self, handle: UniverseHandle) -> list[str]:
        return []
