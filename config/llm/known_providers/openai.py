from typing import  Literal
from pydantic import Field

from config.llm.base_provider import Base_LLM_Provider

class OpenAI_LLM_Provider(Base_LLM_Provider):
    name: Literal["openai"] = "openai"
    description: Literal["OpenAI compatible LLM inference provider"] = "OpenAI compatible LLM inference provider"
