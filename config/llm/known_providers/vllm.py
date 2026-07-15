from typing import Literal, Optional, List
from pydantic import Field, SecretStr
from config.llm.base_provider import Base_LLM_Provider


vllm_known_endpoints = {"chat/completions":[],
                          "completions":[],
                          "responses":[],
                          "models":[],
                          "embeddings":[],
                          "images/generations":[],
                          "audio/transcriptions":[],
                          "audio/translations":[],
                          "realtime":[],
                          "tokenize":[],
                          "detokenize":[],
                          "pooling":[],
                          "classify":[],
                          "score":[],
                          "rerank":[],
                          "v1/rerank":[],
                          "v2/rerank":[]
                          }
vllm_endpoints = list(vllm_known_endpoints.keys())

# --- vLLM ---
# vLLM is strictly OpenAI-compatible.
class vLLM_LLM_Provider(Base_LLM_Provider[List[str]]):
    name: Literal["vllm"] = "vllm"
    description: str = "vLLM High-throughput inference engine"
    host: str = "localhost"
    port: Optional[int] = Field(default=8000)
    api_key: Optional[SecretStr] = Field(default=SecretStr("token-not-required"))
    endpoints: List[str] = Field(default_factory=lambda: vllm_endpoints)

    def get_client(self):
        return f"vLLMClient(url=http://{self.host}:{self.port}/v1)"

