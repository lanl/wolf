from __future__ import annotations

import json
import os
import re
import socket
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


def _safe_k8s_name(value: str, *, prefix: str = "wolf-universe") -> str:
    text = str(value or "universe").lower()
    text = re.sub(r"[^a-z0-9-]+", "-", text).strip("-") or "universe"
    if not text[0].isalnum():
        text = f"u-{text}"
    if not text[-1].isalnum():
        text = f"{text}-u"
    base = f"{prefix}-{text}" if prefix else text
    return base[:63].strip("-") or "wolf-universe"


def _bool_config(config: Dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _pick_free_local_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


class KubernetesUniverseBackend(UniverseBackend):
    """Run a Universe in Kubernetes using the kubectl CLI.

    Runtime contract:
    - The image must be able to execute ``python -m framework.universes.run_universe``.
    - Params are stored in a ConfigMap mounted at ``/wolf/config/params.json``.
    - A writable ``emptyDir`` is mounted at ``/wolf/runtime`` for ``status.json``.
    - The pod listens on ``0.0.0.0:<container_port>``.
    - The backend exposes the service locally through ``kubectl port-forward``.
    """

    backend_name = "kubernetes"

    def launch(self, spec: UniverseRuntimeSpec, config: Dict[str, Any] | None = None) -> UniverseHandle:
        config = dict(config or {})
        kubectl_bin = str(config.get("kubectl_bin") or os.environ.get("WOLF_KUBECTL_BIN") or "kubectl")
        image = str(
            config.get("image")
            or os.environ.get("WOLF_UNIVERSE_KUBERNETES_IMAGE")
            or os.environ.get("WOLF_UNIVERSE_CONTAINER_IMAGE")
            or "wolf-universe:latest"
        )
        namespace = str(config.get("namespace") or os.environ.get("WOLF_K8S_NAMESPACE") or "default")
        context = config.get("context") or os.environ.get("WOLF_K8S_CONTEXT")
        python_bin = str(config.get("python_bin") or "python")
        container_port = int(config.get("container_port") or config.get("port") or 8000)
        local_host = str(config.get("local_host") or "127.0.0.1")
        local_port = int(config.get("local_port") or config.get("host_port") or 0)
        if local_port <= 0:
            local_port = _pick_free_local_port(local_host)
        replicas = int(config.get("replicas") or 1)
        startup_timeout = float(spec.startup_timeout_s or config.get("startup_timeout_s") or 60.0)
        poll_interval = float(config.get("poll_interval_s") or 0.5)

        runtime_root = Path(config.get("runtime_dir") or spec.runtime_dir or (Path(tempfile.gettempdir()) / "wolf_universes"))
        runtime_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_name = _safe_k8s_name(spec.name, prefix="wolf")
        suffix = timestamp.lower()
        app_name = str(config.get("name") or f"{safe_name}-{suffix}")[:63].strip("-")
        configmap_name = f"{app_name}-params"[:63].strip("-")
        service_name = f"{app_name}-svc"[:63].strip("-")
        deployment_name = app_name

        params_file = runtime_root / f"{app_name}.params.json"
        manifest_file = runtime_root / f"{app_name}.k8s.yaml"
        serializable = spec.params.model_dump(mode="json") if hasattr(spec.params, "model_dump") else spec.params.dict()
        params_text = json.dumps(serializable, indent=2)
        params_file.write_text(params_text, encoding="utf-8")

        manifest = self._build_manifest(
            app_name=app_name,
            namespace=namespace,
            deployment_name=deployment_name,
            service_name=service_name,
            configmap_name=configmap_name,
            image=image,
            python_bin=python_bin,
            container_port=container_port,
            params_text=params_text,
            cors=spec.cors if spec.cors is not None else ["*"],
            replicas=replicas,
            config=config,
            env={**(spec.env or {}), **{str(k): str(v) for k, v in (config.get("env") or {}).items()}},
        )
        manifest_file.write_text(manifest, encoding="utf-8")

        base = [kubectl_bin]
        if context:
            base.extend(["--context", str(context)])

        files = {"runtime_root": str(runtime_root), "params_file": str(params_file), "manifest_file": str(manifest_file)}
        metadata = {
            "backend": self.backend_name,
            "deployment_type": "kubernetes",
            "image": image,
            "namespace": namespace,
            "context": context,
            "deployment_name": deployment_name,
            "service_name": service_name,
            "configmap_name": configmap_name,
            "container_port": container_port,
            "local_host": local_host,
            "local_port": local_port,
            "manifest_file": str(manifest_file),
        }

        if _bool_config(config, "create_namespace", False) and namespace != "default":
            ns_result = subprocess.run([*base, "create", "namespace", namespace], capture_output=True, text=True, check=False)
            if ns_result.returncode != 0 and "AlreadyExists" not in (ns_result.stderr or ns_result.stdout or ""):
                return UniverseHandle(name=spec.name, backend=self.backend_name, state="failed", runtime_spec=spec, files=files, metadata={**metadata, "stderr": ns_result.stderr, "kubectl_returncode": ns_result.returncode})

        apply_result = subprocess.run([*base, "apply", "-f", str(manifest_file)], capture_output=True, text=True, check=False)
        if apply_result.returncode != 0:
            return UniverseHandle(name=spec.name, backend=self.backend_name, state="failed", runtime_spec=spec, files=files, metadata={**metadata, "stderr": apply_result.stderr, "kubectl_returncode": apply_result.returncode})

        rollout_result = subprocess.run([*base, "-n", namespace, "rollout", "status", f"deployment/{deployment_name}", f"--timeout={int(startup_timeout)}s"], capture_output=True, text=True, check=False)
        if rollout_result.returncode != 0:
            state = "rollout_failed"
            metadata.update({"rollout_stdout": rollout_result.stdout, "rollout_stderr": rollout_result.stderr, "kubectl_returncode": rollout_result.returncode})
        else:
            state = "starting"
            metadata.update({"rollout_stdout": rollout_result.stdout})

        port_forward_cmd = [*base, "-n", namespace, "port-forward", f"svc/{service_name}", f"{local_port}:{container_port}", "--address", local_host]
        stdout_file = runtime_root / f"{app_name}.port-forward.stdout.log"
        stderr_file = runtime_root / f"{app_name}.port-forward.stderr.log"
        stdout_handle = stdout_file.open("a", encoding="utf-8")
        stderr_handle = stderr_file.open("a", encoding="utf-8")
        try:
            port_forward_proc = subprocess.Popen(port_forward_cmd, stdout=stdout_handle, stderr=stderr_handle, text=True)
        except Exception as exc:
            stdout_handle.close()
            stderr_handle.close()
            return UniverseHandle(name=spec.name, backend=self.backend_name, state="port_forward_failed", runtime_spec=spec, files=files, logs={"stdout": str(stdout_file), "stderr": str(stderr_file)}, metadata={**metadata, "error": str(exc), "port_forward_command": port_forward_cmd})

        process_handle = {
            "kubectl_bin": kubectl_bin,
            "context": context,
            "namespace": namespace,
            "deployment_name": deployment_name,
            "service_name": service_name,
            "configmap_name": configmap_name,
            "port_forward_pid": getattr(port_forward_proc, "pid", None),
            "port_forward_command": port_forward_cmd,
        }
        handle = UniverseHandle(
            name=spec.name,
            backend=self.backend_name,
            state=state,
            runtime_spec=spec,
            process=process_handle,
            files=files,
            logs={"stdout": str(stdout_file), "stderr": str(stderr_file)},
            metadata={**metadata, "port_forward_command": port_forward_cmd, "port_forward_pid": getattr(port_forward_proc, "pid", None)},
        )
        handle.process["port_forward_process"] = port_forward_proc
        handle.process["stdout_handle"] = stdout_handle
        handle.process["stderr_handle"] = stderr_handle

        elapsed = 0.0
        while elapsed < min(startup_timeout, float(config.get("port_forward_wait_s") or 10.0)):
            if port_forward_proc.poll() is not None:
                handle.state = "port_forward_exited"
                break
            if self._tcp_connectable(local_host, local_port, timeout=0.2):
                handle.endpoint = UniverseEndpoint(host=local_host, port=local_port)
                handle.state = "ready" if state != "rollout_failed" else "rollout_failed"
                break
            time.sleep(poll_interval)
            elapsed += poll_interval
        if handle.endpoint is None and handle.state not in {"rollout_failed", "port_forward_exited"}:
            handle.endpoint = UniverseEndpoint(host=local_host, port=local_port)
            handle.state = "port_forward_pending"
        if handle.endpoint:
            handle.metadata.update({"host": handle.endpoint.host, "port": handle.endpoint.port, "url": handle.endpoint.url})
        handle.metadata["startup_elapsed_s"] = elapsed
        return handle

    def _build_manifest(self, **kwargs: Any) -> str:
        app_name = kwargs["app_name"]
        namespace = kwargs["namespace"]
        deployment_name = kwargs["deployment_name"]
        service_name = kwargs["service_name"]
        configmap_name = kwargs["configmap_name"]
        image = kwargs["image"]
        python_bin = kwargs["python_bin"]
        container_port = int(kwargs["container_port"])
        params_text = kwargs["params_text"]
        cors = kwargs.get("cors") or ["*"]
        replicas = int(kwargs.get("replicas") or 1)
        config = kwargs.get("config") or {}
        env = kwargs.get("env") or {}
        image_pull_policy = str(config.get("image_pull_policy") or "IfNotPresent")
        resources = config.get("resources") or {}
        service_type = str(config.get("service_type") or "ClusterIP")

        command = [python_bin, "-m", "framework.universes.run_universe", "--params-file", "/wolf/config/params.json", "--status-file", "/wolf/runtime/status.json", "--host", "0.0.0.0", "--port", str(container_port)]
        if cors:
            command.extend(["--cors", *[str(c) for c in cors]])

        manifest = {
            "apiVersion": "v1",
            "kind": "List",
            "items": [
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": configmap_name, "namespace": namespace, "labels": {"app.kubernetes.io/name": app_name, "app.kubernetes.io/managed-by": "wolf"}},
                    "data": {"params.json": params_text},
                },
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": deployment_name, "namespace": namespace, "labels": {"app.kubernetes.io/name": app_name, "app.kubernetes.io/managed-by": "wolf"}},
                    "spec": {
                        "replicas": replicas,
                        "selector": {"matchLabels": {"app.kubernetes.io/name": app_name}},
                        "template": {
                            "metadata": {"labels": {"app.kubernetes.io/name": app_name, "app.kubernetes.io/managed-by": "wolf"}},
                            "spec": {
                                "containers": [
                                    {
                                        "name": "universe",
                                        "image": image,
                                        "imagePullPolicy": image_pull_policy,
                                        "command": command,
                                        "ports": [{"containerPort": container_port, "name": "http"}],
                                        "env": [{"name": str(k), "value": str(v)} for k, v in env.items()],
                                        "volumeMounts": [
                                            {"name": "params", "mountPath": "/wolf/config", "readOnly": True},
                                            {"name": "runtime", "mountPath": "/wolf/runtime"},
                                        ],
                                    }
                                ],
                                "volumes": [
                                    {"name": "params", "configMap": {"name": configmap_name}},
                                    {"name": "runtime", "emptyDir": {}},
                                ],
                            },
                        },
                    },
                },
                {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": service_name, "namespace": namespace, "labels": {"app.kubernetes.io/name": app_name, "app.kubernetes.io/managed-by": "wolf"}},
                    "spec": {"type": service_type, "selector": {"app.kubernetes.io/name": app_name}, "ports": [{"name": "http", "port": container_port, "targetPort": container_port}]},
                },
            ],
        }
        if resources:
            manifest["items"][1]["spec"]["template"]["spec"]["containers"][0]["resources"] = resources
        try:
            import yaml  # type: ignore
            return yaml.safe_dump(manifest, sort_keys=False)
        except Exception:
            return json.dumps(manifest, indent=2)

    @staticmethod
    def _tcp_connectable(host: str, port: int, timeout: float = 0.2) -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except Exception:
            return False

    def _kubectl_base(self, handle: UniverseHandle) -> list[str]:
        process = handle.process if isinstance(handle.process, dict) else {}
        kubectl_bin = str(process.get("kubectl_bin") or handle.metadata.get("kubectl_bin") or "kubectl")
        base = [kubectl_bin]
        context = process.get("context") or handle.metadata.get("context")
        if context:
            base.extend(["--context", str(context)])
        return base

    def status(self, handle: UniverseHandle) -> UniverseStatus:
        process = handle.process if isinstance(handle.process, dict) else {}
        namespace = str(process.get("namespace") or handle.metadata.get("namespace") or "default")
        deployment = str(process.get("deployment_name") or handle.metadata.get("deployment_name") or handle.name)
        result = subprocess.run([*self._kubectl_base(handle), "-n", namespace, "get", "deployment", deployment, "-o", "json"], capture_output=True, text=True, check=False)
        state = handle.state
        error = None
        metadata = dict(handle.metadata or {})
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout or "{}")
                status = data.get("status", {})
                ready = int(status.get("readyReplicas") or 0)
                desired = int(status.get("replicas") or 0)
                metadata.update({"ready_replicas": ready, "replicas": desired})
                if desired > 0 and ready >= desired:
                    state = "ready" if handle.endpoint and handle.endpoint.usable else "running"
                else:
                    state = "starting"
            except Exception as exc:
                error = str(exc)
        else:
            state = "not_found"
            error = result.stderr or result.stdout
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=state, endpoint=handle.endpoint, metadata=metadata, error=error)

    def logs(self, handle: UniverseHandle, tail: int = 200) -> Dict[str, str]:
        process = handle.process if isinstance(handle.process, dict) else {}
        namespace = str(process.get("namespace") or handle.metadata.get("namespace") or "default")
        deployment = str(process.get("deployment_name") or handle.metadata.get("deployment_name") or handle.name)
        result = subprocess.run([*self._kubectl_base(handle), "-n", namespace, "logs", f"deployment/{deployment}", "--tail", str(max(1, int(tail)))], capture_output=True, text=True, check=False)
        pf_logs = super().logs(handle, tail=tail)
        return {"stdout": result.stdout or "", "stderr": result.stderr or "", "port_forward_stdout": pf_logs.get("stdout", ""), "port_forward_stderr": pf_logs.get("stderr", "")}

    def terminate(self, handle: UniverseHandle, force: bool = False, timeout: float = 10.0) -> UniverseStatus:
        process = handle.process if isinstance(handle.process, dict) else {}
        pf = process.get("port_forward_process") if isinstance(process, dict) else None
        if pf is not None:
            try:
                if force and hasattr(pf, "kill"):
                    pf.kill()
                elif hasattr(pf, "terminate"):
                    pf.terminate()
                if hasattr(pf, "wait"):
                    pf.wait(timeout=timeout)
            except Exception:
                try:
                    if hasattr(pf, "kill"):
                        pf.kill()
                except Exception:
                    pass
        for key in ("stdout_handle", "stderr_handle"):
            h = process.get(key) if isinstance(process, dict) else None
            try:
                if h is not None:
                    h.close()
            except Exception:
                pass

        namespace = str(process.get("namespace") or handle.metadata.get("namespace") or "default")
        manifest_file = (handle.files or {}).get("manifest_file") or handle.metadata.get("manifest_file")
        cmd = [*self._kubectl_base(handle), "-n", namespace, "delete"]
        if manifest_file:
            cmd.extend(["-f", str(manifest_file), "--ignore-not-found=true"])
        else:
            deployment = str(process.get("deployment_name") or handle.metadata.get("deployment_name") or handle.name)
            service = str(process.get("service_name") or handle.metadata.get("service_name") or handle.name)
            configmap = str(process.get("configmap_name") or handle.metadata.get("configmap_name") or handle.name)
            cmd.extend(["deployment", deployment, "service", service, "configmap", configmap, "--ignore-not-found=true"])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        handle.state = "terminated" if result.returncode == 0 else "terminate_failed"
        handle.metadata["terminate_returncode"] = result.returncode
        if result.stderr:
            handle.metadata["terminate_stderr"] = result.stderr
        return UniverseStatus(name=handle.name, backend=self.backend_name, state=handle.state, endpoint=handle.endpoint, metadata=dict(handle.metadata or {}), error=result.stderr if result.returncode else None)

    def cleanup(self, handle: UniverseHandle) -> list[str]:
        removed: list[str] = []
        for key, path in (handle.files or {}).items():
            if key.endswith("_file"):
                try:
                    Path(path).unlink(missing_ok=True)
                    removed.append(key)
                except Exception:
                    pass
        for key, path in (handle.logs or {}).items():
            try:
                Path(path).unlink(missing_ok=True)
                removed.append(f"log_{key}")
            except Exception:
                pass
        return removed
