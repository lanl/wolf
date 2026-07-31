univ_name="HPC_001"
univ_description="HPC environment"
univ_host="0.0.0.0"
univ_port=8115
univ_cors=["*"]


from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams
from framework.universes.base_universe import run_app
"""
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
base_universe_type = NewType('base_universe_type', Type[BaseUniverseModel])
DEFAULT_BASE_UNIVERSE_MODEL = BaseUniverseModel(name="default_universe")

# --------------
# Base Universe params data models
# --------------
class BaseUniverseParams(BaseModel):
    kbs: Dict[str, Any] | None = Field(default=None, description="Dict key-value pairs of KB name and KB_params")
    tbs: Dict[str, Any] | None = Field(default=None, description="Dict key-value pairs of TB name and TB_params")
    info: BaseUniverseModel | None = Field(default=None, description="Info about the universe, see BaseUniverseModel")
"""

univ_model = BaseUniverseModel(name=univ_name,
                               host=univ_host,
                               #port=univ_port,
                               description=univ_description
                               )

univ_params = BaseUniverseParams(info=univ_model)

if __name__ == "__main__":
    run_app(params=univ_params, 
            host=univ_host, 
            #port=univ_port, 
            cors=["*"]
            )  # cors=["*"] allows all origins
