from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from typing import Literal, Dict, Any

from pydantic import BaseModel, Field

from framework.workflows.base_agent_action import AgentAction
from framework.universes.universe_tools import build_params_from_info, get_base_universe_params

# ---------------------------
# Create Universe Action
# ---------------------------
class CreateUniverseArgs(BaseModel):
    system: str = Field(description="System where the universe will be created, e.g., 'local'")
    name: str = Field(description="Name for the new universe")
    info: dict = Field(default_factory=dict, description="Optional BaseUniverseModel fields (host, port, etc.)")

class CreateUniverseAction(AgentAction):
    """Create, configure and launch a new universe in a separate process, then register it.

    The universe is instantiated, stored in ``infra.UNIVs`` and the FastAPI app is started
    via ``uvicorn`` in a background subprocess. The subprocess handle together with the
    creation parameters and metadata are saved in ``infra.managed_deployments`` for later
    inspection or termination.
    """
    action: Literal["create_universe"] = "create_universe"
    description: Literal["Create, configure and launch a new universe"] = "Create, configure and launch a new universe"
    payload: CreateUniverseArgs
    payload_schema: str = """{\n    \"system\": \"string\",\n    \"name\": \"string\",\n    \"info\": \"optional dict with BaseUniverseModel fields\"\n}"""

    def execute(self, infra) -> None:
        # Build universe parameters from the supplied info dict
        params = build_params_from_info(name=self.payload.name, info_dict=self.payload.info)
        # Instantiate the BaseUniverse (does not start the server yet)
        base_universe = get_base_universe_params(params)
        # Register the universe instance so other actions can use it immediately
        infra.UNIVs[self.payload.name] = base_universe
        # Prepare the command to launch the FastAPI app via uvicorn in a background process.
        cmd = [sys.executable, "-m", "uvicorn", "wolf.framework.universes.base_universe:create_app_default", "--host", "0.0.0.0", "--port", "0"]
        proc = subprocess.Popen(cmd)
        # Store deployment metadata for later management
        infra.managed_deployments[self.payload.name] = {
            "handle": proc,
            "params": params.dict() if hasattr(params, "dict") else params,
            "meta_data": {
                "type": "universe",
                "status": "running",
                "created_at": datetime.utcnow().isoformat(),
                "subprocess_pid": proc.pid,
            },
        }
        # Optionally inform the chat history
        infra.append_chat_history(
            actor="system",
            content=f"Universe '{self.payload.name}' launched (PID {proc.pid}) and registered.",
            action={"action": "create_universe"},
            log_console=True,
        )
        return

# ---------------------------
# List Deployments Action
# ---------------------------
class ListDeploymentsArgs(BaseModel):
    system: str = Field(description="System identifier, e.g., 'local'")

class ListDeploymentsAction(AgentAction):
    """Return a snapshot of ``infra.managed_deployments``.

    The action simply reads the dictionary and writes a formatted message to the
    chat history. No mutation is performed.
    """
    action: Literal["list_deployments"] = "list_deployments"
    description: Literal["List all managed deployments (universes, TBs, KBs, etc.)"] = "List all managed deployments (universes, TBs, KBs, etc.)"
    payload: ListDeploymentsArgs
    payload_schema: str = """{\n    \"system\": \"string\"\n}"""

    def execute(self, infra) -> None:
        deployments: Dict[str, Dict[str, Any]] = getattr(infra, "managed_deployments", {})
        if not deployments:
            msg = "No managed deployments are currently registered."
        else:
            lines = ["Managed Deployments:"]
            for name, info in deployments.items():
                meta = info.get("meta_data", {})
                typ = meta.get("type", "unknown")
                status = meta.get("status", "unknown")
                pid = meta.get("subprocess_pid")
                lines.append(f"- {name}: type={typ}, status={status}, pid={pid}")
            msg = "\n".join(lines)
        infra.append_chat_history(
            actor="system",
            content=msg,
            action={"action": "list_deployments"},
            log_console=True,
        )
        return

# ---------------------------
# Terminate Deployment Action
# ---------------------------
class TerminateDeploymentArgs(BaseModel):
    system: str = Field(description="System identifier, e.g., 'local'")
    name: str = Field(description="Name of the deployment to terminate")
    force: bool = Field(default=False, description="If True, kill the process immediately")

