from __future__ import annotations

import importlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from framework.universes.deployment_backends.base import UniverseBackend
from framework.universes.deployment_backends.models import (
    UniverseEndpoint,
    UniverseHandle,
    UniverseRuntimeSpec,
    UniverseStatus,
)
from framework.universes.status_files import endpoint_from_status, read_status_file


def _safe_name(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(value or "universe"))
    return cleaned.strip("_") or "universe"


class _RayUniverseActor:
    """Ray actor target that starts one Universe runtime in a worker process."""

    def __init__(
        self,
        params_data: Dict[str, Any],
        host: str,
        port: int,
        cors: list[str] | None,
        status_file: str,
        stdout_file: str | None = None,
        stderr_file: str | None = None,
    ) -> None:
        import threading
        from framework.universes.data_models import BaseUniverseParams
        from framework.universes.base_universe import run_app
        from framework.universes.status_files import write_status_atomic

        self.status_file = status_file
        self.stdout_file = stdout_file
        self.stderr_file = stderr_file
        self._error: str | None = None
        self._started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        try:
            write_status_atomic(status_file, {"schema_version": 1, "status": "starting", "host": host, "port": port})
        except Exception:
            pass

        params = BaseUniverseParams.model_validate(params_data) if hasattr(BaseUniverseParams, "model_validate") else BaseUniverseParams(**params_data)

        def target() -> None:
            try:
                run_app(params=params, host=host, port=port, cors=cors, status_file=status_file)
            except Exception as exc:  # pragma: no cover - exercised only in real Ray workers
                self._error = f"{type(exc).__name__}: {exc}"
                try:
                    write_status_atomic(status_file, {"schema_version": 1, "status": "failed", "host": host, "port": port, "error": self._error})
                except Exception:
                    pass

        self._thread = threading.Thread(target=target, name="wolf-ray-universe", daemon=True)
        self._thread.start()

    def status(self) -> Dict[str, Any]:
        data = read_status_file(self.status_file) or {}
        if self._error:
            data.setdefault("status", "failed")
            data.setdefault("error", self._error)
        data.setdefault("started_at", self._started_at)
        data.setdefault("alive", bool(getattr(self, "_thread", None) and self._thread.is_alive()))
        return data

    def logs(self, tail: int = 200) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for label, path in {"stdout": self.stdout_file, "stderr": self.stderr_file}.items():
            if not path:
                out[label] = ""
                continue
            try:
                lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
                out[label] = "\n".join(lines[-max(1, int(tail)):])
            except Exception as exc:
                out[label] = f"<unable to read {path}: {exc}>"
        return out


