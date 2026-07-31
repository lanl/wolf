from __future__ import annotations

from typing import Dict, Optional

from .tool_models import (
    ArtifactRef,
    ExecutionPlan,
    ExecutionPolicy,
    LifecycleStatus,
    PlacementPolicy,
    ToolDeployment,
    ToolEndpoint,
    ToolSpec,
)
from .registry import ToolboxRegistries
from .locality import LocalityResolver


class ToolExecutionPlanner:
    """Locality-aware execution planner for Toolbox v4 MVP.

    The current planner is intentionally conservative. It only selects an
    existing deployment/endpoint and explains locality mismatches. Later
    versions can add transfer/mount/deploy-on-demand behavior.
    """

    def __init__(self, registries: ToolboxRegistries):
        self.registries = registries
        self.locality_resolver = LocalityResolver()

    def _choose_deployment(
        self,
        tool: ToolSpec,
        policy: ExecutionPolicy,
        inputs: Dict[str, ArtifactRef],
    ) -> Optional[ToolDeployment]:
        deployments = [
            self.registries.deployments.get(did)
            for did in tool.deployment_refs
        ]
        deployments = [d for d in deployments if d is not None]

        if policy.preferred_deployment_id:
            for d in deployments:
                if d.id == policy.preferred_deployment_id:
                    return d

        ready = [d for d in deployments if d.status in {LifecycleStatus.READY, LifecycleStatus.DEPLOYED, 'ready', 'deployed'}]
        candidates = ready or deployments
        if not candidates:
            return None

        if policy.placement == PlacementPolicy.COMPUTE_NEAR_DATA and inputs:
            input_localities = [a.locality.id for a in inputs.values() if a.locality and a.locality.id]
            for d in candidates:
                if d.locality and d.locality.id in input_localities:
                    return d

        # MVP fallback: first existing deployment.
        return candidates[0]

    def _choose_endpoint(self, tool: ToolSpec, deployment: Optional[ToolDeployment]) -> Optional[ToolEndpoint]:
        endpoint_ids = []
        if deployment:
            endpoint_ids.extend(deployment.endpoint_ids)
        endpoint_ids.extend([eid for eid in tool.endpoint_refs if eid not in endpoint_ids])
        for eid in endpoint_ids:
            ep = self.registries.endpoints.get(eid)
            if ep is not None:
                return ep
        return None

    def plan(
        self,
        tool: ToolSpec,
        inputs: Optional[Dict[str, ArtifactRef]] = None,
        outputs: Optional[Dict[str, ArtifactRef]] = None,
        policy: Optional[ExecutionPolicy] = None,
    ) -> ExecutionPlan:
        inputs = inputs or {}
        outputs = outputs or {}
        policy = policy or ExecutionPolicy()

        warnings = []
        risks = []
        transfers = []
        mounts = []

        deployment = self._choose_deployment(tool, policy, inputs)
        endpoint = self._choose_endpoint(tool, deployment)

        explanation_parts = [
            f"Planning execution for tool {tool.name} ({tool.id}) with placement policy {policy.placement}."
        ]

        if deployment is None:
            warnings.append('No deployment exists for this tool. Build/deploy/test is required before execution.')
            explanation_parts.append('No existing deployment was found.')
        else:
            explanation_parts.append(
                f"Selected deployment {deployment.name} ({deployment.id}) in locality {deployment.locality.kind}:{deployment.locality.id}."
            )

        if endpoint is None:
            warnings.append('No endpoint exists for the selected tool/deployment.')
            explanation_parts.append('No callable endpoint was found.')
        else:
            explanation_parts.append(f"Selected endpoint {endpoint.id} using protocol {endpoint.protocol}.")

        if deployment is not None and inputs:
            for name, artifact in inputs.items():
                same = artifact.locality.id == deployment.locality.id
                if same:
                    explanation_parts.append(f"Input {name} is already local to selected deployment.")
                else:
                    msg = (
                        f"Input {name} locality {artifact.locality.kind}:{artifact.locality.id} "
                        f"differs from deployment locality {deployment.locality.kind}:{deployment.locality.id}."
                    )
                    locality_plan = self.locality_resolver.plan_mount_or_transfer(
                        artifact,
                        deployment.locality,
                        allow_mounts=policy.allow_mounts,
                        allow_data_movement=policy.allow_data_movement,
                    )
                    if locality_plan.get('mount') is not None:
                        mounts.append(locality_plan['mount'])
                        explanation_parts.append(msg + ' Planner added a concrete mount placeholder.')
                    elif locality_plan.get('transfer') is not None:
                        transfers.append(locality_plan['transfer'])
                        explanation_parts.append(msg + ' Planner added a concrete transfer placeholder.')
                    else:
                        warning = locality_plan.get('warning') or (msg + ' Data movement/mounts are not allowed by policy.')
                        warnings.append(warning)
                        risks.append('locality_mismatch')

        readiness = LifecycleStatus.REGISTERED
        if deployment is not None:
            readiness = deployment.status
        elif tool.status:
            readiness = tool.status

        invocation = {}
        if endpoint is not None:
            invocation = {
                'protocol': endpoint.protocol,
                'uri': endpoint.uri,
                'entrypoint': endpoint.entrypoint,
                **endpoint.invocation,
            }

        return ExecutionPlan(
            tool_id=tool.id,
            tool_name=tool.name,
            tool_version=tool.version,
            selected_deployment_id=deployment.id if deployment else None,
            selected_endpoint_id=endpoint.id if endpoint else None,
            input_bindings=inputs,
            output_bindings=outputs,
            mounts=mounts,
            transfers=transfers,
            invocation=invocation,
            readiness_status=readiness,
            risks=risks,
            warnings=warnings,
            explanation=' '.join(explanation_parts),
            policy=policy,
        )
