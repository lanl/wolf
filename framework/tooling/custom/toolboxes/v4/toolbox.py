from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .builder import ToolBuilder
from .deployer import ToolDeployer
from .diagnostics import ToolDiagnostics
from .planner import ToolExecutionPlanner
from .registry import ToolboxRegistries, model_to_dict
from .runtime import ToolRuntimeExecutor
from .security import ToolSecurityPolicyEngine
from .locality import LocalityResolver
from .tool_models import (
    ArtifactRef,
    DeploymentKind,
    EndpointProtocol,
    ExecutionPlan,
    ExecutionPolicy,
    LifecycleStatus,
    LocalityRef,
    PackageKind,
    ReadinessReport,
    RunStatus,
    SmokeTestCase,
    SmokeTestExpectation,
    SmokeTestResult,
    ReadinessCheck,
    SourceKind,
    ToolDeployment,
    ToolEndpoint,
    ToolPackage,
    ToolRun,
    ToolSource,
    ToolSpec,
    ToolboxV4Params,
    utc_now,
)


class ToolBoxV4:
    """Locality-aware capability lifecycle manager for WOLF tools.

    This MVP is registry-backed and intentionally conservative. It supports
    source/spec/deployment/endpoint registration, basic text search, readiness
    reporting, locality-aware planning, and local CLI/Python execution through
    explicit execution plans.
    """

    implementation_name = 'toolbox_v4'
    implementation_version = '0.1.0-mvp'

    def __init__(self, params: ToolboxV4Params | Dict[str, Any] | None = None):
        if params is None:
            params = ToolboxV4Params()
        elif isinstance(params, dict):
            params = ToolboxV4Params(**params)
        self.params = params
        self.name = params.name
        self.root_dir = Path(params.root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        registry_dir = Path(params.registry_dir) if params.registry_dir else self.root_dir / 'registries'
        self.registries = ToolboxRegistries(registry_dir)

        self.builder = ToolBuilder(self.registries)
        self.deployer = ToolDeployer(self.registries)
        self.planner = ToolExecutionPlanner(self.registries)
        self.runtime = ToolRuntimeExecutor(self.registries)
        self.diagnostics = ToolDiagnostics(self.registries)
        self.security = ToolSecurityPolicyEngine()
        self.locality_resolver = LocalityResolver()

    # ------------------------------------------------------------------
    # Registration APIs
    # ------------------------------------------------------------------
    def register_source(
        self,
        name: str,
        kind: SourceKind | str = SourceKind.UNKNOWN,
        uri: Optional[str] = None,
        body: Optional[str] = None,
        entrypoint: Optional[str] = None,
        description: str = '',
        metadata: Optional[Dict[str, Any]] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> ToolSource:
        src = ToolSource(
            name=name,
            kind=kind,
            uri=uri,
            body=body,
            entrypoint=entrypoint,
            description=description,
            metadata=metadata or {},
            provenance=provenance or {},
        )
        return self.registries.sources.upsert(src)

    def register_tool_spec(
        self,
        name: str,
        version: str = '0.1.0',
        description: str = '',
        capabilities: Optional[List[str]] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        source_refs: Optional[List[str]] = None,
        smoke_tests: Optional[List[SmokeTestCase | Dict[str, Any]]] = None,
        status: LifecycleStatus | str = LifecycleStatus.REGISTERED,
        metadata: Optional[Dict[str, Any]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        **extra_fields: Any,
    ) -> ToolSpec:
        payload = dict(
            name=name,
            version=version,
            description=description,
            capabilities=capabilities or [],
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            source_refs=source_refs or [],
            smoke_tests=[t if isinstance(t, SmokeTestCase) else SmokeTestCase(**t) for t in (smoke_tests or [])],
            status=status,
            metadata=metadata or {},
            provenance=provenance or {},
        )
        payload.update(extra_fields)
        spec = ToolSpec(**payload)
        return self.registries.tools.upsert(spec)

    def register_package(
        self,
        tool_id: Optional[str] = None,
        source_id: Optional[str] = None,
        kind: PackageKind | str = PackageKind.SOURCE,
        uri: Optional[str] = None,
        status: LifecycleStatus | str = LifecycleStatus.PACKAGED,
        metadata: Optional[Dict[str, Any]] = None,
        **extra_fields: Any,
    ) -> ToolPackage:
        pkg = ToolPackage(
            tool_id=tool_id,
            source_id=source_id,
            kind=kind,
            uri=uri,
            status=status,
            metadata=metadata or {},
            **extra_fields,
        )
        self.registries.packages.upsert(pkg)
        if tool_id:
            self._link(tool_id, 'package_refs', pkg.id)
        return pkg

    def register_deployment(
        self,
        tool_id: str,
        name: str,
        kind: DeploymentKind | str = DeploymentKind.LOCAL_SUBPROCESS,
        locality: Optional[LocalityRef] = None,
        package_id: Optional[str] = None,
        status: LifecycleStatus | str = LifecycleStatus.DEPLOYED,
        metadata: Optional[Dict[str, Any]] = None,
        **extra_fields: Any,
    ) -> ToolDeployment:
        deployment = ToolDeployment(
            tool_id=tool_id,
            package_id=package_id,
            name=name,
            kind=kind,
            locality=locality or self.params.default_locality,
            status=status,
            metadata=metadata or {},
            **extra_fields,
        )
        deployment = self.deployer.register_deployment(deployment)
        self._link(tool_id, 'deployment_refs', deployment.id)
        return deployment

    def register_endpoint(
        self,
        tool_id: str,
        protocol: EndpointProtocol | str,
        deployment_id: Optional[str] = None,
        uri: Optional[str] = None,
        entrypoint: Optional[str] = None,
        invocation: Optional[Dict[str, Any]] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        status: LifecycleStatus | str = LifecycleStatus.REGISTERED,
        metadata: Optional[Dict[str, Any]] = None,
        **extra_fields: Any,
    ) -> ToolEndpoint:
        endpoint = ToolEndpoint(
            tool_id=tool_id,
            deployment_id=deployment_id,
            protocol=protocol,
            uri=uri,
            entrypoint=entrypoint,
            invocation=invocation or {},
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            status=status,
            metadata=metadata or {},
            **extra_fields,
        )
        self.registries.endpoints.upsert(endpoint)
        self._link(tool_id, 'endpoint_refs', endpoint.id)
        if deployment_id:
            deployment = self.registries.deployments.require(deployment_id)
            if endpoint.id not in deployment.endpoint_ids:
                deployment.endpoint_ids.append(endpoint.id)
                deployment.updated_at = utc_now()
                self.registries.deployments.upsert(deployment)
        return endpoint

    def register_local_cli_tool(
        self,
        name: str,
        entrypoint: str,
        args_schema: Optional[Dict[str, Any]] = None,
        description: str = '',
        capabilities: Optional[List[str]] = None,
        command_prefix: Optional[Sequence[str] | str] = None,
        version: str = '0.1.0',
        source_uri: Optional[str] = None,
        smoke_tests: Optional[List[SmokeTestCase | Dict[str, Any]]] = None,
        mark_ready: bool = False,
    ) -> ToolSpec:
        """Convenience helper for the MVP: register a local CLI/subprocess tool."""
        source = self.register_source(
            name=name,
            kind=SourceKind.LOCAL_FILE,
            uri=source_uri or entrypoint,
            entrypoint=entrypoint,
            description=description,
        )
        spec = self.register_tool_spec(
            name=name,
            version=version,
            description=description,
            capabilities=capabilities or [],
            input_schema=args_schema or {},
            source_refs=[source.id],
            smoke_tests=smoke_tests,
            status=LifecycleStatus.REGISTERED,
        )
        pkg = self.builder.package_source(source, tool_id=spec.id)
        self._link(spec.id, 'package_refs', pkg.id)
        deployment = self.register_deployment(
            tool_id=spec.id,
            name=f'{name}_local',
            kind=DeploymentKind.LOCAL_SUBPROCESS,
            package_id=pkg.id,
            status=LifecycleStatus.DEPLOYED,
        )
        self.register_endpoint(
            tool_id=spec.id,
            deployment_id=deployment.id,
            protocol=EndpointProtocol.CLI,
            entrypoint=entrypoint,
            invocation={'command_prefix': command_prefix or []},
            input_schema=args_schema or {},
            status=LifecycleStatus.READY if mark_ready else LifecycleStatus.REGISTERED,
        )
        if mark_ready:
            self.mark_ready(spec.id)
        return self.registries.tools.require(spec.id)

    # ------------------------------------------------------------------
    # Discovery APIs
    # ------------------------------------------------------------------
    def get_tool_spec(self, tool_id: str) -> ToolSpec:
        return self.registries.tools.require(tool_id)

    def get_tool_by_name(self, name: str, version: Optional[str] = None) -> Optional[ToolSpec]:
        return self.registries.tools.get_by_name(name, version=version)

    def list_tools(self, include_archived: bool = False) -> List[Dict[str, Any]]:
        tools = self.registries.tools.list()
        if not include_archived:
            tools = [t for t in tools if t.status not in {LifecycleStatus.ARCHIVED, 'archived'}]
        return [self._tool_summary(t) for t in tools]

    def search_tools(self, query: str, k: int = 10) -> List[Dict[str, Any]]:
        hits = self.registries.tools.search_text(query)[:k]
        return [self._tool_summary(t) for t in hits]

    def tool_info(self, tool_id_or_name: str) -> Optional[Dict[str, Any]]:
        tool = self.registries.tools.get(tool_id_or_name) or self.registries.tools.get_by_name(tool_id_or_name)
        if tool is None:
            return None
        return {
            'tool': model_to_dict(tool),
            'sources': [model_to_dict(s) for s in [self.registries.sources.get(i) for i in tool.source_refs] if s],
            'packages': [model_to_dict(p) for p in [self.registries.packages.get(i) for i in tool.package_refs] if p],
            'deployments': [model_to_dict(d) for d in [self.registries.deployments.get(i) for i in tool.deployment_refs] if d],
            'endpoints': [model_to_dict(e) for e in [self.registries.endpoints.get(i) for i in tool.endpoint_refs] if e],
        }

    # ------------------------------------------------------------------
    # Planning/execution/readiness APIs
    # ------------------------------------------------------------------
    def plan_execution(
        self,
        tool_id_or_name: str,
        inputs: Optional[Dict[str, ArtifactRef | Dict[str, Any]]] = None,
        outputs: Optional[Dict[str, ArtifactRef | Dict[str, Any]]] = None,
        policy: Optional[ExecutionPolicy | Dict[str, Any]] = None,
    ) -> ExecutionPlan:
        tool = self._resolve_tool(tool_id_or_name)
        input_refs = self._coerce_artifact_map(inputs or {})
        output_refs = self._coerce_artifact_map(outputs or {})
        exec_policy = policy if isinstance(policy, ExecutionPolicy) else ExecutionPolicy(**(policy or {}))
        allowed, reasons = self.security.check_execution_allowed(tool.safety_policy, exec_policy)
        plan = self.planner.plan(tool, inputs=input_refs, outputs=output_refs, policy=exec_policy)
        if not allowed:
            plan.risks.extend(reasons)
            plan.warnings.extend(reasons)
        return plan

    def execute_tool(
        self,
        tool_id_or_name: str,
        inputs: Optional[Dict[str, ArtifactRef | Dict[str, Any]]] = None,
        outputs: Optional[Dict[str, ArtifactRef | Dict[str, Any]]] = None,
        policy: Optional[ExecutionPolicy | Dict[str, Any]] = None,
        args: Optional[Sequence[str]] = None,
        fn_args: Optional[Sequence[Any]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        input_data: Optional[str | bytes] = None,
        text: bool = True,
    ) -> ToolRun:
        plan = self.plan_execution(tool_id_or_name, inputs=inputs, outputs=outputs, policy=policy)
        if plan.selected_endpoint_id is None:
            run = ToolRun(
                plan_id=plan.id,
                tool_id=plan.tool_id,
                deployment_id=plan.selected_deployment_id,
                endpoint_id=None,
                inputs=plan.input_bindings,
                outputs=plan.output_bindings,
                status=RunStatus.FAILED,
                started_at=utc_now(),
                ended_at=utc_now(),
                returncode=-1,
                error='No endpoint available for execution plan',
                stderr='No endpoint available for execution plan',
            )
            return self.registries.runs.upsert(run)
        return self.runtime.execute_plan(
            plan,
            args=args,
            fn_args=fn_args,
            kwargs=kwargs,
            env=env,
            cwd=cwd,
            timeout=timeout if timeout is not None else plan.policy.timeout,
            input_data=input_data,
            text=text,
        )

    def test_tool(self, tool_id_or_name: str, run_smoke_tests: bool = True) -> ReadinessReport:
        tool = self._resolve_tool(tool_id_or_name)
        deployment = self.registries.deployments.get(tool.deployment_refs[0]) if tool.deployment_refs else None
        endpoint = None
        if deployment and deployment.endpoint_ids:
            endpoint = self.registries.endpoints.get(deployment.endpoint_ids[0])
        elif tool.endpoint_refs:
            endpoint = self.registries.endpoints.get(tool.endpoint_refs[0])
        report = self.diagnostics.readiness_report(tool, deployment=deployment, endpoint=endpoint)

        if run_smoke_tests and tool.smoke_tests:
            smoke_results = self.run_smoke_tests(tool.id)
            for result in smoke_results:
                report.checks.append(ReadinessCheck(
                    name=f'smoke_test:{result.name}',
                    passed=result.passed,
                    message=result.message,
                    details=result.details,
                ))
            report.passed = report.passed and all(r.passed for r in smoke_results)
            report.status = LifecycleStatus.READY if report.passed else LifecycleStatus.TEST_FAILED
            report.summary = 'Ready for execution; structural and smoke tests passed.' if report.passed else 'Tool is not ready; one or more smoke tests failed.'
            self.registries.readiness.upsert(report)
        return report

    def run_smoke_tests(self, tool_id_or_name: str) -> List[SmokeTestResult]:
        tool = self._resolve_tool(tool_id_or_name)
        results: List[SmokeTestResult] = []
        for case in tool.smoke_tests:
            run = self.execute_tool(
                tool.id,
                args=case.args,
                fn_args=case.fn_args,
                kwargs=case.kwargs,
                timeout=case.timeout,
                input_data=case.input_data,
            )
            passed, message, details = self._evaluate_smoke_expectation(case, run)
            results.append(SmokeTestResult(
                test_id=case.id,
                name=case.name,
                passed=passed,
                run_id=run.id,
                message=message,
                details=details,
            ))
        return results

    def _evaluate_smoke_expectation(self, case: SmokeTestCase, run: ToolRun) -> tuple[bool, str, Dict[str, Any]]:
        exp = case.expectation
        failures: List[str] = []
        if exp.require_no_error and run.error:
            failures.append(f'Expected no error, got: {run.error}')
        if exp.returncode is not None and run.returncode != exp.returncode:
            failures.append(f'Expected returncode {exp.returncode}, got {run.returncode}')
        if exp.stdout_equals is not None and run.stdout != exp.stdout_equals:
            failures.append('stdout did not equal expected value')
        if exp.stdout_contains is not None and exp.stdout_contains not in (run.stdout or ''):
            failures.append(f'stdout did not contain {exp.stdout_contains!r}')
        if exp.stderr_equals is not None and run.stderr != exp.stderr_equals:
            failures.append('stderr did not equal expected value')
        if exp.stderr_contains is not None and exp.stderr_contains not in (run.stderr or ''):
            failures.append(f'stderr did not contain {exp.stderr_contains!r}')
        if exp.result_equals is not None and run.result != exp.result_equals:
            failures.append('result did not equal expected value')
        passed = not failures
        details = {
            'run_id': run.id,
            'returncode': run.returncode,
            'stdout': run.stdout,
            'stderr': run.stderr,
            'result': run.result,
            'error': run.error,
            'failures': failures,
        }
        return passed, ('Smoke test passed.' if passed else '; '.join(failures)), details

    def mark_ready(self, tool_id_or_name: str) -> ReadinessReport:
        tool = self._resolve_tool(tool_id_or_name)
        report = self.test_tool(tool.id, run_smoke_tests=True)
        if report.passed:
            tool.status = LifecycleStatus.READY
            tool.updated_at = utc_now()
            self.registries.tools.upsert(tool)
            if report.deployment_id:
                dep = self.registries.deployments.get(report.deployment_id)
                if dep:
                    dep.status = LifecycleStatus.READY
                    dep.updated_at = utc_now()
                    self.registries.deployments.upsert(dep)
            if report.endpoint_id:
                ep = self.registries.endpoints.get(report.endpoint_id)
                if ep:
                    ep.status = LifecycleStatus.READY
                    ep.updated_at = utc_now()
                    self.registries.endpoints.upsert(ep)
        return report

    def update_status(self, tool_id_or_name: str, status: LifecycleStatus | str) -> ToolSpec:
        tool = self._resolve_tool(tool_id_or_name)
        tool.status = status
        tool.updated_at = utc_now()
        return self.registries.tools.upsert(tool)

    def deprecate_tool(self, tool_id_or_name: str) -> ToolSpec:
        return self.update_status(tool_id_or_name, LifecycleStatus.DEPRECATED)

    def archive_tool(self, tool_id_or_name: str) -> ToolSpec:
        return self.update_status(tool_id_or_name, LifecycleStatus.ARCHIVED)

    # ------------------------------------------------------------------
    # Build/deploy/locality planning APIs
    # ------------------------------------------------------------------
    def classify_artifact_uri(self, uri: str, kind: str = 'unknown', media_type: Optional[str] = None) -> ArtifactRef:
        return self.locality_resolver.classify_uri(uri, kind=kind, media_type=media_type)

    def plan_build(self, source_id: str, tool_id: Optional[str] = None, backend: str = 'source', **kwargs: Any):
        source = self.registries.sources.require(source_id)
        return self.builder.plan_build(source, tool_id=tool_id, backend=backend, **kwargs)

    def build_tool_package(self, source_id: str, tool_id: Optional[str] = None, backend: str = 'source', dry_run: bool = True, **kwargs: Any) -> ToolPackage:
        source = self.registries.sources.require(source_id)
        pkg = self.builder.build_source(source, tool_id=tool_id, backend=backend, dry_run=dry_run, **kwargs)
        if tool_id:
            self._link(tool_id, 'package_refs', pkg.id)
        return pkg

    def plan_deployment(self, package_id: str, tool_id: Optional[str] = None, backend: str = 'local', **kwargs: Any):
        package = self.registries.packages.require(package_id)
        return self.deployer.plan_deploy(package, tool_id=tool_id, backend=backend, **kwargs)

    # ------------------------------------------------------------------
    # MCP extension points
    # ------------------------------------------------------------------
    def import_mcp_server(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        from .mcp.import_mcp import import_mcp_server
        return import_mcp_server(self, *args, **kwargs)

    def serve_mcp(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        from .mcp.export_mcp_server import serve_toolbox_as_mcp
        return serve_toolbox_as_mcp(self, *args, **kwargs)

    # ------------------------------------------------------------------
    # Stats and helpers
    # ------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'implementation': self.implementation_name,
            'version': self.implementation_version,
            'root_dir': str(self.root_dir),
            'registries': self.registries.stats(),
        }

    def _resolve_tool(self, tool_id_or_name: str) -> ToolSpec:
        tool = self.registries.tools.get(tool_id_or_name) or self.registries.tools.get_by_name(tool_id_or_name)
        if tool is None:
            raise KeyError(f'Tool not found: {tool_id_or_name}')
        return tool

    def _link(self, tool_id: str, field_name: str, ref_id: str) -> None:
        tool = self.registries.tools.require(tool_id)
        refs = list(getattr(tool, field_name))
        if ref_id not in refs:
            refs.append(ref_id)
            setattr(tool, field_name, refs)
            tool.updated_at = utc_now()
            self.registries.tools.upsert(tool)

    def _coerce_artifact_map(self, values: Dict[str, ArtifactRef | Dict[str, Any]]) -> Dict[str, ArtifactRef]:
        out: Dict[str, ArtifactRef] = {}
        for key, value in values.items():
            if isinstance(value, ArtifactRef):
                out[key] = value
            elif isinstance(value, dict):
                out[key] = ArtifactRef(**value)
            else:
                raise TypeError(f'Artifact binding {key} must be ArtifactRef or dict')
        return out

    def _tool_summary(self, tool: ToolSpec) -> Dict[str, Any]:
        return {
            'id': tool.id,
            'name': tool.name,
            'version': tool.version,
            'description': tool.description,
            'capabilities': tool.capabilities,
            'status': tool.status,
            'sources': len(tool.source_refs),
            'packages': len(tool.package_refs),
            'deployments': len(tool.deployment_refs),
            'endpoints': len(tool.endpoint_refs),
            'updated_at': tool.updated_at,
        }


# Compatibility-friendly aliases for dynamic loaders.
ToolBox = ToolBoxV4
Toolbox = ToolBoxV4
