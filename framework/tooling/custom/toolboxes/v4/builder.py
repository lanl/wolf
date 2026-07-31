from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from typing import Dict, Optional

from .registry import ToolboxRegistries
from .tool_models import BuildPlan, LifecycleStatus, PackageKind, ToolPackage, ToolSource


class BuildBackend(ABC):
    name: str = 'base'

    @abstractmethod
    def plan_build(self, source: ToolSource, tool_id: str | None = None, **kwargs) -> BuildPlan:
        raise NotImplementedError

    @abstractmethod
    def build(self, source: ToolSource, tool_id: str | None = None, dry_run: bool = True, **kwargs) -> ToolPackage:
        raise NotImplementedError


class SourceBuildBackend(BuildBackend):
    name = 'source'

    def plan_build(self, source: ToolSource, tool_id: str | None = None, **kwargs) -> BuildPlan:
        return BuildPlan(
            source_id=source.id,
            tool_id=tool_id,
            backend=self.name,
            package_kind=PackageKind.SOURCE,
            dry_run=True,
            explanation='No-op source package backend; records source as package without building.',
        )

    def build(self, source: ToolSource, tool_id: str | None = None, dry_run: bool = True, **kwargs) -> ToolPackage:
        return ToolPackage(
            tool_id=tool_id,
            source_id=source.id,
            kind=PackageKind.SOURCE,
            uri=source.uri,
            status=LifecycleStatus.PACKAGED,
            metadata={'backend': self.name, 'dry_run': dry_run, 'note': 'No-op source package.'},
        )


class DockerBuildBackend(BuildBackend):
    name = 'docker'

    def _cmd(self):
        return shutil.which('docker')

    def plan_build(self, source: ToolSource, tool_id: str | None = None, image_tag: Optional[str] = None, context_dir: Optional[str] = None, dockerfile_path: Optional[str] = None, **kwargs) -> BuildPlan:
        warnings = []
        docker = self._cmd()
        if not docker:
            warnings.append('docker command not found on PATH; plan is not executable in current environment.')
        cmd = [docker or 'docker', 'build']
        if image_tag:
            cmd += ['-t', image_tag]
        if dockerfile_path:
            cmd += ['-f', dockerfile_path]
        cmd.append(context_dir or source.uri or '.')
        return BuildPlan(
            source_id=source.id,
            tool_id=tool_id,
            backend=self.name,
            package_kind=PackageKind.DOCKER_IMAGE,
            dockerfile_path=dockerfile_path,
            context_dir=context_dir or source.uri,
            image_tag=image_tag,
            command=cmd,
            dry_run=True,
            warnings=warnings,
            explanation='Docker build dry-run plan. Real build is intentionally gated behind dry_run=False.',
        )

    def build(self, source: ToolSource, tool_id: str | None = None, dry_run: bool = True, **kwargs) -> ToolPackage:
        plan = self.plan_build(source, tool_id=tool_id, **kwargs)
        status = LifecycleStatus.PACKAGED if dry_run else LifecycleStatus.BUILDING
        return ToolPackage(
            tool_id=tool_id,
            source_id=source.id,
            kind=PackageKind.DOCKER_IMAGE,
            uri=plan.image_tag,
            build_backend=self.name,
            build_log='Dry-run Docker build plan created.' if dry_run else 'Real Docker build not implemented yet.',
            status=status,
            metadata={'build_plan': plan.model_dump(mode='json') if hasattr(plan, 'model_dump') else plan.dict()},
        )


class PodmanBuildBackend(DockerBuildBackend):
    name = 'podman'

    def _cmd(self):
        return shutil.which('podman')


class ToolBuilder:
    """Tool build facade with pluggable backends.

    Current state: source/no-op packaging is functional; Docker/Podman produce
    inspectable dry-run plans and package records but do not run real builds yet.
    """

    def __init__(self, registries: ToolboxRegistries):
        self.registries = registries
        self.backends: Dict[str, BuildBackend] = {
            'source': SourceBuildBackend(),
            'noop': SourceBuildBackend(),
            'docker': DockerBuildBackend(),
            'podman': PodmanBuildBackend(),
        }

    def get_backend(self, backend: str) -> BuildBackend:
        if backend not in self.backends:
            raise KeyError(f'Unknown build backend: {backend}')
        return self.backends[backend]

    def plan_build(self, source: ToolSource, tool_id: str | None = None, backend: str = 'source', **kwargs) -> BuildPlan:
        return self.get_backend(backend).plan_build(source, tool_id=tool_id, **kwargs)

    def build_source(self, source: ToolSource, tool_id: str | None = None, backend: str = 'source', dry_run: bool = True, **kwargs) -> ToolPackage:
        pkg = self.get_backend(backend).build(source, tool_id=tool_id, dry_run=dry_run, **kwargs)
        self.registries.packages.upsert(pkg)
        return pkg

    def package_source(self, source: ToolSource, tool_id: str | None = None) -> ToolPackage:
        return self.build_source(source, tool_id=tool_id, backend='source', dry_run=True)
