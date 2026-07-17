from typing import Type
from framework.utils.class_helper import _collect_classes, get_class_by_name
from framework.workflows.base_workflow import BaseWorkflow

# Import all custom workflows dynamically
custom_WFs = _collect_classes(base_cls=BaseWorkflow,
                              sub_dir="custom_workflows",
                              base_package="framework.workflows")
# Build the space of WF from extracted WFs
WFs = {BaseWorkflow.__name__: BaseWorkflow}
for WF in custom_WFs:
    if WF.__name__ in WFs.keys(): print(f"[!!][DUPLICATES]")
    WFs [WF.__name__] = WF
#
WF_NAMEs = list(WF.keys())


# ---------------------------------------------------------------------
# Workflow Space Registry
# ---------------------------------------------------------------------

def get_workflow_class(workflow_name: str) -> Type[BaseWorkflow]:
    """
    Retrieve a workflow class from the registered workspace by its name/tag.
    Uses the unified discovery utility to handle standard class discovery.
    
    Parameters
    ----------
    workflow_name : str
        The identifier of the workflow (e.g., 'TurnBasedWorkflow')
        
    Returns
    -------
    Type[BaseWorkflow]
        The concrete workflow class
        
    Raises
    -------
    ValueError
        If the workflow name is not found in the space.
    """
    return get_class_by_name(
        base_cls=BaseWorkflow,
        sub_dir="custom_workflows",
        name=workflow_name,
        discriminator="WF_TAG",
        base_package="framework.workflows"
    )
