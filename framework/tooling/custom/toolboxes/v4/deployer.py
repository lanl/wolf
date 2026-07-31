from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from typing import Dict, Optional

from .registry import ToolboxRegistries
from .tool_models import DeployPlan, DeploymentKind, LifecycleStatus, ToolDeployment, ToolPackage


class DeploymentBackend(ABC):
    name: str = 'base'

    @abstractmethod
    def plan_deploy(self, package: ToolPackage, tool_id: str | None = None, **kwargs) -> DeployPlan:
        raise NotImplementedError


class LocalDeploymentBackend(DeploymentBackend):
    name = 'local'

    def plan_deploy(self, package: ToolPackage, tool_id: str | None = None, **kwargs) -> DeployPlan:
        return DeployPlan(
            tool_id=tool_id or package.tool_id,
            package_id=package.id,
            backend=self.name,
            deployment_kind=DeploymentKind.LOCAL_SUBPROCESS,
            dry_run=True,
            explanation='Local deployment plan placeholder. For local CLI tools, endpoint registration is usually sufficient.',
        )


class DockerDeploymentBackend(DeploymentBackend):
    name = 'docker'

    def _cmd(self):
        return shutil.which('docker')

    def plan_deploy(self, package: ToolPackage, tool_id: str | None = None, image_tag: Optional[str] = None, container_name: Optional[str] = None, **kwargs) -> DeployPlan:
        warnings = []
        docker = self._cmd()
        if not docker:
            warnings.append('docker command not found on PATH; deployment plan is not executable in current environment.')
        image = image_tag or package.uri
        cmd = [docker or 'docker', 'run', '--rm']
        if container_name:
            cmd += ['--name', container_name]
        cmd.append(image or '<image>')
        return DeployPlan(
            tool_id=tool_id or package.tool_id,
            package_id=package.id,
            backend=self.name,
            deployment_kind=DeploymentKind.DOCKER_CONTAINER,
            image_tag=image,
            command=cmd,
            dry_run=True,
            warnings=warnings,
            explanation='Docker deployment dry-run plan. Real container launch is intentionally gated for review.',
        )


class PodmanDeploymentBackend(DockerDeploymentBackend):
    name = 'podman'

    def _cmd(self):
        return shutil.which('podman')


class ToolDeployer:
    """Tool deployment facade with pluggable backend plans."""

    def __init__(self, registries: ToolboxRegistries):
        self.registries = registries
        self.backends: Dict[str, DeploymentBackend] = {
            'local': LocalDeploymentBackend(),
            'docker': DockerDeploymentBackend(),
            'podman': PodmanDeploymentBackend(),
        }

    def get_backend(self, backend: str) -> DeploymentBackend:
        if backend not in self.backends:
            raise KeyError(f'Unknown deployment backend: {backend}')
        return self.backends[backend]

    def plan_deploy(self, package: ToolPackage, tool_id: str | None = None, backend: str = 'local', **kwargs) -> DeployPlan:
        return self.get_backend(backend).plan_deploy(package, tool_id=tool_id, **kwargs)

    def register_deployment(self, deployment: ToolDeployment) -> ToolDeployment:
        if deployment.status == LifecycleStatus.REGISTERED:
            deployment.status = LifecycleStatus.DEPLOYED
        self.registries.deployments.upsert(deployment)
        return deployment
