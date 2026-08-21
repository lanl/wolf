from __future__ import annotations

import importlib
import json
import pkgutil
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Optional, get_args, get_origin

from pydantic import BaseModel

from framework.workflows.base_agent_action import AgentAction


@dataclass(frozen=True)
class ActionSpec:
    """Registry metadata for one concrete AgentAction class."""

    name: str
    action_cls: type[AgentAction]
    payload_model: type[BaseModel] | None
    description: str | None = None
    payload_schema: str | None = None
    risk_level: str = "normal"
    tags: tuple[str, ...] = ()


class ActionRegistry:
    """Name-first registry for AgentAction classes.

    This registry intentionally validates only the selected action class at
    runtime. It can still render prompt snippets and provide compatibility data,
    but it does not require callers to validate every response against a giant
    discriminated union.
    """

    def __init__(self, action_classes: Optional[Iterable[type[AgentAction]]] = None):
        self._specs: dict[str, ActionSpec] = {}
        self._classes: list[type[AgentAction]] = []
        if action_classes:
            for cls in action_classes:
                self.register(cls)

    def register(self, action_cls: type[AgentAction]) -> None:
        if not isinstance(action_cls, type) or not issubclass(action_cls, AgentAction):
            raise TypeError(f"Expected AgentAction subclass, got {action_cls!r}")
        if action_cls is AgentAction:
            return

        name = extract_action_name(action_cls)
        if not name:
            raise ValueError(f"Action class {action_cls.__module__}.{action_cls.__name__} has no action discriminator default")
        if name in self._specs:
            prev = self._specs[name].action_cls
            raise ValueError(
                "Duplicate action discriminator "
                f"{name!r}: {prev.__module__}.{prev.__name__} and {action_cls.__module__}.{action_cls.__name__}"
            )

        payload_model = extract_payload_model(action_cls)
        description = extract_field_default(action_cls, "description")
        payload_schema = extract_field_default(action_cls, "payload_schema")
        spec = ActionSpec(
            name=name,
            action_cls=action_cls,
            payload_model=payload_model,
            description=str(description) if description not in (None, "") else None,
            payload_schema=str(payload_schema) if payload_schema not in (None, "") else None,
            risk_level=infer_risk_level(name),
            tags=infer_tags(name),
        )
        self._specs[name] = spec
        self._classes.append(action_cls)

    def get(self, name: str) -> ActionSpec | None:
        return self._specs.get(str(name or ""))

    def names(self) -> list[str]:
        return sorted(self._specs.keys())

    def specs(self) -> list[ActionSpec]:
        return [self._specs[name] for name in self.names()]

    def subset(self, names: Iterable[str]) -> "ActionRegistry":
        selected = ActionRegistry()
        missing: list[str] = []
        for name in names:
            spec = self.get(name)
            if spec is None:
                missing.append(str(name))
                continue
            selected.register(spec.action_cls)
        if missing:
            # Do not silently grant broader action capability. The caller can
            # decide whether unknown allowed names should be fatal.
            selected._missing = missing  # type: ignore[attr-defined]
        return selected

    def render_prompt(self, names: Iterable[str] | None = None, budget: int | None = None) -> str:
        specs = self.specs() if names is None else [self._specs[n] for n in names if n in self._specs]
        parts: list[str] = [
            "Allowed action examples:",
            "The object must have exactly these top-level fields: action, payload, purpose, expectations, yield_motion_to.",
            "Respond only with one valid JSON action object.",
            "",
        ]
        for idx, spec in enumerate(specs, start=1):
            example = {
                "action": spec.name,
                "payload": payload_example_for_action(spec.action_cls),
                "purpose": "why this action is being taken",
                "expectations": "what result is expected",
                "yield_motion_to": "user",
            }
            risk = "" if spec.risk_level == "normal" else f" [RISK: {spec.risk_level}]"
            desc = spec.description or "No description."
            parts.append(f"{idx}. {spec.name}{risk}: {desc}")
            parts.append(json.dumps(example, indent=2, sort_keys=True))
            parts.append("")
            if budget is not None and len("\n".join(parts)) >= budget:
                parts.append("[TRUNCATED: action prompt budget reached]")
                break
        parts.append("Important rules:")
        parts.append("- Do not invent actions or payload keys.")
        parts.append("- Do not use comments, trailing commas, Python reprs, or schema internals.")
        parts.append("- If you cannot comply, use send_message with a concise error.")
        return "\n".join(parts)


def extract_field_default(action_cls: type[AgentAction], field_name: str) -> Any:
    field = getattr(action_cls, "model_fields", {}).get(field_name)
    if field is None:
        return None
    default = getattr(field, "default", None)
    if repr(default) == "PydanticUndefined":
        return None
    return default


