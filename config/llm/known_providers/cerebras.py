from typing import Literal, Optional, List
from pydantic import Field, SecretStr
from config.llm.base_provider import Base_LLM_Provider

# --- Cerebras ---
# Cerebras provides an extremely fast OpenAI-compatible API.
class Cerebras_LLM_Provider(Base_LLM_Provider[List[str]]):
    name: Literal["cerebras"] = "cerebras"
    description: str = "Cerebras Inference API"
    host: str = "api.cerebras.ai"
    endpoints: List[str] = Field(default_factory=lambda: ["chat/completions", "embeddings"])

    def get_client(self):
        return f"CerebrasClient(host={self.host})"

