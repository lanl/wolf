from __future__ import annotations

import json
import os
import re
import subprocess
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


_PORT_RE = re.compile(r"(?P<host>[^\s:]+|\[[^\]]+\]):(?P<port>\d+)\s*$")


def _safe_name(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(value or "universe"))
    return cleaned.strip("_") or "universe"


def _bool_config(config: Dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


class PodmanUniverseBackend(UniverseBackend):
    """Run a Universe inside a Podman container using the Podman CLI.

    Image/runtime contract:
    - The image must be able to execute ``python -m framework.universes.run_universe``.
    - A unique per-launch host work directory is mounted into the container.
    - The container reads ``params.json`` and writes ``status.json`` in that mounted directory.
    - The Universe listens on ``0.0.0.0:<container_port>`` inside the container.
    - Podman publishes that container port to a host endpoint, discovered with ``podman port``.
    """

    backend_name = "podman"

    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        config = dict(config or {})
        podman_bin = str(config.get("podman_bin") or os.environ.get("WOLF_PODMAN_BIN") or "podman")
        image = str(config.get("image") or os.environ.get("WOLF_UNIVERSE_PODMAN_IMAGE") or "wolf-universe:latest")
        python_bin = str(config.get("python_bin") or "python")
        container_port = int(config.get("container_port") or config.get("port") or 8000)
        publish_host = str(config.get("publish_host") or config.get("host") or "127.0.0.1")
        host_port = int(config.get("host_port", config.get("publish_port", 0)) or 0)
        container_host = str(config.get("container_host") or "0.0.0.0")
        remove_on_exit = _bool_config(config, "rm", True)
        detach = _bool_config(config, "detach", True)

        runtime_root = Path(config.get("runtime_dir") or spec.runtime_dir or (Path(tempfile.gettempdir()) / "wolf_universes"))
        runtime_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_name = _safe_name(spec.name)
        container_name = str(config.get("container_name") or f"wolf-{safe_name}-{timestamp}".lower())
        work_dir = Path(config.get("work_dir") or (runtime_root / f"{safe_name}_{timestamp}_{os.getpid()}"))
        work_dir.mkdir(parents=True, exist_ok=True)

        params_file = work_dir / "params.json"
        status_file = work_dir / "status.json"

        serializable = spec.params.model_dump(mode="json") if hasattr(spec.params, "model_dump") else spec.params.dict()
        params_file.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

        container_runtime_dir = str(config.get("container_runtime_dir") or "/wolf/runtime")
        container_params_file = f"{container_runtime_dir}/params.json"
        container_status_file = f"{container_runtime_dir}/status.json"
        publish_spec = f"{publish_host}:{host_port}:{container_port}/tcp" if host_port > 0 else f"{publish_host}::{container_port}/tcp"

        cmd: list[str] = [podman_bin, "run"]
        if detach:
            cmd.append("-d")
        if remove_on_exit:
            cmd.append("--rm")
        cmd.extend(["--name", container_name, "-p", publish_spec])

        if _bool_config(config, "rootless", True):
            userns = str(config.get("userns") or "keep-id")
            if userns:
                cmd.extend(["--userns", userns])
        if config.get("cpus") is not None:
            cmd.extend(["--cpus", str(config["cpus"])])
        if config.get("memory") is not None:
            cmd.extend(["--memory", str(config["memory"])])
        if config.get("pids_limit") is not None:
            cmd.extend(["--pids-limit", str(config["pids_limit"])])

        env = {**(spec.env or {}), **{str(k): str(v) for k, v in (config.get("env") or {}).items()}}
        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])

        cmd.extend(["-v", f"{work_dir}:{container_runtime_dir}:rw"])
        for mount in config.get("mounts") or []:
            if not isinstance(mount, dict):
                continue
            source = mount.get("source") or mount.get("src")
            target = mount.get("target") or mount.get("dst") or mount.get("destination")
            if not source or not target:
                continue
            options = mount.get("options")
            if options is None:
                options = "ro" if _bool_config(mount, "readonly", False) else "rw"
            cmd.extend(["-v", f"{source}:{target}:{options}"])

        cmd.append(image)
        runtime_cmd = [
            python_bin,
            "-m",
            "framework.universes.run_universe",
            "--params-file",
            container_params_file,
            "--status-file",
            container_status_file,
            "--host",
            container_host,
            "--port",
            str(container_port),
        ]
        cors = spec.cors if spec.cors is not None else ["*"]
        if cors:
            runtime_cmd.extend(["--cors", *[str(c) for c in cors]])
        cmd.extend(runtime_cmd)

        files = {
            "runtime_root": str(runtime_root),
            "work_dir": str(work_dir),
            "params_file": str(params_file),
            "status_file": str(status_file),
        }
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return UniverseHandle(
                name=spec.name,
                backend=self.backend_name,
                state="failed",
                runtime_spec=spec,
                files=files,
                metadata={
                    "backend": self.backend_name,
                    "deployment_type": "podman",
                    "image": image,
                    "container_name": container_name,
                    "podman_returncode": result.returncode,
                    "stderr": result.stderr,
                    "command": cmd,
                },
            )

        container_id = (result.stdout or "").strip().splitlines()[-1].strip() if (result.stdout or "").strip() else container_name
        process_handle = {"container_id": container_id, "container_name": container_name, "podman_bin": podman_bin}
        handle = UniverseHandle(
            name=spec.name,
            backend=self.backend_name,
            state="starting",
            runtime_spec=spec,
            process=process_handle,
            files=files,
            metadata={
                "backend": self.backend_name,
                "deployment_type": "podman",
                "image": image,
                "container_name": container_name,
                "container_id": container_id,
                "container_port": container_port,
                "publish_host": publish_host,
                "publish_spec": publish_spec,
                "rootless": _bool_config(config, "rootless", True),
                "command": cmd,
            },
        )

        max_wait = float(spec.startup_timeout_s or config.get("startup_timeout_s") or 60.0)
        poll_interval = float(config.get("poll_interval_s") or 0.5)
        elapsed = 0.0
        endpoint: UniverseEndpoint | None = None
        while elapsed < max_wait:
            endpoint = self._discover_published_endpoint(handle, container_port=container_port, publish_host=publish_host, podman_bin=podman_bin)
            if endpoint and endpoint.usable:
                break
            status_data = read_status_file(status_file)
            status_endpoint = endpoint_from_status(status_data)
            if status_endpoint:
                port = host_port if host_port > 0 else int(status_endpoint["port"])
                host = publish_host if publish_host not in {"0.0.0.0", "::"} else "127.0.0.1"
                endpoint = UniverseEndpoint(scheme=status_endpoint.get("scheme", "http"), host=host, port=port)
                if endpoint.usable:
                    break
            if not self._container_running(handle):
                break
            time.sleep(poll_interval)
            elapsed += poll_interval

        if endpoint and endpoint.usable:
            handle.endpoint = endpoint
            handle.state = "ready"
        elif self._container_running(handle):
            handle.state = "port_unknown"
        else:
            handle.state = "failed"
        handle.metadata["startup_elapsed_s"] = elapsed
        if handle.endpoint:
            handle.metadata.update({"host": handle.endpoint.host, "port": handle.endpoint.port, "url": handle.endpoint.url})
        return handle

    def _run_podman(self, handle: UniverseHandle, args: list[str]) -> subprocess.CompletedProcess:
        podman_bin = str((handle.process or {}).get("podman_bin") or handle.metadata.get("podman_bin") or "podman") if isinstance(handle.process, dict) else "podman"
        return subprocess.run([podman_bin, *args], capture_output=True, text=True, check=False)

    def _container_ref(self, handle: UniverseHandle) -> str:
        process = handle.process if isinstance(handle.process, dict) else {}
        return str(process.get("container_id") or process.get("container_name") or handle.metadata.get("container_id") or handle.metadata.get("container_name") or handle.name)

    def _container_running(self, handle: UniverseHandle) -> bool:
        result = self._run_podman(handle, ["inspect", "-f", "{{.State.Running}}", self._container_ref(handle)])
        if result.returncode != 0:
            return False
        return (result.stdout or "").strip().lower() == "true"

    def _discover_published_endpoint(self, handle: UniverseHandle, *, container_port: int, publish_host: str, podman_bin: str) -> UniverseEndpoint | None:
        ref = self._container_ref(handle)
        result = subprocess.run([podman_bin, "port", ref, f"{int(container_port)}/tcp"], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return None
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        for line in lines:
            match = _PORT_RE.search(line)
            if not match:
                continue
            host = match.group("host").strip("[]")
            if host in {"0.0.0.0", "::"}:
                host = publish_host if publish_host not in {"0.0.0.0", "::"} else "127.0.0.1"
            return UniverseEndpoint(host=host, port=int(match.group("port")))
        return None

    def status(self, handle: UniverseHandle) -> UniverseStatus:
        running = self._container_running(handle)
        state = handle.state if running and handle.state in {"ready", "port_unknown", "starting"} else ("running" if running else "exited")
        container_port = int(handle.metadata.get("container_port") or 8000)
        publish_host = str(handle.metadata.get("publish_host") or "127.0.0.1")
        podman_bin = str((handle.process or {}).get("podman_bin") or "podman") if isinstance(handle.process, dict) else "podman"
        endpoint = self._discover_published_endpoint(handle, container_port=container_port, publish_host=publish_host, podman_bin=podman_bin) or handle.endpoint
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=state, endpoint=endpoint, metadata=dict(handle.metadata or {}))

    def logs(self, handle: UniverseHandle, tail: int = 200) -> Dict[str, str]:
        result = self._run_podman(handle, ["logs", "--tail", str(max(1, int(tail))), self._container_ref(handle)])
        if result.returncode != 0:
            return {"stdout": result.stdout or "", "stderr": result.stderr or f"podman logs failed with returncode {result.returncode}"}
        return {"stdout": result.stdout or "", "stderr": result.stderr or ""}

    def terminate(self, handle: UniverseHandle, force: bool = False, timeout: float = 10.0) -> UniverseStatus:
        ref = self._container_ref(handle)
        if force:
            result = self._run_podman(handle, ["rm", "-f", ref])
        else:
            result = self._run_podman(handle, ["stop", "--time", str(max(0, int(timeout))), ref])
        handle.state = "terminated" if result.returncode == 0 else "terminate_failed"
        handle.metadata["terminate_returncode"] = result.returncode
        if result.stderr:
            handle.metadata["terminate_stderr"] = result.stderr
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=handle.state, endpoint=handle.endpoint, metadata=dict(handle.metadata or {}), error=result.stderr if result.returncode else None)

    def cleanup(self, handle: UniverseHandle) -> list[str]:
        removed: list[str] = []
        work_dir = (handle.files or {}).get("work_dir")
        for key, path in (handle.files or {}).items():
            if key.endswith("_file"):
                try:
                    Path(path).unlink(missing_ok=True)
                    removed.append(key)
                except Exception:
                    pass
        if work_dir:
            try:
                path = Path(work_dir)
                path.rmdir()
                removed.append("work_dir")
            except Exception:
                pass
        return removed
