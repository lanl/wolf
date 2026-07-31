from typing import Literal, Optional, List
from pydantic import Field, SecretStr
from config.llm.base_provider import Base_LLM_Provider

# --- llama.cpp ---
# llama.cpp server is OpenAI-compatible.
class LlamaCpp_LLM_Provider(Base_LLM_Provider[List[str]]):
    name: Literal["llamacpp"] = "llamacpp"
    description: str = "llama.cpp server inference"
    host: str = "localhost"
    port: Optional[int] = Field(default=8080)
    api_key: Optional[SecretStr] = Field(default=SecretStr("not-required"))
    endpoints: List[str] = Field(default_factory=lambda: ["chat/completions", "completions", "embeddings"])

    def get_client(self):
        return f"LlamaCppClient(url=http://{self.host}:{self.port})"