def extract_action_name(action_cls: type[AgentAction]) -> str | None:
    default = extract_field_default(action_cls, "action")
    if isinstance(default, str) and default:
        return default
    field = getattr(action_cls, "model_fields", {}).get("action")
    annotation = getattr(field, "annotation", None)
    if get_origin(annotation) is not None:
        args = [a for a in get_args(annotation) if isinstance(a, str)]
        if args:
            return args[0]
    return None


def extract_payload_model(action_cls: type[AgentAction]) -> type[BaseModel] | None:
    field = getattr(action_cls, "model_fields", {}).get("payload")
    annotation = getattr(field, "annotation", None)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def infer_risk_level(name: str) -> str:
    if name in {"run_syscall", "create_universe", "terminate_deployment"}:
        return "high"
    if name in {"write_file", "clear_memory_category", "forget_memory", "batch_forget_memory", "truncate_context_window"}:
        return "medium"
    return "normal"


def infer_tags(name: str) -> tuple[str, ...]:
    tags: list[str] = []
    if name.startswith("gui_"):
        tags.append("gui")
    if name.startswith("universe_"):
        tags.append("universe")
    if name in {"write_file", "run_syscall"}:
        tags.append("local_side_effect")
    if infer_risk_level(name) != "normal":
        tags.append("risky")
    return tuple(tags)


def collect_agent_action_classes() -> list[type[AgentAction]]:
    """Import agent action modules and collect concrete AgentAction subclasses."""
    package_name = "framework.workflows.agent_actions"
    package = importlib.import_module(package_name)
    if hasattr(package, "__path__"):
        for _, module_name, _ in pkgutil.iter_modules(package.__path__):
            importlib.import_module(f"{package_name}.{module_name}")

    def walk(cls: type) -> list[type[AgentAction]]:
        out: list[type[AgentAction]] = []
        for sub in cls.__subclasses__():
            if issubclass(sub, AgentAction):
                out.append(sub)
                out.extend(walk(sub))
        return out

    concrete = [cls for cls in walk(AgentAction) if extract_action_name(cls)]
    return sorted(concrete, key=lambda cls: extract_action_name(cls) or cls.__name__)


@lru_cache(maxsize=1)
def get_default_action_registry() -> ActionRegistry:
    return ActionRegistry(collect_agent_action_classes())


def _resolve_ref(schema: dict, ref: str) -> dict:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return {}
    cur: Any = schema
    for part in ref[2:].split("/"):
        cur = cur.get(part, {}) if isinstance(cur, dict) else {}
    return cur if isinstance(cur, dict) else {}


def _deref(prop: dict, root: dict) -> dict:
    seen: set[str] = set()
    while isinstance(prop, dict) and "$ref" in prop and prop["$ref"] not in seen:
        seen.add(prop["$ref"])
        resolved = _resolve_ref(root, prop["$ref"])
        merged = {k: v for k, v in prop.items() if k != "$ref"}
        prop = {**resolved, **merged}
    return prop if isinstance(prop, dict) else {}


def _example_for_schema(prop: dict, root: dict, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    prop = _deref(prop, root)
    for union_key in ("anyOf", "oneOf", "allOf"):
        branches = prop.get(union_key)
        if isinstance(branches, list) and branches:
            branch = next((b for b in branches if isinstance(b, dict) and b.get("type") != "null"), branches[0])
            return _example_for_schema(branch, root, depth + 1)
    if "const" in prop:
        return prop["const"]
    if "enum" in prop and prop["enum"]:
        return prop["enum"][0]
    typ = prop.get("type")
    if typ == "object" or "properties" in prop:
        props = prop.get("properties", {})
        return {name: _example_for_schema(child, root, depth + 1) for name, child in props.items()} if isinstance(props, dict) else {}
    if typ == "array":
        return [_example_for_schema(prop.get("items", {}), root, depth + 1)]
    if "default" in prop and prop.get("default") is not None:
        return prop.get("default")
    if typ == "integer":
        return int(prop.get("minimum", 1))
    if typ == "number":
        return float(prop.get("minimum", 1.0))
    if typ == "boolean":
        return False
    if typ == "string":
        return "string"
    return "value"


def payload_example_for_action(action_cls: type[AgentAction]) -> dict:
    try:
        schema = action_cls.model_json_schema()
        payload_schema = schema.get("properties", {}).get("payload", {})
        example = _example_for_schema(payload_schema, schema)
        return example if isinstance(example, dict) else {}
    except Exception:
        payload_schema = extract_field_default(action_cls, "payload_schema")
        if isinstance(payload_schema, str):
            try:
                loaded = json.loads(payload_schema)
                return loaded if isinstance(loaded, dict) else {}
            except Exception:
                return {}
        return {}
