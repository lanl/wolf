from pydantic import BaseModel, Field, model_validator
from typing import Type, NewType, Optional
import os
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