class TerminateDeploymentAction(AgentAction):
    """Terminate a managed deployment.

    For subprocess handles the action sends ``terminate`` (or ``kill`` when ``force``
    is True) and removes the entry from ``infra.managed_deployments``. For other
    object types the entry is simply removed.
    """
    action: Literal["terminate_deployment"] = "terminate_deployment"
    description: Literal["Terminate a managed deployment (universe, TB, KB, etc.)"] = "Terminate a managed deployment (universe, TB, KB, etc.)"
    payload: TerminateDeploymentArgs
    payload_schema: str = """{\n    \"system\": \"string\",\n    \"name\": \"string\",\n    \"force\": \"boolean (optional)\"\n}"""

    def execute(self, infra) -> None:
        name = self.payload.name.strip()
        deployments = getattr(infra, "managed_deployments", {})
        entry = deployments.get(name)
        if not entry:
            infra.append_chat_history(
                actor="system",
                content=f"Deployment '{name}' not found.",
                action={"action": "terminate_deployment"},
                log_console=True,
            )
            return
        handle = entry.get("handle")
        # If the handle is a subprocess.Popen, attempt graceful termination
        if isinstance(handle, subprocess.Popen):
            try:
                if self.payload.force:
                    handle.kill()
                else:
                    handle.terminate()
                handle.wait(timeout=10)
                infra.append_chat_history(
                    actor="system",
                    content=f"Subprocess for deployment '{name}' terminated (force={self.payload.force}).",
                    action={"action": "terminate_deployment"},
                    log_console=True,
                )
            except Exception as e:
                infra.append_chat_history(
                    actor="system",
                    content=f"Error terminating subprocess for '{name}': {e}",
                    action={"action": "terminate_deployment"},
                    log_console=True,
                )
        # Remove the deployment record
        deployments.pop(name, None)
        infra.append_chat_history(
            actor="system",
            content=f"Deployment '{name}' removed from managed_deployments.",
            action={"action": "terminate_deployment"},
            log_console=True,
        )
        return
# ---------------------------
# Create KnowledgeBase Action
# ---------------------------
from typing import Literal, Dict, Any
from pydantic import BaseModel, Field
from framework.knowledgebase.knowledge_base import KnowledgeBase

class CreateKBArgs(BaseModel):
    system: str = Field(description="System where the KB will be created, e.g., 'local'")
    name: str = Field(description="Name for the new KnowledgeBase")
    params: dict = Field(default_factory=dict, description="Optional dict of parameters for KnowledgeBase constructor")

class CreateKBAction(AgentAction):
    """Create a KnowledgeBase instance and register it in ``infra.managed_deployments``.

    The created ``KnowledgeBase`` object is stored under the provided ``name``.
    No external process is started; the object lives in‑process.
    """
    action: Literal["create_kb"] = "create_kb"
    description: Literal["Create and register a KnowledgeBase"] = "Create and register a KnowledgeBase"
    payload: CreateKBArgs
    payload_schema: str = "{\n    \"system\": \"string\",\n    \"name\": \"string\",\n    \"params\": \"optional dict of KnowledgeBase init args\"\n}"

    def execute(self, infra) -> None:
        # Instantiate KnowledgeBase with given params (if any)
        kb = KnowledgeBase(self.payload.params)
        # Store in managed_deployments
        infra.managed_deployments[self.payload.name] = {
            "handle": kb,
            "params": self.payload.params,
            "meta_data": {
                "type": "knowledge_base",
                "status": "ready",
                "created_at": datetime.utcnow().isoformat(),
            },
        }
        infra.append_chat_history(
            actor="system",
            content=f"KnowledgeBase '{self.payload.name}' created and registered.",
            action={"action": "create_kb"},
            log_console=True,
        )
        return

# ---------------------------
# Create ToolBox Action
# ---------------------------
from framework.tooling.toolbox import ToolBox

class CreateToolBoxArgs(BaseModel):
    system: str = Field(description="System where the toolbox will be created, e.g., 'local'")
    name: str = Field(description="Name for the new ToolBox")
    params: dict = Field(default_factory=dict, description="Optional dict of parameters for ToolBox constructor")

class CreateToolBoxAction(AgentAction):
    """Create a ToolBox instance and register it in ``infra.managed_deployments``.

    The created ``ToolBox`` object is stored under the provided ``name``.
    """
    action: Literal["create_toolbox"] = "create_toolbox"
    description: Literal["Create and register a ToolBox"] = "Create and register a ToolBox"
    payload: CreateToolBoxArgs
    payload_schema: str = "{\n    \"system\": \"string\",\n    \"name\": \"string\",\n    \"params\": \"optional dict of ToolBox init args\"\n}"

    def execute(self, infra) -> None:
        # Instantiate ToolBox with given params (if any)
        tb = ToolBox(self.payload.params)
        # Store in managed_deployments
        infra.managed_deployments[self.payload.name] = {
            "handle": tb,
            "params": self.payload.params,
            "meta_data": {
                "type": "toolbox",
                "status": "ready",
                "created_at": datetime.utcnow().isoformat(),
            },
        }
        infra.append_chat_history(
            actor="system",
            content=f"ToolBox '{self.payload.name}' created and registered.",
            action={"action": "create_toolbox"},
            log_console=True,
        )
        return
