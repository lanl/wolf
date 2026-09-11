from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from .models import PermissionDecision, PermissionRequest

PermissionProvider = Callable[[PermissionRequest], PermissionDecision | dict[str, Any] | None]


class PermissionManager:
    """Shared permission manager for risky agent actions.

    The manager owns generic approve-for-session state and decision audit
    history. Concrete user interaction mechanisms are supplied as providers,
    such as terminal prompts, local GUI HTTP, or gateway websocket bridges.
    Providers are tried in order until one returns a decision. If no provider
    can decide, the manager fails closed.
    """

    def __init__(
        self,
        *,
        approval_state: Optional[dict[str, Any]] = None,
        providers: Optional[Iterable[PermissionProvider]] = None,
        max_decisions: int = 200,
    ) -> None:
        self.approval_state = self._normalize_state(approval_state)
        self.providers: list[PermissionProvider] = list(providers or [])
        self.max_decisions = max(1, int(max_decisions or 200))

    @staticmethod
    def _normalize_state(state: Optional[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(state, dict):
            state = {}
        state.setdefault("approved_for_session", {})
        state.setdefault("decisions", [])

        # Backward compatibility with the partially implemented CLI syscall state.
        if state.get("run_syscall_approved_for_session"):
            state["approved_for_session"]["run_syscall"] = True
        return state

    def set_providers(self, providers: Iterable[PermissionProvider]) -> None:
        self.providers = list(providers or [])

    def add_provider(self, provider: PermissionProvider) -> None:
        self.providers.append(provider)

    def is_approved_for_session(self, kind: str) -> bool:
        approvals = self.approval_state.setdefault("approved_for_session", {})
        return bool(approvals.get(str(kind or "")))

    def record_decision(
        self,
        decision: PermissionDecision | dict[str, Any],
        request: PermissionRequest | None = None,
    ) -> PermissionDecision:
        decision_obj = decision if isinstance(decision, PermissionDecision) else PermissionDecision.from_data(decision, request=request)
        if decision_obj.approve_for_session and decision_obj.kind:
            self.approval_state.setdefault("approved_for_session", {})[decision_obj.kind] = True
            # Maintain the legacy syscall key so old snapshots remain understandable.
            if decision_obj.kind == "run_syscall":
                self.approval_state["run_syscall_approved_for_session"] = True

        row = decision_obj.model_dump(mode="json")
        row.setdefault("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        decisions = self.approval_state.setdefault("decisions", [])
        decisions.append(row)
        if len(decisions) > self.max_decisions:
            self.approval_state["decisions"] = decisions[-self.max_decisions:]
        return decision_obj

    def request_permission(
        self,
        request: PermissionRequest | dict[str, Any],
        *,
        providers: Optional[Iterable[PermissionProvider]] = None,
    ) -> PermissionDecision:
        request_obj = request if isinstance(request, PermissionRequest) else PermissionRequest.from_data(request)

        if self.is_approved_for_session(request_obj.kind):
            return PermissionDecision(
                request_id=request_obj.id,
                kind=request_obj.kind,
                approved=True,
                approve_for_session=True,
                source="session",
                status="approved",
                reason=f"{request_obj.kind} approved for rest of session",
            )

        provider_list = list(providers) if providers is not None else self.providers
        for provider in provider_list:
            try:
                raw = provider(request_obj)
            except Exception as exc:
                raw = {
                    "approved": False,
                    "source": getattr(provider, "__name__", "provider"),
                    "status": "provider_error",
                    "reason": f"Permission provider failed: {type(exc).__name__}: {exc}",
                }
            if raw is None:
                continue
            decision = raw if isinstance(raw, PermissionDecision) else PermissionDecision.from_data(raw, request=request_obj)
            return self.record_decision(decision, request_obj)

        return self.record_decision(
            PermissionDecision(
                request_id=request_obj.id,
                kind=request_obj.kind,
                approved=False,
                source="permission_manager",
                status="denied",
                reason="No permission provider returned a decision; failing closed.",
            ),
            request_obj,
        )

    def snapshot(self) -> dict[str, Any]:
        return self.approval_state

    def restore(self, state: dict[str, Any]) -> None:
        self.approval_state = self._normalize_state(state)