from pydantic import BaseModel, Field
from typing import  Type, NewType
#from framework.data_store.data_models import base_vs_params_type, DEFAULT_BASE_VS_PARAMS
from framework.data_store.data_models import EmbeddingParams, VectorStoreParams, DEFAULT_VS_PARAMS

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
    inventory_path: str|None = Field(..., description='(Optional) Path/to/root/documentation/files')
    rebuild_text_vstore: bool = Field(False, description='Flag for rebuilding the vector store by recreating the collection and reuploading the files') 
    vrbz: int = Field(default=0, description="KB Level of verbosity")
kb_params_type = NewType('kb_params_type', Type[KnowledgeBaseParams])
DEFAULT_KB_PARAMS = KnowledgeBaseParams(name="default_KB", vstore_params=DEFAULT_VS_PARAMS, inventory_path=None)
