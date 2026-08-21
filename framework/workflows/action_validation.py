from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from framework.workflows.action_registry import ActionRegistry, ActionSpec, get_default_action_registry
from framework.workflows.base_agent_action import AgentAction


class ActionEnvelope(BaseModel):
    """Lightweight top-level action envelope.

    This intentionally validates only universal fields. Selected-action payload
    validation happens after registry lookup.
    """

    model_config = ConfigDict(extra="ignore")

    action: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    purpose: str = ""
    expectations: str = ""
    yield_motion_to: Optional[str] = "user"


class ActionValidationError(BaseModel):
    stage: Literal[
        "json_parse",
        "envelope_validation",
        "unknown_action",
        "action_not_allowed",
        "payload_validation",
        "execution_failed",
    ]
    message: str
    action: Optional[str] = None
    details: Any = None
    repair_hint: Optional[str] = None


@dataclass
class ValidatedAction:
    envelope: ActionEnvelope
    spec: ActionSpec
    action_obj: AgentAction
    normalized: Dict[str, Any]


def response_to_dict(response: Any) -> Dict[str, Any] | None:
    """Normalize common provider/Pydantic response shapes to a plain dict."""
    if isinstance(response, dict):
        parsed = response.get("parsed")
        if isinstance(parsed, BaseModel):
            return parsed.model_dump(mode="json", by_alias=True)
        if isinstance(parsed, dict):
            return parsed
        return response

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, BaseModel):
        return parsed.model_dump(mode="json", by_alias=True)
    if isinstance(parsed, dict):
        return parsed

    if isinstance(response, BaseModel):
        dumped = response.model_dump(mode="json", by_alias=True)
        parsed_dump = dumped.get("parsed") if isinstance(dumped, dict) else None
        if isinstance(parsed_dump, dict):
            return parsed_dump
        return dumped if isinstance(dumped, dict) else None

    if hasattr(response, "model_dump"):
        try:
            dumped = response.model_dump(mode="json", by_alias=True)
            if isinstance(dumped, dict):
                parsed_dump = dumped.get("parsed")
                return parsed_dump if isinstance(parsed_dump, dict) else dumped
        except Exception:
            return None
    return None


def normalize_action_payload(data: Dict[str, Any], actor: Any = None) -> Dict[str, Any]:
    """Small shared normalization before envelope/payload validation."""
    actor_name = actor if isinstance(actor, str) else getattr(actor, "name", None)
    if not isinstance(actor_name, str) or not actor_name:
        actor_name = "assistant"

    normalized = {k: v for k, v in data.items() if v is not None}
    if "yield_motion_to" in normalized and not normalized["yield_motion_to"]:
        del normalized["yield_motion_to"]

    if normalized.get("action") == "send_message":
        payload_section = normalized.get("payload", {})
        if isinstance(payload_section, dict):
            if not isinstance(payload_section.get("sender"), str) or not payload_section.get("sender"):
                payload_section["sender"] = actor_name
            if not isinstance(payload_section.get("receiver"), str) or not payload_section.get("receiver"):
                payload_section["receiver"] = "user"
            normalized["payload"] = payload_section
    return normalized


def validate_action_response(
    response: Any,
    registry: ActionRegistry | None = None,
    allowed_actions: Iterable[str] | None = None,
    actor: Any = None,
) -> ValidatedAction | ActionValidationError:
    registry = registry or get_default_action_registry()

    data = response_to_dict(response)
    if not isinstance(data, dict):
        return ActionValidationError(
            stage="json_parse",
            message="Response is not a JSON object or Pydantic/dict action payload.",
            details={"response_type": str(type(response))},
            repair_hint="Return exactly one JSON action object.",
        )

    normalized = normalize_action_payload(data, actor=actor)
    try:
        envelope = ActionEnvelope.model_validate(normalized)
    except ValidationError as exc:
        return ActionValidationError(
            stage="envelope_validation",
            message="Invalid action envelope.",
            details=exc.errors(),
            repair_hint="Use top-level keys: action, payload, purpose, expectations, yield_motion_to.",
        )

    spec = registry.get(envelope.action)
    if spec is None:
        return ActionValidationError(
            stage="unknown_action",
            action=envelope.action,
            message=f"Unknown action: {envelope.action}",
            details={"known_action_count": len(registry.names())},
            repair_hint="Use one of the currently allowed action names exactly as shown in the prompt.",
        )

    if allowed_actions is not None:
        allowed_set = {str(a) for a in allowed_actions}
        if envelope.action not in allowed_set:
            return ActionValidationError(
                stage="action_not_allowed",
                action=envelope.action,
                message=f"Action not allowed in this workflow/policy: {envelope.action}",
                details={"allowed_actions": sorted(allowed_set)},
                repair_hint="Choose an action from the current allowed action list.",
            )

    try:
        # Validate only the selected action class, never the global union.
        action_obj = spec.action_cls.model_validate(normalized)
    except ValidationError as exc:
        return ActionValidationError(
            stage="payload_validation",
            action=envelope.action,
            message=f"Invalid payload for selected action: {envelope.action}",
            details=exc.errors(),
            repair_hint=f"Fix only the payload for {envelope.action}; do not change to an unrelated action unless necessary.",
        )
    except Exception as exc:
        return ActionValidationError(
            stage="payload_validation",
            action=envelope.action,
            message=f"Selected action validation failed for {envelope.action}: {type(exc).__name__}: {exc}",
            details=str(exc),
        )

    return ValidatedAction(envelope=envelope, spec=spec, action_obj=action_obj, normalized=normalized)
