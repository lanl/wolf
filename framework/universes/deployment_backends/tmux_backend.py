from __future__ import annotations

import json
import os
import shlex
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


def _safe_session_name(value: str, *, prefix: str = "wolf") -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(value or "universe"))
    cleaned = cleaned.strip("_-") or "universe"
    return f"{prefix}_{cleaned}"[:80]


def _safe_file_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(value or "universe")).strip("_") or "universe"


class _TerminalMultiplexerUniverseBackend(UniverseBackend):
    """Base implementation for detached terminal-multiplexer Universe sessions."""

    backend_name = "terminal_multiplexer"
    multiplexer = "screen"
    bin_config_key = "screen_bin"
    env_bin_key = "WOLF_SCREEN_BIN"

    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        config = dict(config or {})
        multiplexer_bin = str(config.get(self.bin_config_key) or os.environ.get(self.env_bin_key) or self.multiplexer)
        python_bin = str(config.get("python_bin") or sys.executable)
        runtime_dir = Path(config.get("runtime_dir") or spec.runtime_dir or (Path(tempfile.gettempdir()) / "wolf_universes"))
        runtime_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_name = _safe_file_name(spec.name)
        session_name = str(config.get("session_name") or _safe_session_name(f"{safe_name}_{timestamp}", prefix=f"wolf_{self.multiplexer}"))

        params_file = runtime_dir / f"{safe_name}_{timestamp}.params.json"
        status_file = runtime_dir / f"{safe_name}_{timestamp}.status.json"
        stdout_file = runtime_dir / f"{safe_name}_{timestamp}.stdout.log"
        stderr_file = runtime_dir / f"{safe_name}_{timestamp}.stderr.log"

        serializable = spec.params.model_dump(mode="json") if hasattr(spec.params, "model_dump") else spec.params.dict()
        params_file.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

        universe_cmd = [
            python_bin,
            "-m",
            "framework.universes.run_universe",
            "--params-file",
            str(params_file),
            "--status-file",
            str(status_file),
        ]
        host = config.get("host")
        port = config.get("port")
        if host:
            universe_cmd.extend(["--host", str(host)])
        if port is not None:
            universe_cmd.extend(["--port", str(port)])
        cors = spec.cors if spec.cors is not None else ["*"]
        if cors:
            universe_cmd.extend(["--cors", *[str(c) for c in cors]])

        shell_cmd = " ".join(shlex.quote(part) for part in universe_cmd)
        shell_cmd = f"cd {shlex.quote(str(Path.cwd()))} && {shell_cmd} >> {shlex.quote(str(stdout_file))} 2>> {shlex.quote(str(stderr_file))}"
        launch_cmd = self._launch_command(multiplexer_bin, session_name, shell_cmd)

        files = {
            "params_file": str(params_file),
            "status_file": str(status_file),
            "stdout_file": str(stdout_file),
            "stderr_file": str(stderr_file),
        }
        logs = {"stdout_file": str(stdout_file), "stderr_file": str(stderr_file)}
        metadata = {
            "system": spec.system,
            "deployment_type": self.backend_name,
            "backend": self.backend_name,
            "multiplexer": self.multiplexer,
            "session_name": session_name,
            "command": launch_cmd,
            "universe_command": universe_cmd,
            "shell_command": shell_cmd,
        }

        result = subprocess.run(launch_cmd, capture_output=True, text=True, check=False, env={**os.environ, **(spec.env or {})})
        if result.returncode != 0:
            return UniverseHandle(
                name=spec.name,
                backend=self.backend_name,
                state="failed",
                runtime_spec=spec,
                files=files,
                logs=logs,
                metadata={**metadata, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
            )

        process_handle = {"multiplexer": self.multiplexer, "bin": multiplexer_bin, "session_name": session_name}
        handle = UniverseHandle(
            name=spec.name,
            backend=self.backend_name,
            state="starting",
            runtime_spec=spec,
            process=process_handle,
            files=files,
            logs=logs,
            metadata=metadata,
        )

        max_wait = float(spec.startup_timeout_s or config.get("startup_timeout_s") or 60.0)
        poll_interval = float(config.get("poll_interval_s") or 0.5)
        elapsed = 0.0
        endpoint_data = None
        while elapsed < max_wait:
            status_data = read_status_file(status_file)
            endpoint_data = endpoint_from_status(status_data)
            if endpoint_data:
                break
            if not self._session_exists(handle):
                break
            time.sleep(poll_interval)
            elapsed += poll_interval

        endpoint_data = endpoint_data or endpoint_from_status(read_status_file(status_file))
        if endpoint_data:
            handle.endpoint = UniverseEndpoint(**endpoint_data)
            handle.state = "ready"
        elif self._session_exists(handle):
            handle.state = "port_unknown"
        else:
            handle.state = "exited"
        handle.metadata["startup_elapsed_s"] = elapsed
        if handle.endpoint:
            handle.metadata.update({"host": handle.endpoint.host, "port": handle.endpoint.port, "url": handle.endpoint.url})
        return handle

    def _launch_command(self, multiplexer_bin: str, session_name: str, shell_cmd: str) -> list[str]:
        raise NotImplementedError

    def _session_exists(self, handle: UniverseHandle) -> bool:
        raise NotImplementedError

    def status(self, handle: UniverseHandle) -> UniverseStatus:
        exists = self._session_exists(handle)
        status_data = read_status_file((handle.files or {}).get("status_file"))
        endpoint_data = endpoint_from_status(status_data)
        endpoint = UniverseEndpoint(**endpoint_data) if endpoint_data else handle.endpoint
        if exists and endpoint and endpoint.usable:
            state = "ready"
        elif exists:
            state = handle.state if handle.state in {"starting", "port_unknown"} else "running"
        else:
            state = "exited"
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=state, endpoint=endpoint, metadata=dict(handle.metadata or {}))

    def terminate(self, handle: UniverseHandle, force: bool = False, timeout: float = 10.0) -> UniverseStatus:
        cmd = self._terminate_command(handle)
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        ok = result.returncode == 0 or not self._session_exists(handle)
        handle.state = "terminated" if ok else "terminate_failed"
        handle.metadata["terminate_returncode"] = result.returncode
        if result.stderr:
            handle.metadata["terminate_stderr"] = result.stderr
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=handle.state, endpoint=handle.endpoint, metadata=dict(handle.metadata or {}), error=result.stderr if not ok else None)

    def _terminate_command(self, handle: UniverseHandle) -> list[str]:
        raise NotImplementedError

    def cleanup(self, handle: UniverseHandle) -> list[str]:
        removed: list[str] = []
        for key, path in (handle.files or {}).items():
            try:
                Path(path).unlink(missing_ok=True)
                removed.append(key)
            except Exception:
                pass
        return removed


