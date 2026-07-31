from typing import Dict, Type

from framework.utils.class_helper import _collect_classes, get_class_by_name
from framework.workflows.base_workflow import BaseWorkflow

# Import all custom workflows dynamically.
custom_WFs = _collect_classes(
    base_cls=BaseWorkflow,
    sub_dir="custom_workflows",
    base_package="framework.workflows",
)

# Build the workflow registry from discovered workflow subclasses.
WFs: Dict[str, Type[BaseWorkflow]] = {BaseWorkflow.__name__: BaseWorkflow}
for WF in custom_WFs:
    if WF.__name__ in WFs:
        print(f"[!!][DUPLICATES] Workflow class name already registered: {WF.__name__}")
    WFs[WF.__name__] = WF

WF_NAMEs = list(WFs.keys())


# ---------------------------------------------------------------------
# Workflow Space Registry
# ---------------------------------------------------------------------

def get_workflow_class(workflow_name: str) -> Type[BaseWorkflow]:
    """
    Retrieve a workflow class from the registered workflow space by class name
    or by its WF_TAG discriminator when available.
    """
    if workflow_name in WFs:
        return WFs[workflow_name]
    return get_class_by_name(
        base_cls=BaseWorkflow,
        sub_dir="custom_workflows",
        name=workflow_name,
        discriminator="WF_TAG",
        base_package="framework.workflows",
    )


def list_workflow_classes() -> Dict[str, Type[BaseWorkflow]]:
    """Return a copy of the currently discovered workflow registry."""
    return dict(WFs)
