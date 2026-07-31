from typing import Literal, Optional, List, Dict
from pydantic import Field, SecretStr
from config.llm.base_provider import Base_LLM_Provider

# --- Anthropic ---
class Anthropic_LLM_Provider(Base_LLM_Provider[List[str]]):
    name: Literal["anthropic"] = "anthropic"
    description: str = "Anthropic Claude API Provider"
    host: str = "api.anthropic.com"
    endpoints: List[str] = Field(
        default_factory=lambda: ["messages"],
        description="Anthropic uses the /messages endpoint for Claude"
    )

    def get_client(self):
        print(f"Initializing Anthropic client via {self.host}...")
        return "AnthropicClientInstance"
