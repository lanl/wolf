from pydantic import BaseModel, Field
from typing import  Type, NewType
from framework.data_store.data_models import base_vs_params_type, DEFAULT_BASE_VS_PARAMS

# --------------
# KnowledgeBase parameters data models
# --------------
class KnowledgeBaseParams(BaseModel):
    name: str = Field(..., description="Name of the KnowledgeBase")
    vstore_params: base_vs_params_type = Field(DEFAULT_BASE_VS_PARAMS, description='Parameters of the vector store')
    inventory_path: str|None = Field(..., description='Path/to/root/documentation/files')
    vrbz: int = Field(default=0, description="Level of verbosity")
kb_params_type = NewType('kb_params_type', Type[KnowledgeBaseParams])
DEFAULT_KB_PARAMS = KnowledgeBaseParams(name="default_KB", inventory_path=None)
