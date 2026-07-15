from typing import Literal, Optional, List, Dict
from pydantic import Field, SecretStr
from config.llm.base_provider import Base_LLM_Provider

# --- OpenAI ---
class OpenAI_LLM_Provider(Base_LLM_Provider[List[str]]):
    name: Literal["openai"] = "openai"
    description: str = "Official OpenAI API Provider"
    host: str = "api.openai.com"
    endpoints: List[str] = Field(
        default_factory=lambda: ["chat/completions", "embeddings", "images/generations"],
        description="Standard OpenAI endpoints"
    )

    def get_client(self):
        print(f"Initializing OpenAI client via {self.host}...")
        return "OpenAIClientInstance"


# --- OpenAI Compatible inference Engines---
class OpenAICompatible_LLM_Provider(Base_LLM_Provider[List[str]]):
    """
    Provider for any engine that implements the OpenAI API specification.
    Examples: vLLM, Ollama, LocalAI, LM Studio, Text-Generation-WebUI.
    """
    name: Literal["openai_compatible"] = "openai_compatible"
    description: str = "Generic OpenAI-compatible inference engine"
    
    # Most compatible engines run on localhost by default
    host: str = Field(default="localhost", description="The IP or hostname of the local server")
    port: Optional[int] = Field(default=8000, description="The port (e.g., 8000 for vLLM, 11434 for Ollama)")
    
    # Many local engines don't actually check the API key, but the client library 
    # often requires a non-empty string to avoid errors.
    api_key: Optional[SecretStr] = Field(
        default=SecretStr("not-needed"), 
        description="API key (often a dummy value for local engines)"
    )

    endpoints: List[str] = Field(
        default_factory=lambda: ["chat/completions", "completions", "embeddings", "models"],
        description="Standard OpenAI-compatible endpoints"
    )

    def get_client(self):
        """
        Returns a client configured to point to the custom local endpoint.
        """
        base_url = f"http://{self.host}:{self.port}/v1"
        print(f"Initializing OpenAI-Compatible client at {base_url}...")
        
        # Example implementation using the official openai library:
        # from openai import OpenAI
        # return OpenAI(base_url=base_url, api_key=self.api_key.get_secret_value())
        
        return f"OpenAICompatibleClient(url={base_url})"
