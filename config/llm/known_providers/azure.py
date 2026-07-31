from typing import Literal, Optional, List, Dict
from pydantic import Field, SecretStr
from config.llm.base_provider import Base_LLM_Provider

# --- Azure OpenAI ---
class Azure_LLM_Provider(Base_LLM_Provider[List[str]]):
    name: Literal["azure"] = "azure"
    description: str = "Azure OpenAI Service Provider"
    # Azure requires a custom subdomain (e.g., your-resource.openai.azure.com)
    host: str = Field(..., description="Azure OpenAI endpoint URL")
    api_version: str = Field(default="2023-05-15", description="Azure API version")
    deployment_id: str = Field(..., description="The name of the model deployment in Azure")
    
    endpoints: List[str] = Field(
        default_factory=lambda: ["chat/completions", "embeddings"],
        description="Azure OpenAI endpoints"
    )

    def get_client(self):
        print(f"Initializing Azure client for deployment {self.deployment_id}...")
        return "AzureOpenAIClientInstance"
