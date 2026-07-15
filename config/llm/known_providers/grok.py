from typing import Literal, Optional, List
from pydantic import Field, SecretStr
from config.llm.base_provider import Base_LLM_Provider

# --- Grok (xAI) ---
# Grok uses an OpenAI-compatible API.
class Grok_LLM_Provider(Base_LLM_Provider[List[str]]):
    name: Literal["grok"] = "grok"
    description: str = "xAI Grok LLM Provider"
    host: str = "api.x.ai"
    endpoints: List[str] = Field(default_factory=lambda: ["chat/completions"])

    def get_client(self):
        return f"GrokClient(host={self.host})"
