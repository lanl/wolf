from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from framework.utils.io_tools import read_file, write_file
from framework.workflows.base_agent_action import AgentAction


# --------------
# IO actions (read / write)
# --------------
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


class ReadFileActionArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    file_path: str = Field(..., alias="path", description="/path/of/file")


class ReadFileAction(AgentAction):
    action: Literal["read_file"] = "read_file"
    description: Literal["Action for reading files"] = "Action for reading files"
    payload: ReadFileActionArgs
    payload_schema: str = """{"path": <string>: "/path/of/file"}"""

    def execute(self, infra: Any = None) -> str:
        try:
            result = read_file(file_path=self.payload.file_path)
        except Exception as action_err:
            result = (
                f"[system][ReadFileAction][error]: problem reading '{self.payload.file_path}':\n"
                f"error message: {action_err}"
            )
        ctx_msg = (
            f"File content:"
            f"** [BEGIN] {self.payload.file_path} content:**\n"
            f"{result}\n"
            f"** [END] {self.payload.file_path} content:**"
        )
        infra.append_chat_history(
            actor="system",
            content=ctx_msg,
            action={"action": "system_info"},
            log_console=True,
        )
        return


class WriteFileActionArgs(BaseModel):
    """Payload for write_file.

    Backward-compatible one-shot writes still use ``content``. Safer transports
    are also supported for high-context-pressure turns where one large escaped
    JSON string is brittle:

    - ``content_lines``: list of short strings joined with ``\n``.
    - ``content_base64``: base64 encoded UTF-8 text.

    Older prompts and saved sessions may still emit the legacy ``path`` key, so
    a pre-validator maps ``path`` to ``file_path``.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "file_path": "IMPROVEMENTS/example.md",
                    "content_lines": ["# Title", "", "Body text."],
                    "append": False,
                }
            ]
        },
    )

    file_path: str = Field(..., description="Target file path")
    content: str | None = Field(default=None, description="Text written into the file as one string")
    content_lines: list[str] | None = Field(
        default=None,
        description="Safer transport for multiline text: lines joined with newline characters",
    )
    content_base64: str | None = Field(
        default=None,
        description="Base64 encoded UTF-8 text to write; useful for quote/backslash-heavy content",
    )
    append: bool = Field(default=False, description="Append instead of replacing the target file")

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_and_bool_keys(cls, data: Any):
        if isinstance(data, dict):
            data = dict(data)
            if "file_path" not in data and "path" in data:
                data["file_path"] = data.pop("path")
            if "append" in data:
                data["append"] = _coerce_bool(data["append"])
        return data

    @model_validator(mode="after")
    def validate_exactly_one_content_source(self):
        provided = [
            self.content is not None,
            self.content_lines is not None,
            self.content_base64 is not None,
        ]
        if sum(provided) != 1:
            raise ValueError("write_file requires exactly one of content, content_lines, or content_base64")
        if self.content_base64 is not None:
            try:
                base64.b64decode(self.content_base64.encode("ascii"), validate=True).decode("utf-8")
            except Exception as exc:
                raise ValueError("content_base64 must be valid base64-encoded UTF-8 text") from exc
        return self

    def resolved_content(self) -> str:
        if self.content is not None:
            return self.content
        if self.content_lines is not None:
            return "\n".join(self.content_lines)
        if self.content_base64 is not None:
            return base64.b64decode(self.content_base64.encode("ascii"), validate=True).decode("utf-8")
        raise ValueError("No write_file content source was provided")


class WriteFileAction(AgentAction):
    action: Literal["write_file"] = "write_file"
    description: Literal["Action for writing files"] = "Action for writing files"
    payload: WriteFileActionArgs
    payload_schema: str = """
    Preferred payload for multiline text:
    {"file_path": "path/to/file.md", "content_lines": ["line 1", "line 2"], "append": false}

    Backward-compatible payload:
    {"file_path": "path/to/file.md", "content": "Text written into the file", "append": false}

    Escape-heavy payload:
    {"file_path": "path/to/file.md", "content_base64": "base64-encoded UTF-8 text", "append": false}

    Provide exactly one of content, content_lines, or content_base64.
    """

    def execute(self, infra: Any = None) -> Path:
        try:
            result = write_file(
                file_path=self.payload.file_path,
                content=self.payload.resolved_content(),
                append=self.payload.append,
            )
        except Exception as action_err:
            result = (
                f"[WriteFileAction][error]: problem writing '{self.payload.file_path}':\n"
                f"error message: {action_err}"
            )
        ctx_msg = f"{result}"
        infra.append_chat_history(
            actor="system",
            content=ctx_msg,
            action={"action": "system_info"},
            log_console=True,
        )
        return
