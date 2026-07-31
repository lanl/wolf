"""All action definitions for WOLF.
Each action inherits from ``AgentAction`` which now provides a no‑op
``execute`` method that accepts a ``BaseInfrastructure`` instance. Concrete
actions override ``execute`` to perform their side‑effects using the
infrastructure utilities (logging, token counting, chat history, etc.).
"""

from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

class AgentAction(BaseModel):
    """Base class for every action.
    ``execute`` receives a ``BaseInfrastructure`` instance (or ``None``
    for actions that do not need any infrastructure). Sub‑classes override
    it to implement their behaviour.
    """

    action: str = Field(..., description="Discriminator – the LLM must set this: Type of action the agent wants to take")
    description: Optional[str] = Field(default=None, description="Description of the action")
    payload: Any = Field(description="Parameters/Arguments of the action")
    payload_schema: Optional[str] = Field(default=None, description="Schema for the action payload")
    #yield_motion_to: str | None = None
    yield_motion_to: Optional[str]  = Field(default="user", description="entity(user, agent, worker...) who's turn it is to speak or take the next action")
    purpose: str = Field(description="reasons for taking the action")
    expectations: str = Field(description="expected outcome/results")
    def execute(self, infra: Any = None) -> Any:  # pragma: no cover
        """Default implementation does nothing.
        Concrete actions should return whatever is appropriate for the
        workflow (e.g. file content, subprocess output, etc.).
        """
        return None

# --------------
# Helper to collect all concrete subclasses for the union
# --------------
def _collect_action_classes() -> list[type[AgentAction]]:
    """Return a list of every concrete subclass of ``AgentAction``.
    Walks the inheritance tree recursively.
    """
    def _walk(cls: type) -> list[type]:
        result: list[type] = []
        for sub in cls.__subclasses__():
            result.append(sub)
            result.extend(_walk(sub))
        return result
    return _walk(AgentAction)

# The ``Actions`` union is built in ``workflow_models.py``.
