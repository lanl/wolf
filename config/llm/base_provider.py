from pydantic import BaseModel, Field
from framework.utils.io_tools import clone_dict
from typing import List, Type, NewType, Union, Literal, Optional

class Base_LLM_Provider(BaseModel):
    """Base class for every LLM inference provider.
    """
    name: str = Field(..., description="Discriminator for the different providers")
    host: str = Field(default='localhost', description="address of the inference endpoint")
    port: Optional[int] = Field(default=None, description="port of the inference endpoint")
    api_key: Optional[str] = Field(default=None, description="API key to make inference with")
    api_version: Optional[str] = Field(default=None, description="version of the api endpoint")
    endpoints: Optional[List[str]] = Field(default=None, description="['different','endpoint']")

base_llm_provider = NewType('llm_provider', Type[Base_LLM_Provider])
