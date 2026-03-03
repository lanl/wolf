from typing import  Literal
from pydantic import Field

from config.llm.base_provider import Base_LLM_Provider

litlellm_known_endpoints = {"chat/completions":[], 
                            "completions":[],
                            "converse":[],
                            "responses":[],
                            "models":[],
                            "embeddings":[],
                            "images/generations":[],
                            "audio/transcriptions":[],
                            "audio/translations":[],
                            "audio/speech":[],
                            "realtime":[],
                            "tokenize":[],
                            "detokenize":[],
                            "pooling":[],
                            "classify":[],
                            "score":[],
                            "rerank":[],
                            "v1/rerank":[],
                            "v2/rerank":[],
                            "a2a":[],
                            "assistants":[],
                            "batches":[],
                            "containers":[],
                            "containers/files":[],
                            "files":[],
                            "fine_tuning":[],
                            "evals":[],
                            "generateContent":[],
                            "guardrails/apply_guardrail":[],
                            "invoke":[],
                            "interactions":[],
                            "images/edits":[],
                            "videos":[],
                            "vector_stores/{vector_store_id}/files":[],
                            "vector_stores - Create Vector Store":[],
                            "vector_stores/search - Search Vector Store":[],
                            "mcp":[],
                            "v1/messages":[],
                            "v1/messages/count_tokens":[],
                            "moderations":[],
                            "ocr":[],
                            "rag/ingest":[],
                            "rag/query":[],
                            "responses/compact":[],
                            "search":[],
                            "skills":[]
                            }

class VLLM_LLM_Provider(Base_LLM_Provider):
    name: Literal["litellm"] = "litellm"
    description: Literal["LiteLLM LLM inference provider"] = "LiteLLM LLM inference provider"
    port: Optional[int] = Field(default=4000, description="port of the inference endpoint")
    endpoints: Optional[List[dict]] = Field(defaultc=litellm_known_endpoints, description="""Supported API endpoints i.e 
                                                                                             'chat/completions':['chat_completions','streaming', 
                                                                                             'json','vision', 'tools'][]
                                                                                          """
                                            )
