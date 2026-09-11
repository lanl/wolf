from __future__ import annotations

import json
import os
import shlex
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


def _safe_name(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(value or "universe"))
    return cleaned.strip("_") or "universe"


def _first_job_id(stdout: str) -> str:
    text = str(stdout or "").strip()
    if not text:
        return ""
    first = text.splitlines()[-1].strip()
    if ";" in first:
        first = first.split(";", 1)[0]
    return first.split()[0] if first.split() else first


class SlurmUniverseBackend(UniverseBackend):
    """Run a Universe as a Slurm batch job using Slurm CLI tools.

    The backend is intentionally CLI-based and dependency-free. It writes a
    small batch script that starts ``python -m framework.universes.run_universe``
    and tracks the returned Slurm job id. Endpoint discovery uses the Universe
    status file, which works for shared filesystems and local/mock tests.
    """

    backend_name = "slurm"

    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        config = dict(config or {})
        sbatch_bin = str(config.get("sbatch_bin") or os.environ.get("WOLF_SBATCH_BIN") or "sbatch")
        squeue_bin = str(config.get("squeue_bin") or os.environ.get("WOLF_SQUEUE_BIN") or "squeue")
        scancel_bin = str(config.get("scancel_bin") or os.environ.get("WOLF_SCANCEL_BIN") or "scancel")
        python_bin = str(config.get("python_bin") or os.environ.get("WOLF_SLURM_PYTHON_BIN") or "python")

        runtime_dir = Path(config.get("runtime_dir") or spec.runtime_dir or (Path(tempfile.gettempdir()) / "wolf_universes"))
        runtime_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_name = _safe_name(spec.name)
        job_name = str(config.get("job_name") or f"wolf_{safe_name}_{timestamp}")[:128]

        params_file = runtime_dir / f"{safe_name}_{timestamp}.params.json"
        status_file = runtime_dir / f"{safe_name}_{timestamp}.status.json"
        stdout_file = runtime_dir / f"{safe_name}_{timestamp}.stdout.log"
        stderr_file = runtime_dir / f"{safe_name}_{timestamp}.stderr.log"
        script_file = runtime_dir / f"{safe_name}_{timestamp}.slurm.sh"

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

        script_lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
        header_map = {
            "partition": "--partition",
            "account": "--account",
            "time": "--time",
            "nodes": "--nodes",
            "ntasks": "--ntasks",
            "cpus_per_task": "--cpus-per-task",
            "mem": "--mem",
            "gres": "--gres",
            "constraint": "--constraint",
            "qos": "--qos",
            "reservation": "--reservation",
            "dependency": "--dependency",
        }
        script_lines.extend([
            f"#SBATCH --job-name={job_name}",
            f"#SBATCH --output={stdout_file}",
            f"#SBATCH --error={stderr_file}",
        ])
        for key, flag in header_map.items():
            if config.get(key) is not None:
                script_lines.append(f"#SBATCH {flag}={config[key]}")
        for arg in config.get("extra_sbatch_args") or []:
            script_lines.append(f"#SBATCH {arg}")
        for line in config.get("module_lines") or config.get("setup_lines") or []:
            script_lines.append(str(line))
        script_lines.append(f"cd {shlex.quote(str(Path.cwd()))}")
        env = {**(spec.env or {}), **{str(k): str(v) for k, v in (config.get("env") or {}).items()}}
        for key, value in env.items():
            script_lines.append(f"export {shlex.quote(str(key))}={shlex.quote(str(value))}")
        script_lines.append(" ".join(shlex.quote(part) for part in run_cmd))
        script_file.write_text("\n".join(script_lines) + "\n", encoding="utf-8")

        files = {
            "runtime_root": str(runtime_dir),
            "params_file": str(params_file),
            "status_file": str(status_file),
            "stdout_file": str(stdout_file),
            "stderr_file": str(stderr_file),
            "script_file": str(script_file),
        }
        logs = {"stdout_file": str(stdout_file), "stderr_file": str(stderr_file)}
        metadata: Dict[str, Any] = {
            "backend": self.backend_name,
            "deployment_type": "slurm",
            "system": spec.system,
            "job_name": job_name,
            "sbatch_bin": sbatch_bin,
            "squeue_bin": squeue_bin,
            "scancel_bin": scancel_bin,
            "script_file": str(script_file),
            "run_command": run_cmd,
        }

        submit_cmd = [sbatch_bin, "--parsable", str(script_file)]
        result = subprocess.run(submit_cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return UniverseHandle(
                name=spec.name,
                backend=self.backend_name,
                state="failed",
                runtime_spec=spec,
                files=files,
                logs=logs,
                metadata={**metadata, "submit_command": submit_cmd, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
            )

        job_id = _first_job_id(result.stdout)
        process_handle = {"job_id": job_id, "sbatch_bin": sbatch_bin, "squeue_bin": squeue_bin, "scancel_bin": scancel_bin}
        handle = UniverseHandle(
            name=spec.name,
            backend=self.backend_name,
            state="submitted",
            runtime_spec=spec,
            process=process_handle,
            files=files,
            logs=logs,
            metadata={**metadata, "job_id": job_id, "submit_command": submit_cmd, "submit_stdout": result.stdout},
        )

        max_wait = float(spec.startup_timeout_s or config.get("startup_timeout_s") or 60.0)
        poll_interval = float(config.get("poll_interval_s") or 1.0)
        elapsed = 0.0
        endpoint_data = None
        last_state = "submitted"
        while elapsed < max_wait:
            status_data = read_status_file(status_file)
            endpoint_data = endpoint_from_status(status_data)
            if endpoint_data:
                break
            last_state = self._job_state(handle) or last_state
            if last_state in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}:
                break
            time.sleep(poll_interval)
            elapsed += poll_interval

        endpoint_data = endpoint_data or endpoint_from_status(read_status_file(status_file))
        if endpoint_data:
            connect_host = config.get("connect_host")
            if connect_host:
                endpoint_data = {**endpoint_data, "host": str(connect_host)}
            handle.endpoint = UniverseEndpoint(**endpoint_data)
            handle.state = "ready"
        elif last_state in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}:
            handle.state = last_state.lower()
        else:
            handle.state = "queued_or_running"
        handle.metadata["startup_elapsed_s"] = elapsed
        handle.metadata["slurm_state"] = last_state
        if handle.endpoint:
            handle.metadata.update({"host": handle.endpoint.host, "port": handle.endpoint.port, "url": handle.endpoint.url})
        return handle

    def _job_id(self, handle: UniverseHandle) -> str:
        process = handle.process if isinstance(handle.process, dict) else {}
        return str(process.get("job_id") or handle.metadata.get("job_id") or "")

    def _run_slurm(self, handle: UniverseHandle, bin_key: str, default_bin: str, args: list[str]) -> subprocess.CompletedProcess:
        process = handle.process if isinstance(handle.process, dict) else {}
        bin_ = str(process.get(bin_key) or handle.metadata.get(bin_key) or default_bin)
        return subprocess.run([bin_, *args], capture_output=True, text=True, check=False)

    def _job_state(self, handle: UniverseHandle) -> str | None:
        job_id = self._job_id(handle)
        if not job_id:
            return None
        result = self._run_slurm(handle, "squeue_bin", "squeue", ["-h", "-j", job_id, "-o", "%T"])
        if result.returncode != 0:
            return None
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        return lines[0] if lines else "COMPLETED"

    def status(self, handle: UniverseHandle) -> UniverseStatus:
        status_data = read_status_file((handle.files or {}).get("status_file")) or {}
        endpoint_data = endpoint_from_status(status_data)
        endpoint = UniverseEndpoint(**endpoint_data) if endpoint_data else handle.endpoint
        slurm_state = self._job_state(handle)
        if endpoint and endpoint.usable:
            state = "ready"
        elif slurm_state:
            state = slurm_state.lower()
        else:
            state = handle.state or "unknown"
        return UniverseStatus(
            name=handle.name,
            backend=self.backend_name,
            state=state,
            endpoint=endpoint,
            metadata={**dict(handle.metadata or {}), "slurm_state": slurm_state, "last_status": status_data},
        )

    def terminate(self, handle: UniverseHandle, force: bool = False, timeout: float = 10.0) -> UniverseStatus:
        job_id = self._job_id(handle)
        if not job_id:
            handle.state = "terminate_failed"
            return UniverseStatus(name=handle.name, backend=self.backend_name, state=handle.state, endpoint=handle.endpoint, metadata=dict(handle.metadata or {}), error="missing Slurm job id")
        result = self._run_slurm(handle, "scancel_bin", "scancel", [job_id])
        ok = result.returncode == 0
        handle.state = "terminated" if ok else "terminate_failed"
        handle.metadata["status"] = handle.state
        handle.metadata["scancel_returncode"] = result.returncode
        if result.stderr:
            handle.metadata["scancel_stderr"] = result.stderr
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=handle.state, endpoint=handle.endpoint, metadata=dict(handle.metadata or {}), error=None if ok else result.stderr)

    def cleanup(self, handle: UniverseHandle) -> list[str]:
        removed: list[str] = []
        for key, path in (handle.files or {}).items():
            try:
                Path(path).unlink(missing_ok=True)
                removed.append(key)
            except Exception:
                pass
        return removed
