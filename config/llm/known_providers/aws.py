from typing import Literal, Optional, List, Dict
from pydantic import Field, SecretStr
from config.llm.base_provider import Base_LLM_Provider

# --- AWS Bedrock ---
class AWS_LLM_Provider(Base_LLM_Provider[List[str]]):
    name: Literal["aws"] = "aws"
    description: str = "AWS Bedrock Provider"
    region: str = Field(default="us-east-1", description="AWS Region")
    # AWS usually doesn't use a 'host' in the same way as HTTP APIs, 
    # but we keep it for compatibility.
    host: str = "bedrock.amazonaws.com"
    
    endpoints: List[str] = Field(
        default_factory=lambda: ["invoke_model", "invoke_model_with_response_stream"],
        description="Bedrock runtime endpoints"
    )

    def get_client(self):
        print(f"Initializing AWS Bedrock client in region {self.region}...")
        return "Boto3BedrockClientInstance"
