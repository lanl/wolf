from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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


def _bool_config(config: Dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


class _SandboxProcessUniverseBackend(UniverseBackend):
    """Common local sandbox-process backend for bubblewrap/nsjail prototypes."""

    backend_name = "sandbox"
    runtime_name = "sandbox"
    bin_config_key = "sandbox_bin"
    env_bin_key = "WOLF_SANDBOX_BIN"
    default_bin = "sandbox"

    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        config = dict(config or {})
        sandbox_bin = str(config.get(self.bin_config_key) or os.environ.get(self.env_bin_key) or self.default_bin)
        python_bin = str(config.get("python_bin") or sys.executable)
        require_binary = _bool_config(config, "require_binary", True)
        if require_binary and shutil.which(sandbox_bin) is None:
            return UniverseHandle(
                name=spec.name,
                backend=self.backend_name,
                state="failed",
                runtime_spec=spec,
                metadata={
                    "backend": self.backend_name,
                    "deployment_type": self.runtime_name,
                    "error": f"Sandbox executable not found: {sandbox_bin}",
                    "sandbox_bin": sandbox_bin,
                },
            )

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

        host = str(config.get("host") or getattr(spec.params.info, "host", None) or "127.0.0.1")
        port = int(config.get("port") if config.get("port") is not None else (getattr(spec.params.info, "port", 0) or 0))
        cors = spec.cors if spec.cors is not None else ["*"]
        run_cmd = [
            python_bin,
            "-m",
            "framework.universes.run_universe",
            "--params-file",
            str(params_file),
            "--status-file",
            str(status_file),
            "--host",
            host,
            "--port",
            str(port),
        ]
        if cors:
            run_cmd.extend(["--cors", *[str(c) for c in cors]])

        sandbox_cmd = self._sandbox_command(sandbox_bin, run_cmd, config=config, runtime_dir=runtime_dir)
        files = {
            "runtime_root": str(runtime_dir),
            "params_file": str(params_file),
            "status_file": str(status_file),
            "stdout_file": str(stdout_file),
            "stderr_file": str(stderr_file),
        }
        logs = {"stdout_file": str(stdout_file), "stderr_file": str(stderr_file)}
        stdout_handle = stdout_file.open("a", encoding="utf-8")
        stderr_handle = stderr_file.open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen(sandbox_cmd, stdout=stdout_handle, stderr=stderr_handle, text=True, env={**os.environ, **(spec.env or {})})
        except Exception as exc:
            stdout_handle.close()
            stderr_handle.close()
            return UniverseHandle(
                name=spec.name,
                backend=self.backend_name,
                state="failed",
                runtime_spec=spec,
                files=files,
                logs=logs,
                metadata={
                    "backend": self.backend_name,
                    "deployment_type": self.runtime_name,
                    "sandbox_bin": sandbox_bin,
                    "command": sandbox_cmd,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

        handle = UniverseHandle(
            name=spec.name,
            backend=self.backend_name,
            state="starting",
            runtime_spec=spec,
            process=proc,
            files=files,
            logs=logs,
            metadata={
                "backend": self.backend_name,
                "deployment_type": self.runtime_name,
                "sandbox_bin": sandbox_bin,
                "subprocess_pid": getattr(proc, "pid", None),
                "command": sandbox_cmd,
                "run_command": run_cmd,
                "sandbox_profile": dict(config),
            },
        )

        max_wait = float(spec.startup_timeout_s or config.get("startup_timeout_s") or 60.0)
        poll_interval = float(config.get("poll_interval_s") or 0.5)
        elapsed = 0.0
        endpoint_data = None
        while elapsed < max_wait:
            if proc.poll() is not None:
                break
            status_data = read_status_file(status_file)
            endpoint_data = endpoint_from_status(status_data)
            if endpoint_data:
                break
            time.sleep(poll_interval)
            elapsed += poll_interval

        if proc.poll() is not None:
            handle.state = "failed"
            handle.metadata["returncode"] = proc.returncode
        elif endpoint_data:
            handle.endpoint = UniverseEndpoint(**endpoint_data)
            handle.state = "ready"
        else:
            handle.state = "port_unknown"
        handle.metadata["startup_elapsed_s"] = elapsed
        if handle.endpoint:
            handle.metadata.update({"host": handle.endpoint.host, "port": handle.endpoint.port, "url": handle.endpoint.url})
        return handle

    def _sandbox_command(self, sandbox_bin: str, run_cmd: list[str], *, config: Dict[str, Any], runtime_dir: Path) -> list[str]:
        raise NotImplementedError

    def status(self, handle: UniverseHandle) -> UniverseStatus:
        proc = handle.process
        rc = proc.poll() if proc is not None and hasattr(proc, "poll") else None
        status_data = read_status_file((handle.files or {}).get("status_file")) or {}
        endpoint_data = endpoint_from_status(status_data)
        endpoint = UniverseEndpoint(**endpoint_data) if endpoint_data else handle.endpoint
        if rc is None and endpoint and endpoint.usable:
            state = "ready"
        elif rc is None:
            state = handle.state if handle.state in {"starting", "port_unknown"} else "running"
        else:
            state = f"exited({rc})"
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=state, endpoint=endpoint, pid=getattr(proc, "pid", None), metadata={**dict(handle.metadata or {}), "last_status": status_data})

    def terminate(self, handle: UniverseHandle, force: bool = False, timeout: float = 10.0) -> UniverseStatus:
        proc = handle.process
        if proc is not None and hasattr(proc, "poll"):
            if proc.poll() is None:
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            rc = proc.poll()
            handle.state = f"terminated({rc})" if rc is not None else "terminated"
        else:
            handle.state = "terminated"
        handle.metadata["status"] = handle.state
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=handle.state, endpoint=handle.endpoint, pid=getattr(proc, "pid", None), metadata=dict(handle.metadata or {}))

    def cleanup(self, handle: UniverseHandle) -> list[str]:
        removed: list[str] = []
        for key, path in (handle.files or {}).items():
            try:
                Path(path).unlink(missing_ok=True)
                removed.append(key)
            except Exception:
                pass
        return removed


class BubblewrapUniverseBackend(_SandboxProcessUniverseBackend):
    """Prototype bubblewrap sandbox backend for local Universe runtimes."""

    backend_name = "sandbox_bubblewrap"
    runtime_name = "bubblewrap"
    bin_config_key = "bwrap_bin"
    env_bin_key = "WOLF_BWRAP_BIN"
    default_bin = "bwrap"

    def _sandbox_command(self, sandbox_bin: str, run_cmd: list[str], *, config: Dict[str, Any], runtime_dir: Path) -> list[str]:
        cwd = Path.cwd()
        cmd = [sandbox_bin, "--die-with-parent", "--proc", "/proc", "--dev", "/dev"]
        if _bool_config(config, "tmpfs_tmp", True):
            cmd.extend(["--tmpfs", "/tmp"])
        cmd.extend(["--bind", str(runtime_dir), str(runtime_dir)])
        cmd.extend(["--ro-bind", str(cwd), str(cwd), "--chdir", str(cwd)])
        for mount in config.get("ro_binds") or []:
            src = str(mount.get("source") or mount.get("src")) if isinstance(mount, dict) else str(mount)
            dst = str(mount.get("target") or mount.get("dst") or src) if isinstance(mount, dict) else src
            cmd.extend(["--ro-bind", src, dst])
        for mount in config.get("rw_binds") or []:
            src = str(mount.get("source") or mount.get("src")) if isinstance(mount, dict) else str(mount)
            dst = str(mount.get("target") or mount.get("dst") or src) if isinstance(mount, dict) else src
            cmd.extend(["--bind", src, dst])
        if _bool_config(config, "unshare_pid", False):
            cmd.append("--unshare-pid")
        if _bool_config(config, "unshare_ipc", False):
            cmd.append("--unshare-ipc")
        if _bool_config(config, "unshare_net", False):
            cmd.append("--unshare-net")
        cmd.extend(["--", *run_cmd])
        return cmd


class NsjailUniverseBackend(_SandboxProcessUniverseBackend):
    """Prototype nsjail sandbox backend for local Universe runtimes."""

    backend_name = "sandbox_nsjail"
    runtime_name = "nsjail"
    bin_config_key = "nsjail_bin"
    env_bin_key = "WOLF_NSJAIL_BIN"
    default_bin = "nsjail"

    def _sandbox_command(self, sandbox_bin: str, run_cmd: list[str], *, config: Dict[str, Any], runtime_dir: Path) -> list[str]:
        cwd = Path.cwd()
        cmd = [sandbox_bin, "--quiet", "--mode", str(config.get("mode") or "o"), "--cwd", str(cwd)]
        cmd.extend(["--bindmount", str(runtime_dir)])
        cmd.extend(["--bindmount_ro", str(cwd)])
        if config.get("time_limit") is not None:
            cmd.extend(["--time_limit", str(config["time_limit"])])
        if config.get("max_cpus") is not None:
            cmd.extend(["--max_cpus", str(config["max_cpus"])])
        if config.get("rlimit_as") is not None:
            cmd.extend(["--rlimit_as", str(config["rlimit_as"])])
        for mount in config.get("bindmount_ro") or []:
            cmd.extend(["--bindmount_ro", str(mount)])
        for mount in config.get("bindmount") or []:
            cmd.extend(["--bindmount", str(mount)])
        cmd.extend(["--", *run_cmd])
        return cmd
