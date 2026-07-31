from __future__ import annotations

from .tool_models import ExecutionPolicy, SafetyPolicy


class ToolSecurityPolicyEngine:
    """Minimal policy checker for Toolbox v4 MVP."""

    def check_execution_allowed(self, safety: SafetyPolicy, policy: ExecutionPolicy) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if safety.requires_sandbox and policy.isolation in {'none', 'process'}:
            reasons.append('Tool requires sandbox but execution policy requested non-sandboxed isolation.')
        if safety.max_runtime_seconds is not None and policy.timeout is not None and policy.timeout > safety.max_runtime_seconds:
            reasons.append('Requested timeout exceeds tool safety max_runtime_seconds.')
        return (len(reasons) == 0, reasons)
