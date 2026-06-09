from pydantic import BaseModel, Field, model_validator
from typing import Type, NewType, Optional
import os
from framework.data_store.data_models import EmbeddingParams, VectorStoreParams, DEFAULT_VS_PARAMS, MultimodalEmbeddingParams, MultimodalVectorStoreParams

# --------------
# KnowledgeBase parameters data models
# --------------
class KnowledgeBaseParams(BaseModel):
    name: str = Field(..., description="Name of the KnowledgeBase")
    chunk_size: int = Field(256, description='Number of tokens per chunk')
    chunk_overlap: int = Field(16, description='Number of tokens over which consecutive chinks overlap')
    text_embedding: EmbeddingParams = Field( default=EmbeddingParams(),
                                             description=f""" Parameters of the embedding to use:
                                             {EmbeddingParams.model_fields}""")
    inventory_path: str|None = Field(None, description='(Optional) Path to directory containing documentation files to upload')
    persist_dir: str|None = Field(None, description='(Optional) Path to directory where the KB SQLite inventory database will be stored. Defaults to <inventory_path>/inventory_db if inventory_path is provided, otherwise defaults to <session_dir>/VStore')
    rebuild_text_vstore: bool = Field(False, description='Flag for rebuilding the vector store by recreating the collection and reuploading the files') 
    vrbz: int = Field(default=0, description="KB Level of verbosity")
    
    @model_validator(mode='after')
    def set_persist_dir_default(self):
        """Set persist_dir based on inventory_path if not explicitly provided."""
        if self.persist_dir is None and self.inventory_path is not None:
            # Set persist_dir to <inventory_path>/inventory_db
            self.persist_dir = os.path.join(os.path.expanduser(self.inventory_path), "inventory_db")
        return self

kb_params_type = NewType('kb_params_type', Type[KnowledgeBaseParams])
DEFAULT_KB_PARAMS = KnowledgeBaseParams(name="default_KB", vstore_params=DEFAULT_VS_PARAMS, inventory_path=None, persist_dir=None)

class MultimodalKnowledgeBaseParams(BaseModel):
    name: str = Field(..., description="Name of the KnowledgeBase")
    chunk_size: int = Field(512, description='Number of tokens per chunk')
    chunk_overlap: int = Field(64, description='Number of tokens over which consecutive chunks overlap')
    
    # Multimodal embedding configuration
    embedding: MultimodalEmbeddingParams = Field(
        default_factory=MultimodalEmbeddingParams,
        description=f"""Parameters of the multimodal embedding to use:
                     {MultimodalEmbeddingParams.model_fields}"""
    )
    
    inventory_path: str|None = Field(None, description='(Optional) Path to directory containing documentation files to upload')
    persist_dir: str|None = Field(None, description='(Optional) Path to directory where the KB SQLite inventory database will be stored. Defaults to <inventory_path>/inventory_db if inventory_path is provided, otherwise defaults to <session_dir>/VStore')
    rebuild_vstore: bool = Field(False, description='Flag for rebuilding the vector store by recreating the collection and reuploading the files')
    
    # BM25 and RRF parameters
    use_bm25: bool = Field(True, description='Enable BM25 sparse retrieval')
    use_rrf: bool = Field(True, description='Enable Reciprocal Rank Fusion')
    rrf_k: int = Field(60, description='RRF k parameter')
    
    # Reranker parameters
    use_reranker: bool = Field(False, description='Enable cross-encoder reranking')
    reranker_model: str = Field('cross-encoder/ms-marco-MiniLM-L-6-v2', description='Reranker model name')
    
    # Online fetching
    allow_online: bool = Field(False, description='Allow fetching documents from URLs')
    http_timeout: int = Field(20, description='Timeout for HTTP requests in seconds')
    
    vrbz: int = Field(default=0, description="KB Level of verbosity")

    @model_validator(mode='after')
    def set_persist_dir_default(self):
        """Set persist_dir based on inventory_path if not explicitly provided."""
        if self.persist_dir is None and self.inventory_path is not None:
            # Set persist_dir to <inventory_path>/inventory_db
            self.persist_dir = os.path.join(os.path.expanduser(self.inventory_path), "inventory_db")
        return self

multimodal_kb_params_type = NewType('multimodal_kb_params_type', Type[MultimodalKnowledgeBaseParams])
DEFAULT_MULTIMODAL_KB_PARAMS = MultimodalKnowledgeBaseParams(name="default_multimodal_KB")
