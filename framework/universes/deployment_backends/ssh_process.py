from __future__ import annotations

from typing import Any, Dict

from framework.universes.deployment_backends.base import UniverseBackend
from framework.universes.deployment_backends.models import (
    UniverseEndpoint,
    UniverseHandle,
    UniverseRuntimeSpec,
    UniverseStatus,
)
from framework.universes.remote_deployment import RemoteDeploymentManager, RemoteUniverseHandle


class SSHProcessUniverseBackend(UniverseBackend):
    """SSH-backed Universe deployment backend.

    This Phase 3 adapter keeps the proven RemoteDeploymentManager mechanics but
    exposes them through the common deployment backend interface introduced in
    Phase 2. It intentionally preserves the legacy RemoteUniverseHandle as the
    process handle so existing list/terminate/snapshot paths remain compatible.
    """

    backend_name = "ssh_process"

    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        params = spec.params
        ssh_config = dict(getattr(params.info, "ssh_config", None) or {})
        ssh_config.update(dict((config or {}).get("ssh_config", {}) or {}))
        cors_values = spec.cors if spec.cors is not None else ["*"]
        cors = cors_values[0] if isinstance(cors_values, list) and cors_values else cors_values

        remote = RemoteDeploymentManager.deploy_universe_remote(
            params=params,
            ssh_config=ssh_config,
            cors=str(cors) if cors is not None else None,
        )

        endpoint = None
        if getattr(remote, "actual_port", None):
            endpoint = UniverseEndpoint(host=remote.remote_host, port=int(remote.actual_port))

        handle = UniverseHandle(
            name=spec.name,
            backend=self.backend_name,
            state="ready" if endpoint and endpoint.usable else "port_unknown",
            endpoint=endpoint,
            runtime_spec=spec,
            process=remote,
            files={
                "params_file": remote.remote_params_file,
                "status_file": remote.remote_status_file,
                "stdout_file": remote.local_stdout_file,
                "stderr_file": remote.local_stderr_file,
            },
            logs={
                "stdout_file": remote.local_stdout_file,
                "stderr_file": remote.local_stderr_file,
            },
            metadata={
                "system": spec.system,
                "subprocess_pid": remote.remote_pid,
                "deployment_type": "remote",
                "backend": self.backend_name,
                "remote_host": remote.remote_host,
                "remote_user": remote.remote_user,
                "remote_work_dir": remote.remote_work_dir,
                "actual_port": remote.actual_port,
            },
        )
        return handle

    def status(self, handle: UniverseHandle) -> UniverseStatus:
        remote = handle.process
        rc = remote.poll() if isinstance(remote, RemoteUniverseHandle) else None
        state = "running" if rc is None else f"exited({rc})"
        if handle.endpoint and handle.endpoint.usable and rc is None:
            state = "ready"
        return UniverseStatus(
            name=handle.name,
            backend=self.backend_name,
            state=state,
            endpoint=handle.endpoint,
            pid=getattr(remote, "remote_pid", None),
            metadata=dict(handle.metadata or {}),
        )

    def terminate(self, handle: UniverseHandle, force: bool = False, timeout: float = 10.0) -> UniverseStatus:
        remote = handle.process
        if isinstance(remote, RemoteUniverseHandle):
            RemoteDeploymentManager.terminate_remote_universe(remote, force=force, timeout=timeout)
        handle.state = "terminated"
        handle.metadata["status"] = "terminated"
        return UniverseStatus(
            name=handle.name,
            backend=self.backend_name,
            state="terminated",
            endpoint=handle.endpoint,
            pid=getattr(remote, "remote_pid", None),
            metadata=dict(handle.metadata or {}),
        )

    def logs(self, handle: UniverseHandle, tail: int = 200) -> Dict[str, str]:
        remote = handle.process
        if isinstance(remote, RemoteUniverseHandle):
            stdout, stderr = RemoteDeploymentManager.fetch_remote_logs(remote)
            return {"stdout": stdout, "stderr": stderr}
        return super().logs(handle, tail=tail)
