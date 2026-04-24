"""Workflow model definitions for WOLF.
Imports the concrete action classes from ``agent_actions`` and builds the
Pydantic discriminated union ``Actions`` dynamically. The JSON schema used
by the system prompt is also generated dynamically from action definitions.
"""

from __future__ import annotations

import importlib
import pkgutil
import textwrap
from typing import Annotated, Union, get_args
from pydantic import Field

# Import the base AgentAction class – all actions must inherit from it.
from .base_agent_action import AgentAction

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

# ---------------------------------------------------------------------
# Helper: generate schema string dynamically.
# ---------------------------------------------------------------------
def _generate_schema_string() -> str:
    """Generate a natural-language schema prompt string from action classes.
    Produces a *valid* JSON schema + prose instructions.
    """
    def format_payload_description(cls_: type[AgentAction]) -> str:
        """Return a human-readable payload description using Pydantic schema."""
        schema = cls_.model_json_schema()
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])
        #print(f"[+++] Processing {cls_}:")
        lines = ["{"]
        for field_name, field_info in cls_.model_fields.items():
            #print(f"    - field_name={field_name} | field_info={field_info}")
            prop_schema = properties.get(field_name, {})
            field_type = prop_schema.get("type", "any")

            # Determine if field is optional
            is_required = field_name in required_fields
            is_optional = not is_required

            # Get description
            desc = prop_schema.get("description", "").strip()
            desc = desc or "TODO: describe this field"

            # Format line
            line = f'    "{field_name}": <{field_type}>'
            if desc:
                line += f" # {desc}"
            if is_optional:
                line += " (optional)"
            lines.append(line)

        lines.append("}")
        return "\n".join(lines)

    # Start building the string
    parts = []
    parts.append("You must always respond with a single JSON object.")
    parts.append('The object must have two fields: "action" and "payload".')
    parts.append('The "action" field must be one of the following values, and the payload must match the schema exactly:')
    parts.append("")
    for i, cls_ in enumerate(_action_classes, start=1):
        action_name = cls_.model_fields["action"].default
        parts.append(f"{i}. {cls_.model_fields['description'].default}")
        parts.append('{')
        parts.append(f'  "action": "{action_name}",') # We are controlling key action and payload
        for cls_key in cls_.model_fields.keys():
            if cls_key in ["payload"]:
                parts.append(f'  "payload": {cls_.model_fields["payload_schema"].default}')
            elif cls_key in ["action","payload_schema", "description"]:
                pass
            else:
                cls_val = cls_.model_fields[cls_key]
                parts.append(f'  "{cls_key}": {cls_val.default}')
        parts.append('}')

    # Add caution notes as prose (NOT JSON)
    parts.append("[CAUTION]")
    parts.append("1. Referencing objects (audio, image, video, or any file) in your response:")
    parts.append("   - When you mention an object by name in 'message', provide a proper reference using the appropriate handle (so the UI can render it).")
    parts.append("   - For example: if you mention 'density' and 'temperature' plots, set:")
    parts.append('       message: "Here are the plots for density and temperature",')
    parts.append('       image_references: [{"name": "density", "reference": "/path/to/density.png"}, {"name": "temperature", "reference": "/path/to/temperature.png"}]')
    parts.append("   - Names must exactly match; do not repeat the same name multiple times.")
    parts.append("   - Use one-word names only (e.g., 'gravity_plot', not 'gravity plot').")
    parts.append("   - Avoid Markdown or HTML containers (e.g., **gravity** will not work).")
    parts.append("")
    parts.append("2. Controlling the chat flow with 'yield_motion_to':")
    parts.append("   - If your message is just a notification and you intend to act next, leave 'yield_motion_to' blank.")
    parts.append("   - Otherwise, set 'yield_motion_to' to the name of the entity taking the next turn (e.g., 'user' or 'system').")
    parts.append("   - If unsure, set it to 'yield_motion_to': 'system'.")
    parts.append("")

    # Closing instruction
    parts.append("Do not add extra text. Respond only with the formatted JSON action.")

    return "\n".join(parts)


from typing import Annotated, Union, List
from pydantic import Field

