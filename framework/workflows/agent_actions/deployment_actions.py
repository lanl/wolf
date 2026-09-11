from __future__ import annotations

import os
import subprocess
import sys
import json
import tempfile
import time
from pathlib import Path
from datetime import datetime
from typing import Literal, Dict, Any
from pydantic import BaseModel, ConfigDict, Field
import copy

from framework.workflows.base_agent_action import AgentAction
from framework.universes.universe_tools import build_params_from_info, get_base_universe_params
from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams
#from framework.knowledgebase.knowledge_base import KnowledgeBase
#from framework.tooling.toolbox import ToolBox
from framework.universes.base_universe import run_app
from framework.universes.remote_deployment import RemoteDeploymentManager, RemoteUniverseHandle
from framework.universes.status_files import apply_status_to_infra, endpoint_from_status, read_status_file
from framework.universes.deployment_backends.models import UniverseRuntimeSpec, UniverseHandle
from framework.universes.deployment_backends.registry import get_universe_backend, list_universe_backends

#
# BaseUniver
#

# ---------------------------
# Create Universe Action
# ---------------------------
class CreateUniverseArgs(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"system": "local", "name": "example_universe", "univ_params": {"kbs": {}, "tbs": {}, "info": {"name": "example_universe", "host": "127.0.0.1", "port": 0, "description": ""}}}]})

    system: str = Field(description="System where the universe will be created, e.g., 'local'")
    name: str = Field(description="Name for the new universe")
    #info: dict = Field(
    #    default_factory=dict,
    #    description="Optional BaseUniverseModel fields (host, port, name, etc.)",
    #)
    univ_params: BaseUniverseParams = Field(description=f"Parameters of the universe, {BaseUniverseParams.model_fields}")
    backend: str | None = Field(default=None, description="Deployment backend name; defaults to local_process for local hosts and ssh_process for remote hosts")
    backend_config: Dict[str, Any] = Field(default_factory=dict, description="Optional backend-specific deployment configuration")

