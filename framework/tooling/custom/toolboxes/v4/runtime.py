from __future__ import annotations

import time
from typing import Any, Dict, Optional, Sequence

from framework.tooling.custom.tools.v4 import ToolAdapterRegistry, ToolExecutionRequest

from .registry import ToolboxRegistries
from .tool_models import ExecutionPlan, RunStatus, ToolRun, utc_now


class ToolRuntimeExecutor:
    """Runtime executor for Toolbox v4.

    This class records ToolRun lifecycle state but delegates concrete endpoint
    invocation to custom Tool adapters under `framework.tooling.custom.tools.v4`.
    """

    def __init__(self, registries: ToolboxRegistries, adapter_registry: Optional[ToolAdapterRegistry] = None):
        self.registries = registries
        self.adapter_registry = adapter_registry or ToolAdapterRegistry()

    def execute_plan(
        self,
        plan: ExecutionPlan,
        args: Optional[Sequence[str]] = None,
        fn_args: Optional[Sequence[Any]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        input_data: Optional[str | bytes] = None,
        text: bool = True,
    ) -> ToolRun:
        started = time.perf_counter()
        run = ToolRun(
            plan_id=plan.id,
            tool_id=plan.tool_id,
            deployment_id=plan.selected_deployment_id,
            endpoint_id=plan.selected_endpoint_id,
            inputs=plan.input_bindings,
            outputs=plan.output_bindings,
            status=RunStatus.RUNNING,
            started_at=utc_now(),
        )
        self.registries.runs.upsert(run)

        try:
            endpoint = self.registries.endpoints.require(plan.selected_endpoint_id) if plan.selected_endpoint_id else None
            if endpoint is None:
                raise RuntimeError("Execution plan has no selected endpoint")

            protocol = str(endpoint.protocol)
            adapter = self.adapter_registry.get(protocol)
            request = ToolExecutionRequest(
                endpoint_id=endpoint.id,
                protocol=protocol,
                uri=endpoint.uri,
                entrypoint=endpoint.entrypoint,
                invocation=endpoint.invocation or {},
                args=args,
                fn_args=fn_args,
                kwargs=kwargs or {},
                env=env,
                cwd=cwd,
                timeout=timeout,
                input_data=input_data,
                text=text,
            )
            result = adapter.execute(request)

            run.status = RunStatus.SUCCESS if result.ok else RunStatus.FAILED
            run.returncode = result.returncode
            run.stdout = result.stdout
            run.stderr = result.stderr
            run.result = result.result
            run.logs.extend(result.logs)
            run.metrics.update(result.metrics)
            run.error = result.error
            run.diagnostics.update({"adapter": type(adapter).__name__, "metadata": result.metadata})

        except Exception as e:
            run.status = RunStatus.FAILED
            run.returncode = -1 if run.returncode is None else run.returncode
            run.error = str(e)
            run.stderr = str(e) if run.stderr is None else run.stderr

        finally:
            run.ended_at = utc_now()
            run.duration_seconds = time.perf_counter() - started
            self.registries.runs.upsert(run)

        return run
