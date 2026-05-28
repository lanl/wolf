from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Type, NewType, Any, Dict, Optional
import socket

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
    
    # SSH configuration for remote deployment
    ssh_config: Optional[Dict[str, str]] = Field(
        default=None,
        description="SSH configuration for remote deployment. Required fields: user, key_path. Optional: remote_python_path, remote_work_dir"
    )

    def get_base_url(self) -> str:
        scheme = "http"
        base_url = f"{scheme}://{self.host}"

        if self.port != 0:
            base_url = f"{base_url}:{self.port}"

        if self.api_version is not None:
            base_url = f"{base_url}/{self.api_version.lstrip('/')}"

        return base_url
    
    def is_remote(self) -> bool:
        """Check if this universe is configured for remote deployment.
        
        A host is considered remote if:
        1. It's not a loopback address (127.0.0.1, localhost, ::1)
        2. It's not the wildcard address (0.0.0.0, ::)
        3. It's not resolvable to a local network interface
        """
        # Normalize host
        host = self.host.strip().lower()
        
        # Check for explicit loopback addresses
        loopback_addresses = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}
        if host in loopback_addresses:
            return False
        
        # Try to resolve the hostname and check if it matches local interfaces
        try:
            # Get IP address for the host
            host_ip = socket.gethostbyname(host)
            
            # Check if it's a loopback address after resolution
            if host_ip.startswith("127."):
                return False
            
            # Get all local IP addresses
            local_hostname = socket.gethostname()
            local_ips = set()
            
            # Get IPs associated with local hostname
            try:
                local_ips.update(socket.gethostbyname_ex(local_hostname)[2])
            except (socket.gaierror, socket.herror):
                pass
            
            # Add loopback
            local_ips.add("127.0.0.1")
            local_ips.add("0.0.0.0")
            
            # Check if resolved IP matches any local interface
            if host_ip in local_ips:
                return False
            
            # If we get here, it's likely a remote host
            return True
            
        except (socket.gaierror, socket.herror, OSError):
            # If we can't resolve the hostname, assume it's remote
            # This errs on the side of caution
            return True
    
    def validate_remote_config(self) -> None:
        """Validate that remote deployments have required SSH config."""
        if self.is_remote():
            if not self.ssh_config:
                raise ValueError(f"Remote host {self.host} requires ssh_config with 'user' and 'key_path'")
            if "user" not in self.ssh_config or "key_path" not in self.ssh_config:
                raise ValueError("ssh_config must contain 'user' and 'key_path' for remote deployment")

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
