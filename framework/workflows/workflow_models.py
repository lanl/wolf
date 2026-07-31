"""Workflow model definitions for WOLF.
Imports the concrete action classes from ``agent_actions`` and builds the
Pydantic discriminated union ``Actions`` dynamically. The JSON schema used
by the system prompt is also generated dynamically from action definitions.
"""

from __future__ import annotations

import os

import importlib
import pkgutil
import textwrap
import json
from typing import Annotated, Union, get_args
from pydantic import Field

# Import the base AgentAction class – all actions must inherit from it.
from .base_agent_action import AgentAction
from framework.utils.tokenomics import num_tokens_from_string
from framework.utils.io_tools import console

# ---------------------------------------------------------------------
# Dynamically collect every concrete subclass of ``AgentAction``.
# ---------------------------------------------------------------------
def _collect_action_classes() -> list[type[AgentAction]]:
    """Return a list of every concrete subclass of ``AgentAction``.
    Walks the inheritance tree recursively, ensuring that all sub‑modules
    under ``framework.workflows.agent_actions`` are imported first.
    """
    # Resolve the package name that contains the action modules.
    package_name = __name__.rsplit('.', 1)[0] + ".agent_actions"
    package = importlib.import_module(package_name)

    # Import all sub‑modules so their classes are registered.
    if hasattr(package, "__path__"):
        for _, module_name, _ in pkgutil.iter_modules(package.__path__):
            importlib.import_module(f"{package_name}.{module_name}")

    def _walk(cls: type) -> list:
        result: list = []
        for sub in cls.__subclasses__():
            result.append(sub)
            result.extend(_walk(sub))
        return result

    return _walk(AgentAction)

# Build the union type for Pydantic discriminated union.
# We'll use the *sorted* list to make schema generation deterministic.
_action_classes = sorted(_collect_action_classes(), key=lambda cls: cls.model_fields["action"].default)
_ActionsUnion = Union[tuple(_action_classes)]
Actions = Annotated[_ActionsUnion, Field(discriminator="action")]

# Build the list
ACTIONS = {}
ACTION_SPACE_PROMPT = "Below are 'name':'description' key-value pairs for all allowed actions in this workflow:\n"
ACTION_SPACE_PROMPT += "*** BEGIN ACTION SPACE ***:\n"
for i, A in enumerate(_action_classes):
    model_fields = getattr(A, "model_fields", {})
    action_name_fields  = model_fields.get("action")
    action_description_fields = model_fields.get("description")
    action_name = getattr(action_name_fields, "default", {})
    description = getattr(action_description_fields, "default", {})
    ACTIONS[action_name] = A
    ACTION_SPACE_PROMPT += f"'{action_name}': {description}\n"
ACTION_NAMES = list(ACTIONS.keys())
ACTION_SPACE_PROMPT +=  "*** END ACTION SPACE ***"
#print(f"{ACTION_SPACE_PROMPT}")
if os.environ.get("WOLF_DEBUG_ACTION_SPACE", "").lower() in {"1", "true", "yes"}:
    console.print(f"[+] ACTION_SPACE_PROMPT: {num_tokens_from_string(ACTION_SPACE_PROMPT)} Tokens")

