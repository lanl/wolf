import subprocess
from typing import Union, List, Any
from pydantic import BaseModel, Field
from framework.workflows.base_agent_action import AgentAction
from typing import Literal

class SysCallActionArgs(BaseModel):
    command: Union[str, List[str]]
    timeout: int = 30
    shell: bool = False

class SysCallAction(AgentAction):
    action: Literal["run_syscall"] = "run_syscall"
    description: Literal["Action for making syscalls"] = "Action for making syscalls"
    payload: SysCallActionArgs
    payload_schema: str = """
    {"command": <string>: "command to run",
     "timeout": <int>: command timeout = 30,
     "shell": <bool>: True/False
     }
     """
    def execute(self, infra: Any = None) -> Any:
        cmd = self.payload.command
        # Normalize command
        if isinstance(cmd, list):
            cmd_list = cmd
        else:
            cmd_list = [cmd] if not self.payload.shell else cmd
        try:
            result = subprocess.run(
                cmd_list,
                shell=self.payload.shell,
                capture_output=True,
                text=True,
                timeout=self.payload.timeout,
                check=False,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except subprocess.TimeoutExpired as te:
            return {
                "stdout": te.stdout or "",
                "stderr": te.stderr or "",
                "returncode": getattr(te, "returncode", -1),
                "error": f"Timeout after {self.payload.timeout}s"
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "error": f"Exception: {e}"
            }
