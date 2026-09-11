from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
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


class LocalProcessUniverseBackend(UniverseBackend):
    backend_name = "local_process"

    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        runtime_dir = Path(spec.runtime_dir or (Path(tempfile.gettempdir()) / "wolf_universes"))
        runtime_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in spec.name)

        params_file = runtime_dir / f"{safe_name}_{timestamp}.params.json"
        status_file = runtime_dir / f"{safe_name}_{timestamp}.status.json"
        stdout_file = runtime_dir / f"{safe_name}_{timestamp}.stdout.log"
        stderr_file = runtime_dir / f"{safe_name}_{timestamp}.stderr.log"

        serializable = spec.params.model_dump(mode="json") if hasattr(spec.params, "model_dump") else spec.params.dict()
        params_file.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

        stdout_handle = stdout_file.open("a", encoding="utf-8")
        stderr_handle = stderr_file.open("a", encoding="utf-8")

        cmd = [
            sys.executable,
            "-m",
            "framework.universes.run_universe",
            "--params-file",
            str(params_file),
            "--status-file",
            str(status_file),
        ]
        cors = spec.cors if spec.cors is not None else ["*"]
        if cors:
            cmd.extend(["--cors", *[str(c) for c in cors]])

        try:
            proc = subprocess.Popen(cmd, stdout=stdout_handle, stderr=stderr_handle, text=True, env={**os.environ, **(spec.env or {})})
        except Exception:
            stdout_handle.close()
            stderr_handle.close()
            raise

        files = {
            "params_file": str(params_file),
            "status_file": str(status_file),
            "stdout_file": str(stdout_file),
            "stderr_file": str(stderr_file),
        }
        logs = {"stdout_file": str(stdout_file), "stderr_file": str(stderr_file)}
        handle = UniverseHandle(
            name=spec.name,
            backend=self.backend_name,
            state="starting",
            runtime_spec=spec,
            process=proc,
            files=files,
            logs=logs,
            metadata={"system": spec.system, "subprocess_pid": proc.pid, "deployment_type": "local", "backend": self.backend_name},
        )

        # Wait for process to start and poll status file for endpoint-ready state.
        time.sleep(1.0)
        if proc.poll() is not None:
            handle.state = "failed"
            handle.metadata["returncode"] = proc.returncode
            return handle

        max_wait = float(spec.startup_timeout_s or 60.0)
        poll_interval = 0.5
        elapsed = 0.0
        status_data = None
        while elapsed < max_wait:
            if proc.poll() is not None:
                break
            status_data = read_status_file(status_file)
            if endpoint_from_status(status_data):
                break
            time.sleep(poll_interval)
            elapsed += poll_interval

        if proc.poll() is not None:
            handle.state = "failed"
            handle.metadata["returncode"] = proc.returncode
            return handle

        status_data = status_data if endpoint_from_status(status_data) else read_status_file(status_file)
        endpoint_data = endpoint_from_status(status_data)
        if endpoint_data:
            handle.endpoint = UniverseEndpoint(**endpoint_data)
            handle.state = "ready"
        else:
            handle.state = "port_unknown"
        handle.metadata["startup_elapsed_s"] = elapsed
        return handle

    def status(self, handle: UniverseHandle) -> UniverseStatus:
        proc = handle.process
        rc = proc.poll() if proc is not None and hasattr(proc, "poll") else None
        if rc is None:
            state = handle.state if handle.state in {"ready", "port_unknown", "starting"} else "running"
        else:
            state = f"exited({rc})"
        status_data = read_status_file((handle.files or {}).get("status_file"))
        endpoint_data = endpoint_from_status(status_data)
        endpoint = UniverseEndpoint(**endpoint_data) if endpoint_data else handle.endpoint
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=state, endpoint=endpoint, pid=getattr(proc, "pid", None), metadata=dict(handle.metadata or {}))

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