class CreateUniverseAction(AgentAction):
    """Create, configure and launch a new universe in a separate process, then register it.

    The universe is instantiated, stored in ``infra.UNIVs`` and the FastAPI app is started
    via ``uvicorn`` in a background subprocess. The subprocess handle together with the
    creation parameters and metadata are saved in ``infra.managed_deployments`` for later
    inspection or termination.
    
    Supports both local and remote deployment via SSH.
    """
    action: Literal["create_universe"] = "create_universe"
    description: Literal["Create, configure and launch a new universe"] = "Create, configure and launch a new universe"
    payload: CreateUniverseArgs
    #payload_schema: str = """{
    #"system": "name of system the universe is connected to, e.g. 'local'",
    #"name": "name of the universe",
    #"info": {
    #    "host": "127.0.0.1",
    #    "port": 0,
    #    "description": "...",
    #    "api_version": null,
    #    "api_token": null,
    #    "ssh_config": {
    #        "user": "username",
    #        "key_path": "/path/to/ssh/key",
    #        "remote_python_path": "python3",
    #        "remote_work_dir": "/tmp"
    #    }
    # }
    #}"""
    payload_schema: str = f"{CreateUniverseArgs.model_fields}"

    def execute(self, infra) -> None:
        deployments: Dict[str, Dict[str, Any]] = getattr(infra, "managed_deployments", {})
        
        if self.payload.name in deployments:
            infra.append_chat_history(
                actor="system",
                content=f"Deployment '{self.payload.name}' already exists:\n  -> Info( Universe [{self.payload.name}] ) = {infra.UNIVs[self.payload.name]}",
                action={"action": "create_universe"},
                log_console=True,
            )
            return
        #info_data = dict(self.payload.info or {})
        if isinstance(self.payload.univ_params, BaseUniverseParams):
            params = self.payload.univ_params
        elif isinstance(self.payload.univ_params, dict):
           params = BaseUniverseParams.model_validate(self.payload.univ_params)
        elif isinstance(self.payload.univ_params, (str, bytes)):
           params = BaseUniverseParams.model_validate_json(self.payload.univ_params)
        else:
            infra.append_chat_history(
                actor="system",
                content=f"Unsupported type for univ_params: {type(self.payload.univ_params)}",
                action={"action": "create_universe"},
                log_console=True,
            )
            return
        info_data = dict(params.info or {})
        info_data.setdefault("name", self.payload.name)
        info_data.setdefault("host", "127.0.0.1")
        info_data.setdefault("port", 0)
        
        try:
            info_instance = BaseUniverseModel(**info_data)
            #params = BaseUniverseParams(info=info_instance, kbs=None, tbs=None)
            params.info = info_instance
            self.payload.univ_params = params
        except Exception as e:
            infra.append_chat_history(
                actor="system",
                content=f"Failed to validate universe parameters for '{self.payload.name}': {e}",
                action={"action": "create_universe"},
                log_console=True,
            ) 
            return
        
        # Validate remote configuration if needed
        try:
            info_instance.validate_remote_config()
        except ValueError as e:
            infra.append_chat_history(
                actor="system",
                content=f"Invalid remote configuration for '{self.payload.name}': {e}",
                action={"action": "create_universe"},
                log_console=True,
            )
            return

        if hasattr(infra, "request_permission"):
            requested_backend = self.payload.backend or ("ssh_process" if info_instance.is_remote() else "local_process")
            if requested_backend == "remote":
                requested_backend = "ssh_process"
            deployment_type = "remote" if requested_backend == "ssh_process" else ("local" if requested_backend == "local_process" else requested_backend)
            backend_risk = "starts a Podman container" if requested_backend == "podman" else ("starts a Docker container" if requested_backend == "docker" else ("creates Kubernetes workload resources" if requested_backend == "kubernetes" else ("starts a detached terminal multiplexer session" if requested_backend in {"screen", "tmux"} else ("starts a Ray actor runtime" if requested_backend == "ray" else ("submits a Slurm batch job" if requested_backend == "slurm" else ("starts a local sandbox runtime" if requested_backend in {"sandbox_bubblewrap", "sandbox_nsjail"} else ("starts a VM or microVM runtime" if requested_backend in {"vm_qemu", "microvm_firecracker"} else ("launches a local subprocess" if deployment_type == "local" else "starts a remote process over SSH"))))))))
            approval_request = {
                "action": self.action,
                "payload": self.payload.model_dump(mode="json"),
                "payload_summary": f"Create {deployment_type} universe '{self.payload.name}'",
                "operation": "create_universe",
                "target_path": self.payload.name,
                "cwd": os.getcwd(),
                "purpose": self.purpose,
                "expectations": self.expectations,
                "risk_hints": [
                    "starts a new universe/actionbox runtime",
                    backend_risk,
                    deployment_type,
                    f"backend={requested_backend}",
                ],
                "metadata": {
                    "universe": self.payload.name,
                    "deployment_type": deployment_type,
                    "backend": requested_backend,
                    "host": getattr(info_instance, "host", None),
                    "port": getattr(info_instance, "port", None),
                },
            }
            approval = infra.request_permission("create_universe", approval_request)
            if not approval.get("approved", False):
                result = {
                    "ok": False,
                    "approved": False,
                    "action": self.action,
                    "universe": self.payload.name,
                    "error": approval.get("reason") or "create_universe was denied by user approval policy",
                    "approval": approval,
                }
                infra.append_chat_history(
                    actor="system",
                    content=f"[CreateUniverseAction][denied]: {result}",
                    action={"action": "create_universe"},
                    log_console=True,
                )
                return result
        
        infra.UNIVs[self.payload.name] = params

        # Check if remote deployment
        backend_name = self.payload.backend or ("ssh_process" if info_instance.is_remote() else "local_process")
        if backend_name == "remote":
            backend_name = "ssh_process"
        self._execute_backend_deployment(infra, params, deployments, backend_name=backend_name)

    def _execute_backend_deployment(self, infra, params: BaseUniverseParams, deployments: Dict[str, Dict[str, Any]], backend_name: str = "local_process") -> None:
        """Execute Universe deployment through a registered backend.

        Phase 2 keeps the legacy managed_deployments entry shape while moving
        local subprocess launch/status behavior into LocalProcessUniverseBackend.
        """
        try:
            backend = get_universe_backend(backend_name)
        except Exception as e:
            infra.append_chat_history(
                actor="system",
                content=f"Failed to resolve universe backend '{backend_name}' for '{self.payload.name}': {e}",
                action={"action": "create_universe"},
                log_console=True,
            )
            return

        startup_timeout = float(os.environ.get("WOLF_UNIVERSE_STARTUP_TIMEOUT", "60") or 60)
        if isinstance(getattr(self.payload, "backend_config", None), dict):
            startup_timeout = float(self.payload.backend_config.get("startup_timeout_s", startup_timeout) or startup_timeout)
        spec = UniverseRuntimeSpec(
            name=self.payload.name,
            params=params,
            system=self.payload.system,
            cors=["*"],
            startup_timeout_s=startup_timeout,
            metadata={"purpose": self.purpose, "expectations": self.expectations},
        )
        serializable = params.model_dump(mode="json") if hasattr(params, "model_dump") else params.dict()

        try:
            handle = backend.launch(spec, config=getattr(self.payload, "backend_config", {}) or {})
        except Exception as e:
            infra.append_chat_history(
                actor="system",
                content=f"Failed to launch universe '{self.payload.name}' via backend '{backend_name}': {e}",
                action={"action": "create_universe"},
                log_console=True,
            )
            return

        if handle.endpoint and handle.endpoint.usable:
            infra.UNIVs[self.payload.name].info.host = handle.endpoint.host
            infra.UNIVs[self.payload.name].info.port = handle.endpoint.port

        entry = handle.legacy_entry(serializable)
        deployments[self.payload.name] = entry
        if entry.get("status_file"):
            status_data = read_status_file(entry.get("status_file"))
            apply_status_to_infra(infra, self.payload.name, status_data)

        stored_port = getattr(infra.UNIVs[self.payload.name].info, "port", 0)
        verification_successful = bool(handle.endpoint and handle.endpoint.usable and int(stored_port or 0) == int(handle.endpoint.port))
        if handle.state == "failed":
            msg = (
                f"Universe '{self.payload.name}' failed to launch via backend '{backend_name}'.\n"
                f"PID: {getattr(handle.process, 'pid', None)}\n"
                f"stderr log: {handle.files.get('stderr_file')}"
            )
        elif handle.endpoint and handle.endpoint.usable:
            msg = (
                f"Universe '{self.payload.name}' launched and registered successfully via backend '{backend_name}'.\n"
                f"PID: {getattr(handle.process, 'pid', None)}\n"
                f"Host: {handle.endpoint.host}\n"
                f"Port: {handle.endpoint.port}\n"
                f"URL: {handle.endpoint.url}\n"
                f"Verification: {'successful' if verification_successful else 'FAILED'}"
            )
        else:
            msg = (
                f"Universe '{self.payload.name}' launched via backend '{backend_name}' "
                f"(PID {getattr(handle.process, 'pid', None)}) but port information is not yet available. "
                f"Status file: {handle.files.get('status_file')}"
            )

        infra.append_chat_history(
            actor="system",
            content=msg,
            action={"action": "create_universe"},
            log_console=True,
        )

    def _execute_local_deployment(self, infra, params: BaseUniverseParams, deployments: Dict[str, Dict[str, Any]]) -> None:
        """Backward-compatible wrapper for the extracted local process backend."""
        return self._execute_backend_deployment(infra, params, deployments, backend_name="local_process")

    def _execute_remote_deployment(self, infra, params: BaseUniverseParams, deployments: Dict[str, Dict[str, Any]]) -> None:
        """Backward-compatible wrapper for the extracted SSH process backend."""
        return self._execute_backend_deployment(infra, params, deployments, backend_name="ssh_process")


