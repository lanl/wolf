from __future__ import annotations

import json
import os
import shutil
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


def _bool_config(config: Dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


class _VMProcessUniverseBackend(UniverseBackend):
    """Shared process lifecycle helpers for VM/microVM prototype backends."""

    backend_name = "vm"
    runtime_name = "vm"
    bin_config_key = "vm_bin"
    env_bin_key = "WOLF_VM_BIN"
    default_bin = "vm"

    def _runtime_files(self, spec: UniverseRuntimeSpec, config: Dict[str, Any]) -> tuple[Path, Dict[str, str], Dict[str, str]]:
        runtime_dir = Path(config.get("runtime_dir") or spec.runtime_dir or (Path(tempfile.gettempdir()) / "wolf_universes"))
        runtime_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_name = _safe_name(spec.name)
        files = {
            "runtime_root": str(runtime_dir),
            "params_file": str(runtime_dir / f"{safe_name}_{timestamp}.params.json"),
            "status_file": str(runtime_dir / f"{safe_name}_{timestamp}.status.json"),
            "stdout_file": str(runtime_dir / f"{safe_name}_{timestamp}.stdout.log"),
            "stderr_file": str(runtime_dir / f"{safe_name}_{timestamp}.stderr.log"),
        }
        logs = {"stdout_file": files["stdout_file"], "stderr_file": files["stderr_file"]}
        return runtime_dir, files, logs

    def _failed_handle(self, spec: UniverseRuntimeSpec, message: str, *, files: Dict[str, str] | None = None, logs: Dict[str, str] | None = None, metadata: Dict[str, Any] | None = None) -> UniverseHandle:
        return UniverseHandle(
            name=spec.name,
            backend=self.backend_name,
            state="failed",
            runtime_spec=spec,
            files=files or {},
            logs=logs or {},
            metadata={
                "backend": self.backend_name,
                "deployment_type": self.runtime_name,
                "error": message,
                **(metadata or {}),
            },
        )

    def _poll_endpoint(self, handle: UniverseHandle, proc: subprocess.Popen, status_file: str, max_wait: float, poll_interval: float) -> None:
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

    def status(self, handle: UniverseHandle) -> UniverseStatus:
        proc = handle.process
        rc = proc.poll() if proc is not None and hasattr(proc, "poll") else None
        status_data = read_status_file((handle.files or {}).get("status_file")) or {}
        endpoint_data = endpoint_from_status(status_data)
        endpoint = UniverseEndpoint(**endpoint_data) if endpoint_data else handle.endpoint
        if rc is None and endpoint and endpoint.usable:
            state = "ready"
        elif rc is None:
            state = handle.state if handle.state in {"starting", "port_unknown", "ready"} else "running"
        else:
            state = f"exited({rc})"
        return UniverseStatus(
            name=handle.name,
            backend=self.backend_name,
            state=state,
            endpoint=endpoint,
            pid=getattr(proc, "pid", None),
            metadata={**dict(handle.metadata or {}), "last_status": status_data},
        )

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


class QemuUniverseBackend(_VMProcessUniverseBackend):
    """Prototype QEMU VM backend.

    This backend intentionally defines a launch contract rather than pretending a
    generic VM can boot WOLF automatically. A real deployment must provide a VM
    image/kernel that mounts or otherwise accesses the generated runtime files
    and starts the Universe runtime inside the guest. Endpoint discovery remains
    status-file based for consistency with the rest of the backend abstraction.
    """

    backend_name = "vm_qemu"
    runtime_name = "qemu"
    bin_config_key = "qemu_bin"
    env_bin_key = "WOLF_QEMU_BIN"
    default_bin = "qemu-system-x86_64"

    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        config = dict(config or {})
        qemu_bin = str(config.get("qemu_bin") or os.environ.get("WOLF_QEMU_BIN") or self.default_bin)
        require_binary = _bool_config(config, "require_binary", True)
        if require_binary and shutil.which(qemu_bin) is None:
            return self._failed_handle(spec, f"QEMU executable not found: {qemu_bin}", metadata={"qemu_bin": qemu_bin})

        runtime_dir, files, logs = self._runtime_files(spec, config)
        serializable = spec.params.model_dump(mode="json") if hasattr(spec.params, "model_dump") else spec.params.dict()
        Path(files["params_file"]).write_text(json.dumps(serializable, indent=2), encoding="utf-8")

        disk_image = config.get("disk_image") or config.get("image")
        kernel = config.get("kernel")
        if not disk_image and not kernel and not config.get("allow_no_image"):
            return self._failed_handle(
                spec,
                "QEMU backend requires backend_config.disk_image/image or kernel, unless allow_no_image=true is set for dry/mock launches.",
                files=files,
                logs=logs,
                metadata={"qemu_bin": qemu_bin},
            )

        cmd = [qemu_bin, "-name", str(config.get("vm_name") or f"wolf-{_safe_name(spec.name)}"), "-display", str(config.get("display") or "none")]
        cmd.extend(["-m", str(config.get("memory") or "1024M")])
        cmd.extend(["-smp", str(config.get("cpus") or 1)])
        if disk_image:
            cmd.extend(["-drive", f"file={disk_image},format={config.get('disk_format', 'qcow2')},if=virtio"])
        if kernel:
            cmd.extend(["-kernel", str(kernel)])
        if config.get("initrd"):
            cmd.extend(["-initrd", str(config["initrd"])])
        append = str(config.get("append") or "")
        append = (append + f" wolf.params={files['params_file']} wolf.status={files['status_file']}").strip()
        if append:
            cmd.extend(["-append", append])
        if _bool_config(config, "share_runtime_dir", True):
            cmd.extend(["-virtfs", f"local,path={runtime_dir},mount_tag=wolf_runtime,security_model=mapped-xattr,id=wolf_runtime"])
        if config.get("guest_port"):
            host = str(config.get("host_forward_host") or "127.0.0.1")
            host_port = int(config.get("host_forward_port") or 0)
            guest_port = int(config["guest_port"])
            cmd.extend(["-netdev", f"user,id=net0,hostfwd=tcp:{host}:{host_port}-:{guest_port}", "-device", "virtio-net-pci,netdev=net0"])
        for arg in config.get("extra_qemu_args") or []:
            cmd.append(str(arg))

        stdout_handle = Path(files["stdout_file"]).open("a", encoding="utf-8")
        stderr_handle = Path(files["stderr_file"]).open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen(cmd, stdout=stdout_handle, stderr=stderr_handle, text=True, env={**os.environ, **(spec.env or {})})
        except Exception as exc:
            stdout_handle.close()
            stderr_handle.close()
            return self._failed_handle(spec, f"{type(exc).__name__}: {exc}", files=files, logs=logs, metadata={"qemu_bin": qemu_bin, "command": cmd})

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
                "qemu_bin": qemu_bin,
                "subprocess_pid": getattr(proc, "pid", None),
                "command": cmd,
                "guest_contract": "guest image must start framework.universes.run_universe and publish status.json through the configured shared runtime path or another out-of-band mechanism",
            },
        )
        self._poll_endpoint(handle, proc, files["status_file"], float(config.get("startup_timeout_s") or spec.startup_timeout_s or 60.0), float(config.get("poll_interval_s") or 0.5))
        return handle


