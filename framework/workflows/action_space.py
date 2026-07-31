from typing import Type
from framework.utils.class_helper import get_class_by_name
from framework.workflows.base_agent_action import AgentAction

# ---------------------------------------------------------------------
# Action Space Registry
# ---------------------------------------------------------------------

def get_action_class(action_name: str) -> Type[AgentAction]:
    """
    Retrieve a concrete AgentAction class from the action space by its discriminator value.
    Uses the unified discovery utility to handle Pydantic-style discovery.
    
    Parameters
    ----------
    action_name : str
    The "action" value (e.g., "read_file", "send_message")
    
    Returns
    -------
    Type[AgentAction]
    The concrete action class
    
    Raises
    -------
    ValueError
    If the action name is not recognized in the discovered union.
    """
    return get_class_by_name(
                base_cls=AgentAction,
                sub_dir="agent_actions",
                name=action_name,
                discriminator="action",
                base_package="framework.workflows")
