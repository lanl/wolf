from typing import  Literal
from pydantic import Field

from config.llm.base_provider import Base_LLM_Provider

ollama_known_endpoints = {"chat/completions":["chat_completions","streaming", "json", "reproducible_outputs", "vision", 'tools'], 
                          "completions":["completions","streaming", "json", "reproducible_outputs"],
                          "models":[],
                          "embeddings":[],
                          "images/generations":[],
                          "responses":[]
                          }
class Ollama_LLM_Provider(Base_LLM_Provider):
    name: Literal["ollama"] = "ollama"
    description: Literal["Ollama LLM inference provider"] = "Ollama LLM inference provider"
    port: Optional[int] = Field(default=11434, description="port of the inference endpoint")
    api_version: Optional[str] = Field(default'v1', description="version of the api endpoint")
    endpoints: Optional[List[dict]] = Field(default=ollama_known_endpoints, description="""Supported API endpoints i.e 
                                                                                           'chat/completions':['chat_completions','streaming', 
                                                                                           'json','vision', 'tools'][]
                                                                                        """
                                            )
