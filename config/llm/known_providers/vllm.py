from typing import  Literal, Optional, List, Dict
from pydantic import Field

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
class VLLM_LLM_Provider(Base_LLM_Provider):
    name: Literal["vllm"] = "vllm"
    description: Literal["VLLM LLM inference provider"] = "VLLM LLM inference provider"
    port: Optional[int] = Field(default=8000, description="port of the inference endpoint")
    api_version: Optional[str] = Field(default='v1', description="version of the api endpoint")
    endpoints: Optional[Dict[str, List[str]]] = Field(
        default=vllm_known_endpoints,
        description="""Supported API endpoints i.e '
                    chat/completions':['chat_completions','streaming','json','vision','tools']
                    """
                    )
