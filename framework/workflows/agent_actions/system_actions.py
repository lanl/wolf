from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from framework.workflows.base_agent_action import AgentAction


def _coerce_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return value


class SysCallActionArgs(BaseModel):
    """Payload for run_syscall.

    Backward compatible ``command`` is still accepted. Prefer ``command_args``
    for normal commands and ``script_lines`` for shell scripts to avoid one
    large quote-sensitive command string.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"command_args": ["git", "status", "--short"], "timeout": 30, "shell": False},
                {"command_args": ["/path/to/project/.venv/bin/python", "run.py"], "cwd": "/path/to/example", "env": {"MPLBACKEND": "Agg"}, "timeout": 900, "shell": False},
                {"script_lines": ["set -e", "pwd"], "timeout": 30, "shell": True},
            ]
        },
    )

    command: Union[str, list[str], None] = Field(
        default=None,
        description="Backward-compatible command. Prefer command_args for non-shell commands.",
    )
    command_args: list[str] | None = Field(
        default=None,
        description="Preferred safe argv form for non-shell execution, e.g. ['git', 'status', '--short']",
    )
    script_lines: list[str] | None = Field(
        default=None,
        description="Shell script lines joined with newlines and executed with shell=True",
    )
    timeout: int = Field(default=30, ge=1, le=3600, description="Command timeout in seconds")
    shell: bool = Field(default=False, description="Use shell execution. Prefer false except for script_lines.")
    cwd: str | None = Field(default=None, description="Optional working directory. Prefer this over cd/&& shell composition.")
    env: dict[str, str] | None = Field(default=None, description="Optional environment overrides merged with os.environ, e.g. {MPLBACKEND: Agg}.")

    @model_validator(mode="before")
    @classmethod
    def normalize_scalars(cls, data: Any):
        if isinstance(data, dict):
            data = dict(data)
            if "shell" in data:
                data["shell"] = _coerce_bool(data["shell"])
            if isinstance(data.get("timeout"), str):
                stripped = data["timeout"].strip()
                if stripped.isdigit():
                    data["timeout"] = int(stripped)
            if isinstance(data.get("env"), dict):
                data["env"] = {str(k): str(v) for k, v in data["env"].items()}
        return data

    @model_validator(mode="after")
    def validate_command_source(self):
        provided = [
            self.command is not None,
            self.command_args is not None,
            self.script_lines is not None,
        ]
        if sum(provided) != 1:
            raise ValueError("run_syscall requires exactly one of command, command_args, or script_lines")
        if self.command_args is not None:
            if self.shell:
                raise ValueError("command_args must be used with shell=false")
            if not self.command_args:
                raise ValueError("command_args cannot be empty")
        if self.script_lines is not None and not self.script_lines:
            raise ValueError("script_lines cannot be empty")
        if isinstance(self.command, list):
            if self.shell:
                raise ValueError("list-style command must be used with shell=false")
            if not self.command:
                raise ValueError("list-style command cannot be empty")
        if isinstance(self.command, str) and not self.command.strip():
            raise ValueError("command string cannot be empty")
        if self.cwd is not None and not str(self.cwd).strip():
            raise ValueError("cwd cannot be empty when provided")
        return self

    def subprocess_args(self) -> tuple[str | list[str], bool]:
        if self.command_args is not None:
            return self.command_args, False
        if self.script_lines is not None:
            return "\n".join(self.script_lines), True
        if isinstance(self.command, list):
            return self.command, False
        if isinstance(self.command, str):
            if self.shell:
                return self.command, True
            return shlex.split(self.command), False
        raise ValueError("No syscall command source was provided")


class SysCallAction(AgentAction):
    action: Literal["run_syscall"] = "run_syscall"
    description: Literal["Action for making syscalls"] = "Action for making syscalls"
    payload: SysCallActionArgs
    payload_schema: str = """
    Preferred non-shell payload:
    {"command_args": ["git", "status", "--short"], "timeout": 30, "shell": false}

    Preferred project script/simulation payload without shell composition:
    {"command_args": ["/path/to/project/.venv/bin/python", "run.py"], "cwd": "/path/to/example", "env": {"MPLBACKEND": "Agg"}, "timeout": 900, "shell": false}

    Preferred shell-script payload:
    {"script_lines": ["set -e", "pwd"], "timeout": 30, "shell": true}

    Backward-compatible payload:
    {"command": "pwd", "timeout": 30, "shell": false}

    Provide exactly one of command, command_args, or script_lines. Prefer cwd/env over bash -lc, cd, &&, or inline ENV=... composition.
    """

    def execute(self, infra: Any = None) -> Any:
        try:
            cmd, effective_shell = self.payload.subprocess_args()

            approval_mode = str(getattr(self, "_gateway_syscall_approval_mode", "ask") or "ask")
            approval_reason = str(getattr(self, "_gateway_syscall_approval_reason", "") or "")
            skip_approval = approval_mode == "allow"

            if infra is not None and hasattr(infra, "request_syscall_approval") and not skip_approval:
                command_display = cmd if isinstance(cmd, str) else shlex.join([str(part) for part in cmd])
                risk_hints = ["local subprocess execution"]
                command_display_lower = command_display.lower()
                if effective_shell:
                    risk_hints.append("shell=True")
                if self.payload.script_lines is not None:
                    risk_hints.append("script_lines")
                if self.payload.cwd:
                    risk_hints.append("custom cwd")
                if self.payload.env:
                    risk_hints.append("environment overrides")
                if isinstance(cmd, list) and cmd:
                    base = str(cmd[0]).split("/")[-1]
                    if base in {"bash", "sh", "zsh", "fish", "ksh"} and any(str(p) in {"-c", "-lc", "-ic"} for p in cmd[1:3]):
                        risk_hints.append("shell wrapper")
                        risk_hints.append("inline shell script")
                if approval_reason:
                    risk_hints.append(approval_reason)
                if any(ch in command_display for ch in [";", "&&", "||", "|", "`", "$", ">", "<", "\\n", "\\r"]):
                    risk_hints.append("shell metacharacters/composition")
                if any(tok in command_display_lower for tok in [" if ", " then ", " else ", " fi", " for ", " while "]):
                    risk_hints.append("shell control flow")
                approval_request = {
                    "action": self.action,
                    "payload": self.payload.model_dump(mode="json", exclude_none=True),
                    "resolved_command": cmd,
                    "command_display": command_display,
                    "shell": bool(effective_shell),
                    "timeout": self.payload.timeout,
                    "cwd": self.payload.cwd or os.getcwd(),
                    "env": self.payload.env or {},
                    "purpose": self.purpose,
                    "expectations": self.expectations,
                    "risk_hints": list(dict.fromkeys(risk_hints)),
                }
                approval = infra.request_syscall_approval(approval_request)
                if not approval.get("approved", False):
                    response = {
                        "stdout": "",
                        "stderr": approval.get("reason") or "run_syscall was denied by user approval policy",
                        "returncode": -1,
                        "approved": False,
                        "approval": approval,
                    }
                    ctx_msg = f"** 'Sys_Call' Results: **\n{response}\n"
                    infra.append_chat_history(
                        actor="system",
                        content=ctx_msg,
                        action={"action": "system_info"},
                        log_console=True,
                    )
                    return response

            run_env = None
            if self.payload.env:
                run_env = os.environ.copy()
                run_env.update({str(k): str(v) for k, v in self.payload.env.items()})
            result = subprocess.run(
                cmd,
                shell=effective_shell,
                capture_output=True,
                text=True,
                timeout=self.payload.timeout,
                check=False,
                cwd=self.payload.cwd or None,
                env=run_env,
            )
            response = {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "approved": True,
            }
        except subprocess.TimeoutExpired as te:
            response = {
                "stdout": te.stdout or "",
                "stderr": te.stderr or "",
                "returncode": getattr(te, "returncode", -1),
                "error": f"Timeout after {self.payload.timeout}s",
            }
        except Exception as e:
            response = {
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "error": f"Exception: {e}",
            }
        ctx_msg = f"** 'Sys_Call' Results: **\n{response}\n"
        if infra is not None:
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
        return response
