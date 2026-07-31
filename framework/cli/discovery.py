from __future__ import annotations

from typing import Dict, List


def get_workflows() -> Dict[str, dict]:
    from framework.workflows.workflow_space import list_workflow_classes

    workflows = {}
    for name, cls in sorted(list_workflow_classes().items()):
        workflows[name] = {
            "class_name": cls.__name__,
            "module": cls.__module__,
            "wf_tag": getattr(cls, "WF_TAG", None),
            "doc": (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else "",
        }
    return workflows


def get_actions() -> List[dict]:
    from framework.workflows.workflow_models import ACTIONS

    rows = []
    for name, cls in sorted(ACTIONS.items()):
        description = ""
        field = getattr(cls, "model_fields", {}).get("description")
        if field is not None:
            description = str(getattr(field, "default", "") or "")
        rows.append({"action": name, "class_name": cls.__name__, "module": cls.__module__, "description": description})
    return rows
