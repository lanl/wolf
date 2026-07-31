from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict

from framework.utils.io_tools import read_file, write_file, run_syscall
from framework.workflows.base_agent_action import AgentAction

# --------------
# IO actions (read / write )
# --------------
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
            result = f"""[system][ReadFileAction][error]: problem reading '{self.payload.file_path}':\n 
                                       error message: {action_err}"""
        ## Show results 
        ctx_msg = (f"File content:"
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

    The model-facing schema should advertise ``file_path``.  Older prompts and
    saved sessions may still emit the legacy ``path`` key, so a pre-validator
    maps ``path`` to ``file_path`` for compatibility without using a Pydantic
    alias that would make generated examples show the wrong key.
    """

    model_config = ConfigDict(populate_by_name=True)
    file_path: str = Field(..., description="/path/of/file")
    content: str = Field(description="Text written into the file")
    append: bool = False

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_path_key(cls, data):
        if isinstance(data, dict) and "file_path" not in data and "path" in data:
            data = dict(data)
            data["file_path"] = data.pop("path")
        return data

class WriteFileAction(AgentAction):
    action: Literal["write_file"] = "write_file"
    description: Literal["Action for writing files"] = "Action for writing files"
    payload: WriteFileActionArgs
    payload_schema: str = """
    {"file_path": <string>: "/path/of/file",
     "content": <string>: "Text written into the file",
     "append": <string>: True/False # Append or not to file,
     }
     """
    def execute(self, infra: Any = None) -> Path:
        try:
            result = write_file(
                    file_path=self.payload.file_path,
                    content=self.payload.content,
                    append=self.payload.append,
                    )
        except Exception as action_err:
            result = f"""[WriteFileAction][error]: problem writing '{self.payload.file_path}':\n
                                       error message: {action_err}"""
        ## Show results 
        ctx_msg = f"{result}"
        infra.append_chat_history(
            actor="system",
            content=ctx_msg,
            action={"action": "system_info"},
            log_console=True,
        )
        return