class FirecrackerUniverseBackend(_VMProcessUniverseBackend):
    """Prototype Firecracker microVM backend.

    A real launch requires a Firecracker kernel image and root filesystem image.
    The backend generates a Firecracker config file and process handle, then uses
    the same status-file endpoint contract as other backend prototypes.
    """

    backend_name = "microvm_firecracker"
    runtime_name = "firecracker"
    bin_config_key = "firecracker_bin"
    env_bin_key = "WOLF_FIRECRACKER_BIN"
    default_bin = "firecracker"

    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        config = dict(config or {})
        firecracker_bin = str(config.get("firecracker_bin") or os.environ.get("WOLF_FIRECRACKER_BIN") or self.default_bin)
        require_binary = _bool_config(config, "require_binary", True)
        if require_binary and shutil.which(firecracker_bin) is None:
            return self._failed_handle(spec, f"Firecracker executable not found: {firecracker_bin}", metadata={"firecracker_bin": firecracker_bin})

        runtime_dir, files, logs = self._runtime_files(spec, config)
        serializable = spec.params.model_dump(mode="json") if hasattr(spec.params, "model_dump") else spec.params.dict()
        Path(files["params_file"]).write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        fc_config_file = runtime_dir / f"{_safe_name(spec.name)}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.firecracker.json"
        files["firecracker_config_file"] = str(fc_config_file)

        kernel = config.get("kernel_image") or config.get("kernel")
        rootfs = config.get("rootfs") or config.get("rootfs_image")
        if (not kernel or not rootfs) and not config.get("allow_incomplete_config"):
            return self._failed_handle(
                spec,
                "Firecracker backend requires backend_config.kernel_image and rootfs/rootfs_image unless allow_incomplete_config=true is set for dry/mock launches.",
                files=files,
                logs=logs,
                metadata={"firecracker_bin": firecracker_bin},
            )

        boot_args = str(config.get("boot_args") or "console=ttyS0 reboot=k panic=1 pci=off")
        boot_args = (boot_args + f" wolf.params={files['params_file']} wolf.status={files['status_file']}").strip()
        fc_config = {
            "boot-source": {"kernel_image_path": str(kernel or "<kernel_image_required>"), "boot_args": boot_args},
            "drives": [{"drive_id": "rootfs", "path_on_host": str(rootfs or "<rootfs_required>"), "is_root_device": True, "is_read_only": bool(config.get("rootfs_read_only", False))}],
            "machine-config": {"vcpu_count": int(config.get("vcpu_count") or config.get("cpus") or 1), "mem_size_mib": int(config.get("mem_size_mib") or 512)},
            "logger": {"log_path": files["stderr_file"], "level": str(config.get("log_level") or "Info"), "show_level": True, "show_log_origin": True},
        }
        if config.get("network_interfaces"):
            fc_config["network-interfaces"] = config["network_interfaces"]
        fc_config_file.write_text(json.dumps(fc_config, indent=2), encoding="utf-8")

        api_sock = str(config.get("api_sock") or (runtime_dir / f"{_safe_name(spec.name)}.firecracker.sock"))
        cmd = [firecracker_bin, "--api-sock", api_sock, "--config-file", str(fc_config_file)]
        for arg in config.get("extra_firecracker_args") or []:
            cmd.append(str(arg))

        stdout_handle = Path(files["stdout_file"]).open("a", encoding="utf-8")
        stderr_handle = Path(files["stderr_file"]).open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen(cmd, stdout=stdout_handle, stderr=stderr_handle, text=True, env={**os.environ, **(spec.env or {})})
        except Exception as exc:
            stdout_handle.close()
            stderr_handle.close()
            return self._failed_handle(spec, f"{type(exc).__name__}: {exc}", files=files, logs=logs, metadata={"firecracker_bin": firecracker_bin, "command": cmd})

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
                "firecracker_bin": firecracker_bin,
                "api_sock": api_sock,
                "subprocess_pid": getattr(proc, "pid", None),
                "command": cmd,
                "guest_contract": "guest rootfs/init must start framework.universes.run_universe and publish endpoint metadata to the configured status path or an equivalent host-visible channel",
            },
        )
        self._poll_endpoint(handle, proc, files["status_file"], float(config.get("startup_timeout_s") or spec.startup_timeout_s or 60.0), float(config.get("poll_interval_s") or 0.5))
        return handle