# ---------------------------
# List Deployments Action
# ---------------------------
class ListDeploymentsArgs(BaseModel):
    system: str = Field(description="System identifier, e.g., 'local'")

class ListDeploymentsAction(AgentAction):
    """Return a snapshot of ``infra.managed_deployments``.

    The action simply reads the dictionary and writes a formatted message to the
    chat history. No mutation is performed.
    """
    action: Literal["list_deployments"] = "list_deployments"
    description: Literal["List all managed deployments (universes, TBs, KBs, etc.)"] = "List all managed deployments (universes, TBs, KBs, etc.)"
    payload: ListDeploymentsArgs
    payload_schema: str = """ {"system": "string"}""" 

    def execute(self, infra) -> None:
        deployments: Dict[str, Dict[str, Any]] = getattr(infra, "managed_deployments", {})

        if not deployments:
            msg = "No managed deployments are currently registered."
        else:
            lines = ["Managed Deployments:"]

            for name, info in deployments.items():
                meta = info.get("meta_data", {})
                typ = meta.get("type", "unknown")
                status = meta.get("status", "unknown")
                pid = meta.get("subprocess_pid")
                deployment_type = meta.get("deployment_type", "unknown")
                extra = [f"deployment_type={deployment_type}"]

                backend_handle = info.get("backend_handle")
                if isinstance(backend_handle, UniverseHandle):
                    extra.append(f"backend={backend_handle.backend}")
                    if backend_handle.endpoint and backend_handle.endpoint.usable:
                        extra.append(f"url={backend_handle.endpoint.url}")
                handle = info.get("handle")
                
                # Handle local subprocess
                if isinstance(handle, subprocess.Popen):
                    rc = handle.poll()
                    if rc is None:
                        status = "running"
                    else:
                        status = f"exited({rc})"
                    meta["status"] = status
                
                # Handle remote universe
                elif isinstance(handle, RemoteUniverseHandle):
                    rc = handle.poll()
                    if rc is None:
                        status = "running"
                    else:
                        status = f"exited({rc})"
                    meta["status"] = status
                
                    extra.append(f"remote_host={handle.remote_host}")
                    extra.append(f"remote_user={handle.remote_user}")
                    if handle.actual_port:
                        extra.append(f"port={handle.actual_port}")

                status_file = info.get("status_file")
                if status_file and deployment_type == "local":
                    try:
                        with open(status_file, "r", encoding="utf-8") as f:
                            runtime_status = json.load(f)

                        runtime_state = runtime_status.get("status")
                        runtime_host = runtime_status.get("host")
                        runtime_port = runtime_status.get("port")
                        runtime_url = runtime_status.get("url")

                        if runtime_state:
                            extra.append(f"runtime_status={runtime_state}")
                        if runtime_host:
                            extra.append(f"host={runtime_host}")
                        if runtime_port is not None:
                            extra.append(f"port={runtime_port}")
                        if runtime_url:
                            extra.append(f"url={runtime_url}")
                    except Exception:
                        pass

                suffix = f", " + ", ".join(extra) if extra else ""
                lines.append(f"- {name}: type={typ}, status={status}, pid={pid}{suffix}")

            msg = "\n".join(lines)

        infra.append_chat_history(
            actor="system",
            content=msg,
            action={"action": "list_deployments"},
            log_console=True,
        )


