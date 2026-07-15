from pydantic import BaseModel, Field, SecretStr
from typing import List, Optional, Generic, TypeVar
from abc import ABC, abstractmethod

# This TypeVar allows subclasses to define their own structure for 'endpoints'
# (e.g., some might use a List[str], others a Dict[str, List[str]])
T_Endpoints = TypeVar("T_Endpoints")

class Base_LLM_Provider(BaseModel, Generic[T_Endpoints], ABC):
    """
    Base class for every LLM inference provider.
    Inherits from ABC to ensure it cannot be instantiated directly.
    """
    name: str = Field(..., description="Discriminator for the different providers")
    host: str = Field(default='localhost', description="address of the inference endpoint")
    port: Optional[int] = Field(default=None, description="port of the inference endpoint")
    
    # SecretStr prevents the API key from being printed in plain text in logs/console
    api_key_var: Optional[str] = Field(
        default=None, 
        description="ENVIRONMENT VARIABLE name holding the API key"
    )
    api_key: Optional[SecretStr] = Field(
        default=None, 
        description="Actual API key"
    )
    
    api_version: Optional[str] = Field(default=None, description="version of the api endpoint")
    endpoints: T_Endpoints
    capabilities: List[str] = Field(default_factory=list, description="List of capabilities (e.g. 'vision', 'tools')")

    @abstractmethod
    def get_client(self) -> any:
        """
        Abstract method to initialize and return the provider's client.
        Must be implemented by all subclasses.
        """
        pass
