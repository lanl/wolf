from pydantic import BaseModel, Field
from typing import Type, NewType, Optional, Dict, Any

# EMBEDDINGs
class EmbeddingParams(BaseModel):
    """
    This model is used for data validation and schema generation
    related to vector store embeddings
    """
    embedding_type: str = Field('huggingfaceembeddings', description='Type of embedding')
    model: str = Field('sentence-transformers/all-MiniLM-L6-v2', description='Embedding model')
    n_gpu_layers: int = Field(-1, description='Number of layers to put on GPU, -1 for all layers')
    n_batch: int = Field(21, description='Batch size to GPU')
    LLM_VRBZ: int = Field(0, description='Level of Verbosity of the model')
    class Config:
        # Pydantic V1: use schema_extra. Pydantic V2: use json_schema_extra
        # This adds an extra top-level description to the generated schema
        json_schema_extra = {
            "description": "Main models are the models we use the most"
        }
embedding_params_type = NewType('embedding_params_type', Type[EmbeddingParams])
ALL_MINILM_L6_V2_Embedding_Params = EmbeddingParams()

class MultimodalEmbeddingParams(BaseModel):
    """
    This model is used for data validation and schema generation
    related to multimodal embeddings (text, images, audio, video, tables)
    """
    # Text embedding model
    text_model: str = Field('sentence-transformers/all-MiniLM-L6-v2', description='Text embedding model')
    
    # CLIP model for vision-language tasks
    clip_model: str = Field('ViT-B-32', description='CLIP model for vision-language tasks')
    clip_pretrained: str = Field('openai', description='CLIP pretrained weights')
    
    # Device configuration
    device: Optional[str] = Field(None, description='Device to use (cpu, cuda, mps, etc.)')
    
    # Modality-specific encoder configurations
    image_encoder_cfg: Dict[str, Any] = Field(default_factory=dict, description='Configuration for image encoder')
    audio_encoder_cfg: Dict[str, Any] = Field(default_factory=dict, description='Configuration for audio encoder (Whisper)')
    video_encoder_cfg: Dict[str, Any] = Field(default_factory=dict, description='Configuration for video encoder')
    table_encoder_cfg: Dict[str, Any] = Field(default_factory=dict, description='Configuration for table encoder (DistilBERT)')
    
    class Config:
        json_schema_extra = {
            "description": "Embedding parameters for multimodal vector stores supporting text, images, audio, video, and tables"
        }

multimodal_embedding_params_type = NewType('multimodal_embedding_params_type', Type[MultimodalEmbeddingParams])
DEFAULT_MULTIMODAL_EMBEDDING_PARAMS = MultimodalEmbeddingParams()

# VECTOR STORES
class VectorStoreParams(BaseModel):
    """
    This model is used for data validation and schema generation
    related to vector stores
    """
    #db_name: str = Field(..., description='Name of the db file inside whitch the vectore is stored')
    #persist_directory: str = Field(..., description='Path/where/to/store/the/db/file')
    collection_name: str = Field(..., description="Name of initial data collection")
    chunk_size: int = Field(256, description='Number of tokens per chunk')
    chunk_overlap: int = Field(16, description='Number of tokens over which consecutive chinks overlap')
    embedding: EmbeddingParams = Field(default=ALL_MINILM_L6_V2_Embedding_Params,
                                             description=f""" Parameters of the embedding to use:
                                             {EmbeddingParams.model_fields}""")
    rebuild_vstore: bool = Field(False, description='Flag for rebuilding the vector store by purging the db file and reuploading the files')
    vs_VRBZ: int = Field(0, description='Level of verbosity when operating on the vector store')
    class Config:
        # Pydantic V1: use schema_extra. Pydantic V2: use json_schema_extra
        # This adds an extra top-level description to the generated schema
        json_schema_extra = {
            "description": "Main models are the models we use the most"
        }
vs_params_type = NewType('vs_params_type', Type[VectorStoreParams])
DEFAULT_VS_PARAMS = VectorStoreParams(db_name='default_vs', persist_directory='./', collection_name="main_collection")

class MultimodalVectorStoreParams(BaseModel):
    """
    This model is used for data validation and schema generation
    related to multimodal vector stores
    """
    collection_name: str = Field(..., description="Name of initial data collection")
    chunk_size: int = Field(512, description='Number of tokens per chunk')
    chunk_overlap: int = Field(64, description='Number of tokens over which consecutive chunks overlap')
    persist_directory: str = Field("./chroma_db", description='Path where to store the db file')
    rebuild_vstore: bool = Field(False, description='Flag for rebuilding the vector store by purging the db file and reuploading the files')
    
    # Embedding configuration
    embedding: MultimodalEmbeddingParams = Field(
        default_factory=MultimodalEmbeddingParams,
        description=f"""Parameters of the multimodal embedding to use:
                     {MultimodalEmbeddingParams.model_fields}"""
    )
    
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
    
    vs_VRBZ: int = Field(0, description='Level of verbosity when operating on the vector store')
    
    class Config:
        json_schema_extra = {
            "description": "Parameters for multimodal vector stores with support for text, images, audio, video, and tables"
        }

multimodal_vs_params_type = NewType('multimodal_vs_params_type', Type[MultimodalVectorStoreParams])
DEFAULT_MULTIMODAL_VS_PARAMS = MultimodalVectorStoreParams(collection_name="multimodal_collection")

# --------------
# Vector Store parameters data models
# --------------

class BaseVectorStoreParams(BaseModel):
    embedding_model: str = Field(default="all-MiniLM-L6-v2", description="The text embedding model")
    chunk_size: int = Field(default=512, description="Maximum number of token per chunk when documents are split")
    chunk_overlap: int = Field(default=64, description="Number of tokens over which two consecutive chunks overlapt")
    collection_name: str = Field(default="default_collection", description="Name of the main data collection")
    persist_directory: str = Field(default=None, description="path/to/location where to persist the vector store db file")
    rebuild_vstore: bool  = Field(default=False, description="Flag for ressint the vector store by uploading files")
base_vs_params_type = NewType('base_vs_params_type', Type[BaseVectorStoreParams])
DEFAULT_BASE_VS_PARAMS = BaseVectorStoreParams()
