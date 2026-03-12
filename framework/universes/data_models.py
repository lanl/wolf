from pydantic import BaseModel, Field
from typing import Type, NewType, Any, Dict
#from framework.data_store.data_models import base_vs_params_type, DEFAULT_BASE_VS_PARAMS
#from framework.data_store.data_models import BaseVectorStoreParams
#from framework.knowledgebase.data_models import kb_params_type, KnowledgeBaseParams
#from framework.knowledgebase.knowledge_base import  KBParams

# --------------
# Base Universe data models
# --------------
class BaseUniverseModel(BaseModel):
    name: str = Field(..., description="Name of the Universe")
    host: str = Field(default="http://0.0.0.0", description="Address of the host on which the universe is running")
    port: int = Field(default=9000, description="Port on which the universe is accessible")
    description: str = Field(default="", description="Description of the universe")
    api_version: str|None = Field(default=None, description="Version of api")
    api_token:   str|None = Field(default=None, description="API Access token")
    def get_base_url(self):
        base_url = f"{self.host}"
        if self.port is not None: base_url = f"{base_url}:{self.port}"
        if self.api_version is not None: base_url=f"{base_url}/{api_version}"
        return base_url
base_universe_type = NewType('base_universe_type', Type[BaseUniverseModel])
DEFAULT_BASE_UNIVERSE_MODEL = BaseUniverseModel(name="default_universe")

# --------------
# Base Universe params data models
# --------------
class BaseUniverseParams(BaseModel):
    kbs: Dict[str, Any] | None = Field(default=None, description="Dict key-value pairs of KB name and KB_params")
    tbs: Dict[str, Any] | None = Field(default=None, description="Dict key-value pairs of TB name and TB_params")
    info: BaseUniverseModel | None = Field(default=None, description="Info about the universe, see BaseUniverseModel")
    def get_base_url(self):
        return self.info.get_base_url()

######################## TYPES
base_universe_params_type   = NewType('base_universe_params_type', Type[BaseUniverseParams])
