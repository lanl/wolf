from typing import Literal, Optional, List, Dict
from pydantic import Field
from config.llm.base_provider import Base_LLM_Provider

# Defined as a constant to keep the class definition clean
LITELLM_KNOWN_ENDPOINTS: Dict[str, List[str]] = {
    "chat/completions": ["chat_completions", "streaming", "json", "vision", "tools"],
    "completions": [],
    "converse": [],
    "responses": [],
    "models": [],
    "embeddings": [],
    "images/generations": [],
    "audio/transcriptions": [],
    "audio/translations": [],
    "audio/speech": [],
    "realtime": [],
    "tokenize": [],
    "detokenize": [],
    "pooling": [],
    "classify": [],
    "score": [],
    "rerank": [],
    "v1/rerank": [],
    "v2/rerank": [],
    "a2a": [],
    "assistants": [],
    "batches": [],
    "containers": [],
    "containers/files": [],
    "files": [],
    "fine_tuning": [],
    "evals": [],
    "generateContent": [],
    "guardrails/apply_guardrail": [],
    "invoke": [],
    "interactions": [],
    "images/edits": [],
    "videos": [],
    "vector_stores/{vector_store_id}/files": [],
    "vector_stores - Create Vector Store": [],
    "vector_stores/search - Search Vector Store": [],
    "mcp": [],
    "v1/messages": [],
    "v1/messages/count_tokens": [],
    "moderations": [],
    "ocr": [],
    "rag/ingest": [],
    "rag/query": [],
    "responses/compact": [],
    "search": [],
    "skills": []
}

class LiteLLM_LLM_Provider(Base_LLM_Provider[Dict[str, List[str]]]):
    """LiteLLM LLM inference provider implementation."""
    
    name: Literal["litellm"] = "litellm"
    description: str = "LiteLLM LLM inference provider"
    port: Optional[int] = Field(default=4000, description="port of the inference endpoint")
    
    # We use default_factory to ensure each instance gets its own copy of the dict
    endpoints: Dict[str, List[str]] = Field(
        default_factory=lambda: LITELLM_KNOWN_ENDPOINTS,
        description="Supported API endpoints and their specific attributes"
    )

    def get_client(self):
        """
        Implementation of the client initialization for LiteLLM.
        """
        # Example implementation logic:
        # import litellm
        # litellm.api_base = f"http://{self.host}:{self.port}"
        # return litellm
        print(f"Connecting to LiteLLM at {self.host}:{self.port}...")
        return f"LiteLLMClient({self.host})"