class ScreenUniverseBackend(_TerminalMultiplexerUniverseBackend):
    """Run a Universe in a detached GNU screen session."""

    backend_name = "screen"
    multiplexer = "screen"
    bin_config_key = "screen_bin"
    env_bin_key = "WOLF_SCREEN_BIN"

    def _launch_command(self, multiplexer_bin: str, session_name: str, shell_cmd: str) -> list[str]:
        return [multiplexer_bin, "-dmS", session_name, "bash", "-lc", shell_cmd]

    def _session_exists(self, handle: UniverseHandle) -> bool:
        process = handle.process if isinstance(handle.process, dict) else {}
        bin_ = str(process.get("bin") or "screen")
        session_name = str(process.get("session_name") or handle.metadata.get("session_name") or handle.name)
        result = subprocess.run([bin_, "-ls"], capture_output=True, text=True, check=False)
        output = f"{result.stdout}\n{result.stderr}"
        return session_name in output

    def _terminate_command(self, handle: UniverseHandle) -> list[str]:
        process = handle.process if isinstance(handle.process, dict) else {}
        bin_ = str(process.get("bin") or "screen")
        session_name = str(process.get("session_name") or handle.metadata.get("session_name") or handle.name)
        return [bin_, "-S", session_name, "-X", "quit"]


class TmuxUniverseBackend(_TerminalMultiplexerUniverseBackend):
    """Run a Universe in a detached tmux session."""

    backend_name = "tmux"
    multiplexer = "tmux"
    bin_config_key = "tmux_bin"
    env_bin_key = "WOLF_TMUX_BIN"

    def _launch_command(self, multiplexer_bin: str, session_name: str, shell_cmd: str) -> list[str]:
        return [multiplexer_bin, "new-session", "-d", "-s", session_name, "bash", "-lc", shell_cmd]

    def _session_exists(self, handle: UniverseHandle) -> bool:
        process = handle.process if isinstance(handle.process, dict) else {}
        bin_ = str(process.get("bin") or "tmux")
        session_name = str(process.get("session_name") or handle.metadata.get("session_name") or handle.name)
        result = subprocess.run([bin_, "has-session", "-t", session_name], capture_output=True, text=True, check=False)
        return result.returncode == 0

    def _terminate_command(self, handle: UniverseHandle) -> list[str]:
        process = handle.process if isinstance(handle.process, dict) else {}
        bin_ = str(process.get("bin") or "tmux")
        session_name = str(process.get("session_name") or handle.metadata.get("session_name") or handle.name)
        return [bin_, "kill-session", "-t", session_name]