class RayUniverseBackend(UniverseBackend):
    """Run a Universe as a Ray actor using the optional ``ray`` package.

    Ray is imported lazily, so this backend can be registered even when Ray is
    not installed. Launch returns a failed handle with diagnostics if Ray cannot
    be imported or initialized.
    """

    backend_name = "ray"

    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        config = dict(config or {})
        runtime_dir = Path(config.get("runtime_dir") or spec.runtime_dir or (Path(tempfile.gettempdir()) / "wolf_universes"))
        runtime_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_name = _safe_name(spec.name)
        params_file = runtime_dir / f"{safe_name}_{timestamp}.params.json"
        status_file = runtime_dir / f"{safe_name}_{timestamp}.status.json"
        stdout_file = runtime_dir / f"{safe_name}_{timestamp}.stdout.log"
        stderr_file = runtime_dir / f"{safe_name}_{timestamp}.stderr.log"

        serializable = spec.params.model_dump(mode="json") if hasattr(spec.params, "model_dump") else spec.params.dict()
        params_file.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

        files = {
            "runtime_root": str(runtime_dir),
            "params_file": str(params_file),
            "status_file": str(status_file),
            "stdout_file": str(stdout_file),
            "stderr_file": str(stderr_file),
        }
        logs = {"stdout_file": str(stdout_file), "stderr_file": str(stderr_file)}
        metadata: Dict[str, Any] = {
            "backend": self.backend_name,
            "deployment_type": "ray",
            "system": spec.system,
            "runtime_dir": str(runtime_dir),
        }

        try:
            ray = importlib.import_module("ray")
        except Exception as exc:
            return UniverseHandle(name=spec.name, backend=self.backend_name, state="failed", runtime_spec=spec, files=files, logs=logs, metadata={**metadata, "error": f"Ray is not available: {type(exc).__name__}: {exc}"})

        address = config.get("address") or os.environ.get("RAY_ADDRESS")
        namespace = config.get("namespace") or os.environ.get("WOLF_RAY_NAMESPACE") or "wolf"
        runtime_env = config.get("runtime_env")
        init_kwargs: Dict[str, Any] = {"ignore_reinit_error": True, "namespace": namespace}
        if address:
            init_kwargs["address"] = address
        if runtime_env:
            init_kwargs["runtime_env"] = runtime_env
        try:
            if not ray.is_initialized():
                ray.init(**init_kwargs)
        except Exception as exc:
            return UniverseHandle(name=spec.name, backend=self.backend_name, state="failed", runtime_spec=spec, files=files, logs=logs, metadata={**metadata, "error": f"ray.init failed: {type(exc).__name__}: {exc}", "ray_init_kwargs": init_kwargs})

        host = str(config.get("host") or getattr(spec.params.info, "host", None) or "127.0.0.1")
        port = int(config.get("port") if config.get("port") is not None else (getattr(spec.params.info, "port", 0) or 0))
        cors = spec.cors if spec.cors is not None else ["*"]
        actor_name = str(config.get("actor_name") or f"wolf_{safe_name}_{timestamp}")
        actor_options = dict(config.get("actor_options") or {})
        if config.get("num_cpus") is not None:
            actor_options.setdefault("num_cpus", config.get("num_cpus"))
        if config.get("num_gpus") is not None:
            actor_options.setdefault("num_gpus", config.get("num_gpus"))
        actor_options.setdefault("name", actor_name)
        actor_options.setdefault("lifetime", str(config.get("lifetime") or "detached"))

        try:
            remote_actor_cls = ray.remote(**actor_options)(_RayUniverseActor)
            actor = remote_actor_cls.remote(serializable, host, port, cors, str(status_file), str(stdout_file), str(stderr_file))
        except Exception as exc:
            return UniverseHandle(name=spec.name, backend=self.backend_name, state="failed", runtime_spec=spec, files=files, logs=logs, metadata={**metadata, "error": f"Ray actor launch failed: {type(exc).__name__}: {exc}", "actor_options": actor_options})

        handle = UniverseHandle(
            name=spec.name,
            backend=self.backend_name,
            state="starting",
            runtime_spec=spec,
            process={"actor": actor, "actor_name": actor_name, "namespace": namespace},
            files=files,
            logs=logs,
            metadata={**metadata, "actor_name": actor_name, "namespace": namespace, "address": address, "actor_options": actor_options},
        )

        max_wait = float(spec.startup_timeout_s or config.get("startup_timeout_s") or 60.0)
        poll_interval = float(config.get("poll_interval_s") or 0.5)
        elapsed = 0.0
        endpoint_data = None
        last_status: Dict[str, Any] = {}
        while elapsed < max_wait:
            status_data = read_status_file(status_file) or self._actor_status(ray, actor, timeout=poll_interval)
            last_status = status_data or last_status
            endpoint_data = endpoint_from_status(status_data)
            if endpoint_data:
                break
            if isinstance(status_data, dict) and status_data.get("status") == "failed":
                break
            time.sleep(poll_interval)
            elapsed += poll_interval

        endpoint_data = endpoint_data or endpoint_from_status(read_status_file(status_file) or last_status)
        if endpoint_data:
            handle.endpoint = UniverseEndpoint(**endpoint_data)
            handle.state = "ready"
        elif last_status.get("status") == "failed":
            handle.state = "failed"
            handle.metadata["error"] = last_status.get("error")
        else:
            handle.state = "port_unknown"
        handle.metadata["startup_elapsed_s"] = elapsed
        handle.metadata["last_status"] = last_status
        if handle.endpoint:
            handle.metadata.update({"host": handle.endpoint.host, "port": handle.endpoint.port, "url": handle.endpoint.url})
        return handle

    def _actor_status(self, ray: Any, actor: Any, timeout: float = 1.0) -> Dict[str, Any]:
        try:
            ref = actor.status.remote()
            data = ray.get(ref, timeout=timeout)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _ray(self):
        return importlib.import_module("ray")

    def status(self, handle: UniverseHandle) -> UniverseStatus:
        status_data = read_status_file((handle.files or {}).get("status_file")) or {}
        actor = (handle.process or {}).get("actor") if isinstance(handle.process, dict) else None
        if actor is not None:
            try:
                ray = self._ray()
                actor_status = self._actor_status(ray, actor, timeout=1.0)
                if actor_status:
                    status_data = actor_status
            except Exception:
                pass
        endpoint_data = endpoint_from_status(status_data)
        endpoint = UniverseEndpoint(**endpoint_data) if endpoint_data else handle.endpoint
        state = str(status_data.get("status") or ("ready" if endpoint and endpoint.usable else handle.state or "unknown"))
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=state, endpoint=endpoint, metadata={**dict(handle.metadata or {}), "last_status": status_data})

    def logs(self, handle: UniverseHandle, tail: int = 200) -> Dict[str, str]:
        actor = (handle.process or {}).get("actor") if isinstance(handle.process, dict) else None
        if actor is not None:
            try:
                ray = self._ray()
                data = ray.get(actor.logs.remote(tail), timeout=2.0)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
            except Exception:
                pass
        return super().logs(handle, tail=tail)

    def terminate(self, handle: UniverseHandle, force: bool = False, timeout: float = 10.0) -> UniverseStatus:
        actor = (handle.process or {}).get("actor") if isinstance(handle.process, dict) else None
        error = None
        if actor is not None:
            try:
                ray = self._ray()
                ray.kill(actor, no_restart=True)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        handle.state = "terminated" if error is None else "terminate_failed"
        handle.metadata["status"] = handle.state
        if error:
            handle.metadata["terminate_error"] = error
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=handle.state, endpoint=handle.endpoint, metadata=dict(handle.metadata or {}), error=error)

    def cleanup(self, handle: UniverseHandle) -> list[str]:
        removed: list[str] = []
        for key, path in (handle.files or {}).items():
            try:
                Path(path).unlink(missing_ok=True)
                removed.append(key)
            except Exception:
                pass
        return removed