# ---------------------------------------------------------------------
# Helper: generate schema string dynamically.
# ---------------------------------------------------------------------
def _generate_schema_string() -> str:
    """Generate a compact, model-facing schema prompt from action classes.

    The previous implementation interpolated raw Pydantic ``FieldInfo`` objects
    and undefined defaults into the prompt. That produced non-JSON examples like
    ``PydanticUndefined`` and ``FieldInfo(...)`` which encouraged malformed model
    responses and validation retry loops. This renderer emits only valid JSON
    examples with primitive placeholder values.
    """

    def _resolve_ref(schema: dict, ref: str) -> dict:
        """Resolve a local JSON-schema $ref such as '#/$defs/Payload'."""
        if not ref.startswith("#/"):
            return {}
        cur = schema
        for part in ref[2:].split("/"):
            cur = cur.get(part, {}) if isinstance(cur, dict) else {}
        return cur if isinstance(cur, dict) else {}

    def _deref(prop: dict, root: dict) -> dict:
        """Return a property schema with local refs resolved."""
        seen = set()
        while isinstance(prop, dict) and "$ref" in prop and prop["$ref"] not in seen:
            seen.add(prop["$ref"])
            resolved = _resolve_ref(root, prop["$ref"])
            merged = {k: v for k, v in prop.items() if k != "$ref"}
            prop = {**resolved, **merged}
        return prop if isinstance(prop, dict) else {}

    def _example_for_schema(prop: dict, root: dict, depth: int = 0):
        """Build a JSON-serializable placeholder example for a schema node."""
        if depth > 4:
            return "..."
        prop = _deref(prop, root)

        # Pick the first non-null branch of union-like schemas.
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
            if not isinstance(props, dict):
                return {}
            return {
                name: _example_for_schema(child, root, depth + 1)
                for name, child in props.items()
            }
        if typ == "array":
            items = prop.get("items", {})
            return [_example_for_schema(items, root, depth + 1)]
        if "default" in prop and prop.get("default") is not None:
            return prop.get("default")
        if typ == "integer":
            if "minimum" in prop:
                return int(prop.get("minimum"))
            if "exclusiveMinimum" in prop:
                return int(prop.get("exclusiveMinimum")) + 1
            return 1
        if typ == "number":
            if "minimum" in prop:
                return float(prop.get("minimum"))
            if "exclusiveMinimum" in prop:
                return float(prop.get("exclusiveMinimum")) + 1.0
            return 1.0
        if typ == "boolean":
            return False
        if typ == "string":
            return "string"
        return "value"

    def _payload_example(cls_: type[AgentAction]) -> dict:
        schema = cls_.model_json_schema()
        payload_schema = schema.get("properties", {}).get("payload", {})
        example = _example_for_schema(payload_schema, schema)
        return example if isinstance(example, dict) else {}

    parts: list[str] = []
    parts.append("You must always respond with exactly one valid JSON object and no surrounding prose or Markdown.")
    parts.append('The object must have exactly these top-level fields: "action", "payload", "purpose", "expectations", and "yield_motion_to".')
    parts.append('The "action" value must be one of the allowed action names below; "payload" must match that action.')
    parts.append('Use string values for "purpose", "expectations", and "yield_motion_to". If you intend to act again, set "yield_motion_to" to your exact agent name; otherwise use "user" or "system" as appropriate.')
    parts.append("")
    parts.append("Allowed action examples:")

    for i, cls_ in enumerate(_action_classes, start=1):
        action_name = cls_.model_fields["action"].default
        description = cls_.model_fields.get("description")
        desc_text = getattr(description, "default", "") if description is not None else ""
        example = {
            "action": action_name,
            "payload": _payload_example(cls_),
            "purpose": "why this action is being taken",
            "expectations": "what result is expected",
            "yield_motion_to": "user",
        }
        parts.append(f"{i}. {desc_text}")
        parts.append(json.dumps(example, indent=2, sort_keys=True))
        parts.append("")

    parts.append("Important rules:")
    parts.append("- Do not invent actions or keys.")
    parts.append("- Do not use Python object reprs, internal schema artifacts, comments, trailing commas, or single-quoted dict reprs.")
    parts.append("- If you cannot complete the requested task, use the valid 'send_message' action with a concise error message.")
    parts.append("- Respond only with the JSON action object.")

    return "\n".join(parts)


from typing import Annotated, Union, List
from pydantic import Field

