from __future__ import annotations

from typing import Optional

from .registry import ToolboxRegistries
from .tool_models import (
    LifecycleStatus,
    ReadinessCheck,
    ReadinessReport,
    ToolDeployment,
    ToolEndpoint,
    ToolSpec,
)


class ToolDiagnostics:
    """Diagnostics/readiness checks for Toolbox v4 MVP."""

    def __init__(self, registries: ToolboxRegistries):
        self.registries = registries

    def readiness_report(
        self,
        tool: ToolSpec,
        deployment: Optional[ToolDeployment] = None,
        endpoint: Optional[ToolEndpoint] = None,
    ) -> ReadinessReport:
        checks = []

        checks.append(ReadinessCheck(
            name='tool_registered',
            passed=True,
            message=f'Tool {tool.name} is registered.',
        ))

        if deployment is None and tool.deployment_refs:
            deployment = self.registries.deployments.get(tool.deployment_refs[0])
        checks.append(ReadinessCheck(
            name='deployment_exists',
            passed=deployment is not None,
            message='Deployment exists.' if deployment else 'No deployment is registered for this tool.',
        ))

        if endpoint is None:
            if deployment and deployment.endpoint_ids:
                endpoint = self.registries.endpoints.get(deployment.endpoint_ids[0])
            elif tool.endpoint_refs:
                endpoint = self.registries.endpoints.get(tool.endpoint_refs[0])
        checks.append(ReadinessCheck(
            name='endpoint_exists',
            passed=endpoint is not None,
            message='Endpoint exists.' if endpoint else 'No endpoint is registered for this tool.',
        ))

        checks.append(ReadinessCheck(
            name='schema_present',
            passed=bool(tool.input_schema or (endpoint and endpoint.input_schema)),
            message='Input schema is present.' if bool(tool.input_schema or (endpoint and endpoint.input_schema)) else 'No input schema is present; allowed for MVP but not ideal.',
        ))

        checks.append(ReadinessCheck(
            name='safety_policy_present',
            passed=tool.safety_policy is not None,
            message='Safety policy is present.',
        ))

        hard_checks = [c for c in checks if c.name in {'tool_registered', 'deployment_exists', 'endpoint_exists', 'safety_policy_present'}]
        passed = all(c.passed for c in hard_checks)
        report = ReadinessReport(
            tool_id=tool.id,
            deployment_id=deployment.id if deployment else None,
            endpoint_id=endpoint.id if endpoint else None,
            checks=checks,
            passed=passed,
            status=LifecycleStatus.READY if passed else LifecycleStatus.TEST_FAILED,
            summary='Ready for execution.' if passed else 'Tool is not ready; see failed checks.',
        )
        self.registries.readiness.upsert(report)
        return report
