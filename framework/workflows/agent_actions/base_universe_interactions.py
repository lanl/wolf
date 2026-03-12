from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
import requests
from framework.workflows.base_agent_action import AgentAction
from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams
from framework.universes.universe_tools import get_universe_info, build_params_from_info, get_base_universe_params

# Default timeout for all HTTP requests
DEFAULT_TIMEOUT = 30


# ===========================
# Base Universe Interaction
# ===========================
class KnownUniversesQueryArgs(BaseModel):
    system: str = Field(description="Name of the system to which the universes are connected to (typically the local system)")

class UniverseDiscoveryArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    #host: str = Field(description="Adress of the machine hosting the universe (e.g., 'localhost', '127.0.0.1',...)")
    #port: int = Field(description="Port on which the universe's API is accessible (e.g., 8000)")
    #api_version: Optional[str] = Field(default=None, description="version of API endpoint if known, otherwise set to None")
    #api_token: Optional[str] = Field(default=None, description="API Auth token if known, otherwise set to None")
    #scheme: str = Field(default='http',description="URL scheme – `http`(default) or `https`")
    #timeout: int = Field(default=5,description="Time before your request times out")

class UniverseInteractionArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to i.e. 'local' for the local system")
    universe: str = Field(description="Name of the universe you are interacting with")

class FindKnownUniversesAction(AgentAction):
    """Get the list of universes known to a system"""
    action: Literal["get_list_known_universes"] = "get_list_known_universes"
    description: Literal["Action to query the list of universes known to a system"] = "Action to query the list of universes known to a system"
    payload: KnownUniversesQueryArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system"} """
    def execute(self, infra) -> Dict[str, Any]:
        result = []
        system_name = self.payload.system.strip().lower()
        if system_name.startswith("https"): system_name = system_name.lstrip('https').lstrip("://")
        if system_name.startswith("http"): system_name = system_name.lstrip('http').lstrip("://")
        if system_name in ['global', 'local', 'localhost','0.0.0.0', '127.0.0.1']:
            for univ in infra.UNIVs.keys():
                try:
                    u_info = infra.UNIVs[univ].info
                    u_host = u_info.host.strip().lower()
                    if u_host.startswith("https"): u_host = u_host.lstrip('https').lstrip("://")
                    if u_host.startswith("http"): u_host = u_host.lstrip('http').lstrip("://")
                    try:
                        u_scheme = u_info.scheme
                    except:
                        u_scheme = None
                    if u_scheme is not None:
                        u_scheme = u_scheme.strip().lower()
                    else:
                        u_scheme = "http" # Setting to default
                    univ_stat = get_universe_info(host=u_host,
                                      port=int(u_info.port),
                                      scheme=u_scheme)
                    result.append({f"{u_info.name}": univ_stat})
                except Exception as e:
                    result.append({f"{u_info.name}": f"UNAVAILABLE: {str(e)}"})
        univ_info = {"system":{self.payload.system}, "universes": result}
        ctx_msg = (f"[RESPONSE] Universes known to system = {self.payload.system}:\n"
                   f"{univ_info}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"},  log_console=True,)
        return

class UniverseInfoAction(AgentAction):
    """Get universe discovery information (KBs, TBs, allowed actions)."""
    action: Literal["universe_info"] = "universe_info"
    description: Literal["Get universe discovery information including available KBs, TBs, and actions"] = "Get universe discovery information including available KBs, TBs, and actions"
    payload: UniverseInteractionArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with"
                              } 
                              """
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"},  log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
            response = requests.get(f"{univ_base_url}/info", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                result = {"error": "Invalid response format", "action": self.action}
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}]: info request results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class UniverseHealthAction(AgentAction):
    """Check universe health status."""
    action: Literal["universe_health"] = "universe_health"
    description: Literal["Check universe health status"] = "Check universe health status"
    payload: UniverseInteractionArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with"
                              }
                              """
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
            response = requests.get(f"{univ_base_url}/health", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                result = {"error": "Invalid response format", "action": self.action}
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ## Show results
        ctx_msg = (f"[UNIVERSES][HEALTH-STAT][QURY][RESULT] for universe = {univ_base_url}:\n"
                   f"{result}"
                   )
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class UniverseStatsAction(AgentAction):
    """Get comprehensive universe statistics."""
    action: Literal["universe_stats"] = "universe_stats"
    description: Literal["Get comprehensive statistics for all KBs and TBs in the universe"] = "Get comprehensive statistics for all KBs and TBs in the universe"
    payload: UniverseInteractionArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with"
                              }
                              """
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}" )
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
            response = requests.get(f"{univ_base_url}/stats", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                result = {"error": "Invalid response format", "action": self.action}
            result = result
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] stats query results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return


class UniverseListToolsAction(AgentAction):
    """List all available tools across all toolboxes."""
    action: Literal["universe_list_tools"] = "universe_list_tools"
    description: Literal["List all available tools across all toolboxes in the universe"] = "List all available tools across all toolboxes in the universe"
    payload: UniverseInteractionArgs
    payload_schema: str = """{"system": <string>: "Name of the system the universes are connected to i.e. 'local' for the local system",
                              "universe": <string>: "Name of the universe you are interacting with"
                              }
                              """
    yield_motion_to: Optional[str] = Field(default=None, description="Entity who's turn is next")

    def execute(self, infra) -> Dict[str, Any]:
        univ_name = self.payload.universe.strip()
        try:
            univ = infra.UNIVs[univ_name]
        except Exception as info_err:
            ctx_msg = (f"[ERROR] finding universe {univ_name}'s info:\n"
                       f"  {info_err}")
            infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
            return
        try:
            univ_base_url = univ.get_base_url()
            response = requests.get(f"{univ_base_url}/tools", timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                result = {"error": "Invalid response format, expected list", "action": self.action}
            result = {"tools": result, "count": len(result)}
        except requests.exceptions.Timeout:
            result = {"error": "Request timed out", "action": self.action}
        except requests.exceptions.RequestException as e:
            result = {"error": f"Request failed: {str(e)}", "action": self.action}
        except Exception as e:
            result = {"error": str(e), "action": self.action}
        ## Show results
        ctx_msg = (f"[Universe: {univ_base_url}] stats query results:\n"
                   f"{result}")
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True,)
        return