def get_actions_subset(action_names: List) -> tuple:
    """Return a Pydantic discriminated union **and** a schema string for a subset of actions.

    Parameters
    ----------
    action_names: List
        The ``action`` discriminator values you are interested in, e.g.
        ``["read_file", "run_syscall"]``.

    Returns
    -------
    Tuple[Annotated[Union[...], Field(discriminator="action")], str]
        * ``SubsetActions`` – the union type that can be used for validation.
        * ``subset_schema`` – a human‑readable schema string describing only the
          selected actions.
    """
    # ------------------------------------------------------------
    # 1️⃣ Find the concrete classes matching the requested discriminators.
    # ------------------------------------------------------------
    matching_classes = []
    for cls in _action_classes:  # _action_classes is the full sorted list defined earlier
        discr = cls.model_fields["action"].default
        if discr in action_names:
            matching_classes.append(cls)

    if not matching_classes:
        raise ValueError("None of the supplied action names match any known action class")

    # ------------------------------------------------------------
    # 2️⃣ Build the Union type for Pydantic.
    # ------------------------------------------------------------
    SubsetUnion = Union
    SubsetActions = Annotated[SubsetUnion, Field(discriminator="action")]

    # ------------------------------------------------------------
    # 3️⃣ Generate a schema string limited to the selected actions.
    # ------------------------------------------------------------
    def format_payload(cls_: type) -> str:
        schema = cls_.model_json_schema()
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        lines = ["{" ]
        for field_name, field_info in cls_.model_fields.items():
            prop = properties.get(field_name, {})
            f_type = prop.get("type", "any")
            desc = prop.get("description", "").strip() or "TODO: describe this field"
            optional = "(optional)" if field_name not in required else ""
            lines.append(f'    "{field_name}": <{f_type}> # {desc} {optional}'.rstrip())
        lines.append("}")
        return "\n".join(lines)

    parts: List = []
    #parts.append("You must always respond with a single JSON object.")
    #parts.append('The object must have two fields: "action" and "payload".')
    #parts.append('The "action" field must be one of the following values, and the payload must match the schema exactly:')
    #parts.append("")
    for i, cls in enumerate(matching_classes, start=1):
        action_name = cls.model_fields["action"].default
        description = cls.model_fields.get("description", None)
        desc_text = description.default if description is not None else ""
        parts.append(f"{i}. {desc_text}")
        parts.append('{')
        parts.append(f'  "action": "{action_name}",')
        parts.append('  "payload": ' + format_payload(cls))
        parts.append('}')
        parts.append("")
    # (the CAUTION section and closing note can be omitted for the subset view)
    subset_schema = "\n".join(parts)

    return SubsetActions, subset_schema


# Ensure the union is resolved before generating schema.
SCHEMA_STRING = _generate_schema_string()

AGENT_ROLE_PROMPT = """You are a helpful assistant. You exist in the "system", a local sandboxed environement inside which you can take actions.
    You are also provided with and exrtensible and composable infrastructure composed of 'actionboxes' which are other types of sandboxed environements, inside which you are also allowed to take actions.
    The acrtionboxes are connected to the "system" which acts as the interface that allows you to interact with each actionbox.
    All your responses must match an action, and should always be a JSON object matching one of the allowed schemas below.
    Do NOT invent new keys (e.g. do not use 'filename'; always use 'file_path' for write_file).\n
    "If you cannot comply, output an error message in JSON with action 'talk'.\n\n"""

#SYS_PROMPT = (
#    """You are a helpful assistant. You exist in the "system", a local sandboxed environement inside which you can take actions.
#    You are also provided with and exrtensible and composable infrastructure composed of 'actionboxes' which are other types of sandboxed environements, inside which you are also allowed to take actions.
#    The acrtionboxes are connected to the "system" which acts as the interface that allows you to interact with each actionbox.
#    All your responses must match an action, and should always be a JSON object matching one of the allowed schemas below.
#    Do NOT invent new keys (e.g. do not use 'filename'; always use 'file_path' for write_file).\n
#    "If you cannot comply, output an error message in JSON with action 'talk'.\n\n"""
#    + SCHEMA_STRING
#)
SYS_PROMPT = ( AGENT_ROLE_PROMPT + SCHEMA_STRING )

#print(f"[!!!!] SYS_PROMPT = {SYS_PROMPT}")