def get_actions_subset(action_names: List) -> tuple:
    """Return a Pydantic discriminated union and clean schema string for actions.

    The subset schema is injected into prompts for restricted-action workflows, so
    it must follow the same rules as the full schema renderer: valid JSON examples
    only, no Pydantic internals, and no Python repr payload dumps.
    """
    matching_classes = []
    for cls in _action_classes:
        discr = cls.model_fields["action"].default
        if discr in action_names:
            matching_classes.append(cls)

    if not matching_classes:
        raise ValueError("None of the supplied action names match any known action class")

    SubsetUnion = Union[tuple(matching_classes)]
    SubsetActions = Annotated[SubsetUnion, Field(discriminator="action")]

    def _resolve_ref(schema: dict, ref: str) -> dict:
        if not ref.startswith("#/"):
            return {}
        cur = schema
        for part in ref[2:].split("/"):
            cur = cur.get(part, {}) if isinstance(cur, dict) else {}
        return cur if isinstance(cur, dict) else {}

    def _deref(prop: dict, root: dict) -> dict:
        seen = set()
        while isinstance(prop, dict) and "$ref" in prop and prop["$ref"] not in seen:
            seen.add(prop["$ref"])
            resolved = _resolve_ref(root, prop["$ref"])
            merged = {k: v for k, v in prop.items() if k != "$ref"}
            prop = {**resolved, **merged}
        return prop if isinstance(prop, dict) else {}

    def _example_for_schema(prop: dict, root: dict, depth: int = 0):
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
            if "minimum" in prop:
                return int(prop.get("minimum"))
            if "exclusiveMinimum" in prop:
                return int(prop.get("exclusiveMinimum")) + 1
            return 1
        if typ == "number":
            if "minimum" in prop:
                return float(prop.get("minimum"))
            if "exclusiveMinimum" in prop:
                return float(prop.get("exclusiveMinimum")) + 1.0
            return 1.0
        if typ == "boolean":
            return False
        if typ == "string":
            return "string"
        return "value"

    def _payload_example(cls_: type[AgentAction]) -> dict:
        schema = cls_.model_json_schema()
        payload_schema = schema.get("properties", {}).get("payload", {})
        example = _example_for_schema(payload_schema, schema)
        return example if isinstance(example, dict) else {}

    parts: List[str] = []
    for i, cls in enumerate(matching_classes, start=1):
        action_name = cls.model_fields["action"].default
        description = cls.model_fields.get("description")
        desc_text = getattr(description, "default", "") if description is not None else ""
        example = {
            "action": action_name,
            "payload": _payload_example(cls),
            "purpose": "why this action is being taken",
            "expectations": "what result is expected",
            "yield_motion_to": "user",
        }
        parts.append(f"{i}. {desc_text}")
        parts.append(json.dumps(example, indent=2, sort_keys=True))
        parts.append("")

    subset_schema = "\n".join(parts)
    return SubsetActions, subset_schema


# Ensure the union is resolved before generating schema.
SCHEMA_STRING = _generate_schema_string()

AGENT_ROLE_PROMPT = """You are a helpful assistant. You exist in the "system", a local sandboxed environement inside which you can take actions.
    You are also provided with and exrtensible and composable infrastructure composed of 'actionboxes' which are other types of sandboxed environements, inside which you are also allowed to take actions.
    The acrtionboxes are connected to the "system" which acts as the interface that allows you to interact with each actionbox.
    All your responses must match an action, and should always be a JSON object matching one of the allowed schemas below.
    Do NOT invent new keys (e.g. do not use 'filename'; always use 'file_path' for write_file).\n
    "If you cannot comply, output an error message in JSON with action 'send_message'.\n\n"""

#SYS_PROMPT = (
#    """You are a helpful assistant. You exist in the "system", a local sandboxed environement inside which you can take actions.
#    You are also provided with and exrtensible and composable infrastructure composed of 'actionboxes' which are other types of sandboxed environements, inside which you are also allowed to take actions.
#    The acrtionboxes are connected to the "system" which acts as the interface that allows you to interact with each actionbox.
#    All your responses must match an action, and should always be a JSON object matching one of the allowed schemas below.
#    Do NOT invent new keys (e.g. do not use 'filename'; always use 'file_path' for write_file).\n
#    "If you cannot comply, output an error message in JSON with action 'send_message'.\n\n"""
#    + SCHEMA_STRING
#)
SYS_PROMPT = ( AGENT_ROLE_PROMPT + SCHEMA_STRING )

#print(f"[!!!!] SYS_PROMPT = {SYS_PROMPT}")
