from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Type, NewType, Any, Dict

# --------------
# Base Universe data models
# --------------
class BaseUniverseModel(BaseModel):
    name: str = Field(..., description="Name of the Universe")

    # Bind host, not a URL
    host: str = Field(
        default="127.0.0.1",
        description="Address/interface on which the universe listens",
    )

    # None means: ask the OS for a free port
    port: int = Field(
        default=0,
        description="Port on which the universe is accessible; Defaults to 0, meaning auto-assigned by OS",
    )

    description: str = Field(default="", description="Description of the universe")
    api_version: str | None = Field(default=None, description="Version of API")
    api_token: str | None = Field(default=None, description="API access token")

    def get_base_url(self) -> str:
        scheme = "http"
        base_url = f"{scheme}://{self.host}"

        if self.port != 0:
            base_url = f"{base_url}:{self.port}"

        if self.api_version is not None:
            base_url = f"{base_url}/{self.api_version.lstrip('/')}"

        return base_url
base_universe_type = NewType('base_universe_type', Type[BaseUniverseModel])
DEFAULT_BASE_UNIVERSE_MODEL = BaseUniverseModel(name="default_universe")

# --------------
# Base Universe params data models
# --------------
class BaseUniverseParams(BaseModel):
    kbs: Dict[str, Any] | None = Field(
        default=None,
        description="Dict key-value pairs of KB name and KB_params",
    )
    tbs: Dict[str, Any] | None = Field(
        default=None,
        description="Dict key-value pairs of TB name and TB_params",
    )
    info: BaseUniverseModel | None = Field(
        default=None,
        description="Info about the universe, see BaseUniverseModel",
    )

    def get_base_url(self):
        return self.info.get_base_url()

######################## TYPES
base_universe_params_type   = NewType('base_universe_params_type', Type[BaseUniverseParams])
