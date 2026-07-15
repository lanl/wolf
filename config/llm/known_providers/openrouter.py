from typing import Literal, Optional, List, Dict
from pydantic import Field, SecretStr
from config.llm.base_provider import Base_LLM_Provider

# --- OpenRouter ---
class OpenRouter_LLM_Provider(Base_LLM_Provider[List[str]]):
    name: Literal["openrouter"] = "openrouter"
    description: str = "OpenRouter Aggregator Provider"
    host: str = "openrouter.ai"
    endpoints: List[str] = Field(
        default_factory=lambda: ["chat/completions"],
        description="OpenRouter uses OpenAI-compatible endpoints"
    )

    def get_client(self):
        print(f"Initializing OpenRouter client via {self.host}...")
        return "OpenRouterClientInstance"
