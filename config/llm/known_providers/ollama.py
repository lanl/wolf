from typing import Literal, Optional, List
from pydantic import Field, SecretStr
from config.llm.base_provider import Base_LLM_Provider

ollama_known_endpoints = {"chat/completions":["chat_completions","streaming", "json", "reproducible_outputs", "vision", 'tools'],
                          "completions":["completions","streaming", "json", "reproducible_outputs"],
                          "models":[],
                          "embeddings":[],
                          "images/generations":[],
                          "responses":[]
                          }
ollama_endpoints = list(ollama_known_endpoints.keys())

# --- Ollama ---
# Ollama is OpenAI-compatible BUT also has its own native API.
class Ollama_LLM_Provider(Base_LLM_Provider[List[str]]):
    name: Literal["ollama"] = "ollama"
    description: str = "Ollama Local LLM Runner"
    host: str = "localhost"
    port: Optional[int] = Field(default=11434)
    api_key: Optional[SecretStr] = Field(default=SecretStr("ollama"))
    # We include the native ollama endpoints as well as the compatible ones
    endpoints: List[str] = Field(
        default_factory=lambda: ollama_endpoints,
        description="Includes both native Ollama and OpenAI-compatible endpoints"
    )

    def get_client(self):
        return f"OllamaClient(url=http://{self.host}:{self.port})"