# ---------------------------
# Terminate Deployment Action
# ---------------------------

class TerminateDeploymentArgs(BaseModel):
    system: str = Field(description="System identifier, e.g., 'local'")
    name: str = Field(description="Name of the deployment to terminate")
    force: bool = Field(default=False, description="If true, kill the process immediately")
    remove_files: bool = Field(default=True, description="If true, delete params/status/log files")

class TerminateDeploymentAction(AgentAction):
    """Terminate a managed deployment.

    For subprocess handles the action sends ``terminate`` (or ``kill`` when ``force``
    is true) and removes the entry from ``infra.managed_deployments``. For other
    object types the entry is simply removed.
    
    Supports both local and remote universe termination.
    """ 
    action: Literal["terminate_deployment"] = "terminate_deployment"
    description: Literal["Terminate a managed deployment (universe, TB, KB, etc.)"] = "Terminate a managed deployment (universe, TB, KB, etc.)"
    payload: TerminateDeploymentArgs
    payload_schema: str = """{
    "system": "string",
    "name": "string",
    "force": "boolean (optional)",
    "remove_files": "boolean (optional)"
}"""

    def execute(self, infra) -> None:
        name = self.payload.name.strip()
        deployments = getattr(infra, "managed_deployments", {})
        entry = deployments.get(name)

        if not entry:
            infra.append_chat_history(
                actor="system",
                content=f"Deployment '{name}' not found.",
                action={"action": "terminate_deployment"},
                log_console=True,
            )
            return

        handle = entry.get("handle")
        meta = entry.get("meta_data", {})
        deployment_type = meta.get("deployment_type", "unknown")
        messages = []

        if hasattr(infra, "request_permission"):
            approval_request = {
                "action": self.action,
                "payload": self.payload.model_dump(mode="json"),
                "payload_summary": f"Terminate deployment '{name}' (force={self.payload.force}, remove_files={self.payload.remove_files})",
                "operation": "terminate_deployment",
                "target_path": name,
                "cwd": os.getcwd(),
                "purpose": self.purpose,
                "expectations": self.expectations,
                "risk_hints": [
                    "terminates a managed deployment",
                    "may kill a local or remote process",
                    "may remove deployment files" if self.payload.remove_files else "keeps deployment files",
                    "force kill requested" if self.payload.force else "graceful termination requested",
                ],
                "metadata": {
                    "deployment": name,
                    "deployment_type": deployment_type,
                    "meta_data": meta,
                },
            }
            approval = infra.request_permission("terminate_deployment", approval_request)
            if not approval.get("approved", False):
                result = {
                    "ok": False,
                    "approved": False,
                    "action": self.action,
                    "deployment": name,
                    "error": approval.get("reason") or "terminate_deployment was denied by user approval policy",
                    "approval": approval,
                }
                infra.append_chat_history(
                    actor="system",
                    content=f"[TerminateDeploymentAction][denied]: {result}",
                    action={"action": "terminate_deployment"},
                    log_console=True,
                )
                return result

        backend_handle = entry.get("backend_handle")
        if isinstance(backend_handle, UniverseHandle):
            try:
                backend = get_universe_backend(backend_handle.backend)
                status = backend.terminate(backend_handle, force=self.payload.force, timeout=10.0)
                meta["status"] = status.state
                messages.append(
                    f"Backend '{backend_handle.backend}' deployment '{name}' terminated (force={self.payload.force})."
                )
            except Exception as e:
                messages.append(f"Error terminating backend deployment for '{name}': {e}")

        # Handle local subprocess
        elif isinstance(handle, subprocess.Popen):
            try:
                if handle.poll() is None:
                    if self.payload.force:
                        handle.kill()
                    else:
                        handle.terminate()
                    handle.wait(timeout=10)

                rc = handle.poll()
                meta["status"] = f"terminated({rc})" if rc is not None else "terminated"
                messages.append(
                    f"Subprocess for deployment '{name}' terminated (force={self.payload.force})."
                )
            except subprocess.TimeoutExpired:
                messages.append(
                    f"Timeout while terminating subprocess for '{name}'."
                )
                if not self.payload.force:
                    try:
                        handle.kill()
                        handle.wait(timeout=5)
                        rc = handle.poll()
                        meta["status"] = f"killed({rc})"
                        messages.append(f"Subprocess for '{name}' was killed after timeout.")
                    except Exception as e:
                        messages.append(f"Failed to kill subprocess for '{name}': {e}")
            except Exception as e:
                messages.append(f"Error terminating subprocess for '{name}': {e}")
        
        # Handle remote universe
        elif isinstance(handle, RemoteUniverseHandle):
            try:
                RemoteDeploymentManager.terminate_remote_universe(
                    handle, force=self.payload.force, timeout=10.0
                )
                meta["status"] = "terminated"
                messages.append(
                    f"Remote universe '{name}' at {handle.remote_user}@{handle.remote_host} "
                    f"terminated (force={self.payload.force})."
                )
            except Exception as e:
                messages.append(f"Error terminating remote universe '{name}': {e}")

        if self.payload.remove_files and isinstance(entry.get("backend_handle"), UniverseHandle):
            try:
                cleanup_backend = get_universe_backend(entry["backend_handle"].backend)
                removed_keys = cleanup_backend.cleanup(entry["backend_handle"])
                if removed_keys:
                    messages.append(f"Backend cleanup removed files: {removed_keys}")
            except Exception as e:
                messages.append(f"Backend cleanup failed for '{name}': {e}")

        if self.payload.remove_files:
            for key in ("params_file", "status_file", "stdout_file", "stderr_file"):
                path = entry.get(key)
                if path and deployment_type == "local":
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception as e:
                        messages.append(f"Could not remove {key} for '{name}': {e}")

        deployments.pop(name, None)
        getattr(infra, "UNIVs", {}).pop(name, None)

        messages.append(f"Deployment '{name}' removed from managed_deployments.")

        infra.append_chat_history(
            actor="system",
            content="\n".join(messages),
            action={"action": "terminate_deployment"},
            log_console=True,
        )
