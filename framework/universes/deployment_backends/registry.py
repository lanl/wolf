from __future__ import annotations

from typing import Dict

from framework.universes.deployment_backends.base import UniverseBackend

_BACKENDS: Dict[str, UniverseBackend] = {}


def register_universe_backend(backend: UniverseBackend) -> UniverseBackend:
    _BACKENDS[backend.backend_name] = backend
    return backend


def get_universe_backend(name: str) -> UniverseBackend:
    _ensure_defaults()
    key = str(name or "local_process")
    if key == "local":
        key = "local_process"
    if key == "remote":
        key = "ssh_process"
    try:
        return _BACKENDS[key]
    except KeyError as exc:
        raise KeyError(f"Unknown Universe deployment backend: {name!r}. Available: {sorted(_BACKENDS)}") from exc


def list_universe_backends() -> list[str]:
    _ensure_defaults()
    return sorted(_BACKENDS)


def _ensure_defaults() -> None:
    if "local_process" not in _BACKENDS:
        from framework.universes.deployment_backends.local_process import LocalProcessUniverseBackend
        register_universe_backend(LocalProcessUniverseBackend())
    if "ssh_process" not in _BACKENDS:
        from framework.universes.deployment_backends.ssh_process import SSHProcessUniverseBackend
        register_universe_backend(SSHProcessUniverseBackend())
    if "podman" not in _BACKENDS:
        from framework.universes.deployment_backends.podman import PodmanUniverseBackend
        register_universe_backend(PodmanUniverseBackend())
    if "docker" not in _BACKENDS:
        from framework.universes.deployment_backends.docker import DockerUniverseBackend
        register_universe_backend(DockerUniverseBackend())
    if "kubernetes" not in _BACKENDS:
        from framework.universes.deployment_backends.kubernetes import KubernetesUniverseBackend
        register_universe_backend(KubernetesUniverseBackend())
    if "screen" not in _BACKENDS:
        from framework.universes.deployment_backends.tmux_backend import ScreenUniverseBackend
        register_universe_backend(ScreenUniverseBackend())
    if "tmux" not in _BACKENDS:
        from framework.universes.deployment_backends.tmux_backend import TmuxUniverseBackend
        register_universe_backend(TmuxUniverseBackend())
    if "ray" not in _BACKENDS:
        from framework.universes.deployment_backends.ray_backend import RayUniverseBackend
        register_universe_backend(RayUniverseBackend())
    if "slurm" not in _BACKENDS:
        from framework.universes.deployment_backends.slurm import SlurmUniverseBackend
        register_universe_backend(SlurmUniverseBackend())
    if "sandbox_bubblewrap" not in _BACKENDS:
        from framework.universes.deployment_backends.sandbox_bubblewrap import BubblewrapUniverseBackend
        register_universe_backend(BubblewrapUniverseBackend())
    if "sandbox_nsjail" not in _BACKENDS:
        from framework.universes.deployment_backends.sandbox_bubblewrap import NsjailUniverseBackend
        register_universe_backend(NsjailUniverseBackend())
    if "vm_qemu" not in _BACKENDS:
        from framework.universes.deployment_backends.vm_microvm import QemuUniverseBackend
        register_universe_backend(QemuUniverseBackend())
    if "microvm_firecracker" not in _BACKENDS:
        from framework.universes.deployment_backends.vm_microvm import FirecrackerUniverseBackend
        register_universe_backend(FirecrackerUniverseBackend())
