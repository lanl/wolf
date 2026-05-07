from typing import Any
from pydantic import BaseModel, Field, ConfigDict
from framework.infrastructure.base_infrastructure import BaseInfrastructure
from framework.workflows.workflow_models import (Actions as FullActions,
    SCHEMA_STRING as FULL_SCHEMA_STRING,
    AGENT_ROLE_PROMPT as FULL_AGENT_ROLE_PROMPT
)

class BaseSession(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    infra: BaseInfrastructure = Field(description="Workflow Infrastructure")
    actions_union: Any = Field(default=FullActions, description="Allow agent actions")
    wf_rules_file: str|None = Field(description="path/to/workflow/rules/file")
    wf_agent_behaviour_file: str|None = Field(description="path/to/agent/behaviour/file")
    wf_agent_sys_prompt_file: str|None = Field(description="path/to/agent/system/prompt/file")
    WF_TAG: str|None = Field(description="TAG of the workflow")
    WORKFLOW_USER : str|None = Field(default="user",description="Name of the user")
    WORKFLOW_TURN : str|None = Field(default="user",description="The entity who's turn is next")
    #WF_RULES: str|None = Field(default=None,description="List of wokflow Rules")
    agent_role_prompt : str = Field(default=FULL_AGENT_ROLE_PROMPT, description="Prompt explaining agent role and WOLF's infrastructure")
    full_schema_string: str = Field(default=FULL_SCHEMA_STRING, description="Prompt showing the agent the schema for the different actions the agent can take")

