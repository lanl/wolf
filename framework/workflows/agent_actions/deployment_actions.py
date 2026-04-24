from __future__ import annotations

import subprocess
import sys
import json
import tempfile
import time
from pathlib import Path
from datetime import datetime
from typing import Literal, Dict, Any
from pydantic import BaseModel, Field

from framework.workflows.base_agent_action import AgentAction
from framework.universes.universe_tools import build_params_from_info, get_base_universe_params
from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams
from framework.knowledgebase.knowledge_base import KnowledgeBase
from framework.tooling.toolbox import ToolBox
from framework.universes.base_universe import run_app

#
# BaseUniver
#

# ---------------------------
# Create Universe Action
# ---------------------------
class CreateUniverseArgs(BaseModel):
    system: str = Field(description="System where the universe will be created, e.g., 'local'")
    name: str = Field(description="Name for the new universe")
    info: dict = Field(
        default_factory=dict,
        description="Optional BaseUniverseModel fields (host, port, name, etc.)",
    )

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
    payload_schema: str = """{
        "system": "name of system the universe is connected to, e.g. 'local'",
        "name": "name of the universe",
        "info": {
            "host": "127.0.0.1",
            "port": 0,
            "description": "...",
            "api_version": null,
            "api_token": null
        }
    }"""

    def execute(self, infra) -> None:
        deployments: Dict[str, Dict[str, Any]] = getattr(infra, "managed_deployments", {})
            
        if self.payload.name in deployments:
            infra.append_chat_history(
                actor="system",
                content=f"Deployment '{self.payload.name}' already exists.",
                action={"action": "create_universe"},
                log_console=True,
            )
            return
        
        info_data = dict(self.payload.info or {})
        info_data.setdefault("name", self.payload.name)
        info_data.setdefault("host", "127.0.0.1")
        info_data.setdefault("port", 0)
            
        try:
            info_instance = BaseUniverseModel(**info_data)
            params = BaseUniverseParams(info=info_instance, kbs=None, tbs=None)
        except Exception as e:
            infra.append_chat_history(
                actor="system",
                content=f"Failed to validate universe parameters for '{self.payload.name}': {e}",
                action={"action": "create_universe"},
                log_console=True,
            )       
            return
                
        infra.UNIVs[self.payload.name] = params

        runtime_dir = Path(tempfile.gettempdir()) / "wolf_universes"
        runtime_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in self.payload.name)

        params_file = runtime_dir / f"{safe_name}_{timestamp}.params.json"
        status_file = runtime_dir / f"{safe_name}_{timestamp}.status.json"
        stdout_file = runtime_dir / f"{safe_name}_{timestamp}.stdout.log"
        stderr_file = runtime_dir / f"{safe_name}_{timestamp}.stderr.log"

        serializable = params.model_dump(mode="json") if hasattr(params, "model_dump") else params.dict()

        with params_file.open("w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)

        stdout_handle = stdout_file.open("a", encoding="utf-8")
        stderr_handle = stderr_file.open("a", encoding="utf-8")

        cmd = [
            sys.executable,
            "-m",
            "framework.universes.run_universe",
            "--params-file",
            str(params_file),
            "--status-file",
            str(status_file),
            "--cors",
            "*",
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
            )
        except Exception as e:
            stdout_handle.close()
            stderr_handle.close()
            infra.append_chat_history(
                actor="system",
                content=f"Failed to launch universe '{self.payload.name}': {e}",
                action={"action": "create_universe"},
                log_console=True,
            )
            return

        time.sleep(1.0)
        return_code = proc.poll()

        meta_data = {
            "type": "universe",
            "system": self.payload.system,
            "status": "running" if return_code is None else "failed",
            "created_at": datetime.utcnow().isoformat(),
            "subprocess_pid": proc.pid,
        }

        if return_code is not None:
            meta_data["return_code"] = return_code

        deployments[self.payload.name] = {
            "handle": proc,
            "params": serializable,
            "params_file": str(params_file),
            "status_file": str(status_file),
            "stdout_file": str(stdout_file),
            "stderr_file": str(stderr_file),
            "meta_data": meta_data,
        }

        if return_code is None:
            infra.append_chat_history(
                actor="system",
                content=(
                    f"Universe '{self.payload.name}' launched "
                    f"(PID {proc.pid}) and registered."
                ),
                action={"action": "create_universe"},
                log_console=True,
            )
        else:
            infra.append_chat_history(
                actor="system",
                content=(
                    f"Universe '{self.payload.name}' failed to launch "
                    f"(PID {proc.pid}, return code {return_code}). "
                    f"See stderr log: {stderr_file}"
                ),
                action={"action": "create_universe"},
                log_console=True,
            )



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
    payload_schema: str = """{"system": "string"}"""

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
                extra = []

                handle = info.get("handle")
                if isinstance(handle, subprocess.Popen):
                    rc = handle.poll()
                    if rc is None:
                        status = "running"
                    else:
                        status = f"exited({rc})"
                    meta["status"] = status

                status_file = info.get("status_file")
                if status_file:
                    try:
                        with open(status_file, "r", encoding="utf-8") as f:
                            runtime_status = json.load(f)

                        runtime_state = runtime_status.get("status")
                        runtime_host = runtime_status.get("host")
                        runtime_port = runtime_status.get("port")
                        runtime_url = runtime_status.get("url")

                        if runtime_state:
                            extra.append(f"runtime_status={runtime_state}")
                        if runtime_host:
                            extra.append(f"host={runtime_host}")
                        if runtime_port is not None:
                            extra.append(f"port={runtime_port}")
                        if runtime_url:
                            extra.append(f"url={runtime_url}")
                    except Exception:
                        pass

                suffix = f", " + ", ".join(extra) if extra else ""
                lines.append(f"- {name}: type={typ}, status={status}, pid={pid}{suffix}")

            msg = "\n".join(lines)

        infra.append_chat_history(
            actor="system",
            content=msg,
            action={"action": "list_deployments"},
            log_console=True,
        )


# ---------------------------
# Terminate Deployment Action
# ---------------------------

class TerminateDeploymentArgs(BaseModel):
    system: str = Field(description="System identifier, e.g., 'local'")
    name: str = Field(description="Name of the deployment to terminate")
    force: bool = Field(default=False, description="If True, kill the process immediately")
    remove_files: bool = Field(default=True, description="If True, delete params/status/log files")

class TerminateDeploymentAction(AgentAction):
    """Terminate a managed deployment.

    For subprocess handles the action sends ``terminate`` (or ``kill`` when ``force``
    is True) and removes the entry from ``infra.managed_deployments``. For other
    object types the entry is simply removed.
    """         
    action: Literal["terminate_deployment"] = "terminate_deployment"
    description: Literal["Terminate a managed deployment (universe, TB, KB, etc.)"] = "Terminate a managed deployment (universe, TB, KB, etc.)"
    payload: TerminateDeploymentArgs
    payload_schema: str = """{
        "system": "string",
        "name": "string",
        "force": "boolean (optional)",
        "remove_files": "boolean (optional)"
    }"""

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
        meta = entry.get("meta_data", {})
        messages = []

        if isinstance(handle, subprocess.Popen):
            try:
                if handle.poll() is None:
                    if self.payload.force:
                        handle.kill()
                    else:
                        handle.terminate()
                    handle.wait(timeout=10)

                rc = handle.poll()
                meta["status"] = f"terminated({rc})" if rc is not None else "terminated"
                messages.append(
                    f"Subprocess for deployment '{name}' terminated (force={self.payload.force})."
                )
            except subprocess.TimeoutExpired:
                messages.append(
                    f"Timeout while terminating subprocess for '{name}'."
                )
                if not self.payload.force:
                    try:
                        handle.kill()
                        handle.wait(timeout=5)
                        rc = handle.poll()
                        meta["status"] = f"killed({rc})"
                        messages.append(f"Subprocess for '{name}' was killed after timeout.")
                    except Exception as e:
                        messages.append(f"Failed to kill subprocess for '{name}': {e}")
            except Exception as e:
                messages.append(f"Error terminating subprocess for '{name}': {e}")

        if self.payload.remove_files:
            for key in ("params_file", "status_file", "stdout_file", "stderr_file"):
                path = entry.get(key)
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception as e:
                        messages.append(f"Could not remove {key} for '{name}': {e}")

        deployments.pop(name, None)
        getattr(infra, "UNIVs", {}).pop(name, None)

        messages.append(f"Deployment '{name}' removed from managed_deployments.")

        infra.append_chat_history(
            actor="system",
            content="\n".join(messages),
            action={"action": "terminate_deployment"},
            log_console=True,
        )

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
        kb = KnowledgeBase(**self.payload.params)
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
        tb = ToolBox(**self.payload.params)
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
