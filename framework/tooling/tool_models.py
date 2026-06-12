from pydantic import BaseModel, Field
from typing import NewType, List, Literal, Union, Annotated


class FuncArg(BaseModel):
    arg_name: str = Field(..., description="Name of the argument")
    arg_type: str = Field(..., description="Type of the argument")
    description: str = Field(default="", description="A description of the argument")
    class Config:
        extra = 'forbid'
FuncArgType = NewType("FuncArgType", type[FuncArg])
FuncArgsType = NewType("FuncArgsType", List[FuncArg])

class FuncMeta(BaseModel):
    name: str = Field(..., description="The name of the function")
    description: str = Field(..., description="A detailed description of what the function does")
    args: FuncArgsType = Field(..., description="List of arguments the function accepts")
    purpose: str = Field(default="", description="The intended purpose or goal of the function (optional)")
    body: str = Field(default="", description="The implementation body of the function (optional)")
    return_type: List[str] = Field(default=[], description="List of types returned by the function (optional)")
    tool_type: Literal["python_func", "go_func", "js_func", "java_func", "typescript_func", "lua_func", "c_func", "cpp_func", "fortran_func", "rust_func", "zig_func"] = Field(..., description="The programming language of the function")
    class Config:
        extra = 'forbid'
FuncMetaType = NewType("FuncMetaType", type[FuncMeta])
FuncsMetaType = NewType("FuncsMetaType", List[FuncMeta])

class ScriptMeta(BaseModel):
    name: str = Field(..., description="The name of the script")
    description: str = Field(..., description="A detailed description of what the script does")
    path: str = Field(..., description="The file system path to the script")
    dependencies: List[str] = Field(default=[], description="List of dependencies required by the script")
    result: str = Field(default="", description="The expected or actual result of the script execution")
    args: FuncArgsType = Field(..., description="List of arguments the script accepts")
    purpose: str = Field(default="", description="The intended purpose or goal of the script (optional)")
    tool_type: Literal["python_script", "go_script", "js_script", "java_script", "typescript_script", "lua_script", "shell_script", "c_script", "cpp_script", "fortran_script", "rust_script", "zig_script"] = Field(..., description="The programming language of the script")
    class Config:
        extra = 'forbid'
ScriptMetaType = NewType("ScriptMetaType", type[ScriptMeta])
ScriptsMetaType = NewType("ScriptsMetaType", List[ScriptMeta])

class ExecutableMeta(BaseModel):
    name: str = Field(..., description="The name of the executable")
    description: str = Field(..., description="A detailed description of what the executable does")
    path: str = Field(..., description="The file system path to the executable binary")
    dependencies: List[str] = Field(default=[], description="List of dependencies required by the executable")
    result: str = Field(default="", description="The expected or actual result of the executable execution")
    args: FuncArgsType = Field(..., description="List of arguments the executable accepts")
    purpose: str = Field(default="", description="The intended purpose or goal of the executable (optional)")
    tool_type: Literal["python_executable", "go_executable", "js_executable", "java_executable", "typescript_executable", "lua_executable", "c_executable", "cpp_executable", "fortran_executable", "rust_executable", "zig_executable", "binary"] = Field(..., description="The programming language of the executable")
    class Config:
        extra = 'forbid'
ExecutableMetaType = NewType("ExecutableMetaType", type[ExecutableMeta])
ExecutablesMetaType = NewType("ExecutablesMetaType", List[ExecutableMeta])

class ToolMeta(BaseModel):
    name: str = Field(..., description="The name of the function, script or executable")
    args: FuncArgsType = Field(..., description="List of arguments the function, script or executable accepts")
    description: str = Field(..., description="A detailed description of what the function, script or executable does")
    body: str = Field(default=None, description="The implementation body of the function (optional)")
    purpose: str = Field(default="", description="The intended purpose or goal of the function, script or executable (optional)")
    path: str|None = Field(description="The file system path to the script or executable binary", default=None)
    dependencies: List[str] = Field(default=[], description="List of dependencies required by the script or executable")
    result: str = Field(default="", description="The expected or actual result of the script or executable execution")
    return_type: List[str] = Field(default=[], description="List of types returned by the function (optional)")
    tool_type: Literal["python_func", "go_func", "js_func", "java_func", "typescript_func", "lua_func", "c_func", 
                       "cpp_func", "fortran_func", "rust_func", "zig_func", "python_script", "go_script", "js_script", 
                       "java_script", "typescript_script", "lua_script", "shell_script", "c_script", "cpp_script", "fortran_script",
                       "rust_script", "zig_script", "python_executable", "go_executable", "js_executable", "java_executable", 
                       "typescript_executable", "lua_executable", "c_executable", "cpp_executable", "fortran_executable", 
                       "rust_executable", "zig_executable", "binary"] = Field(..., description="The programming language of the function, script or executable")
ToolMetaType  = NewType("ToolMetaType", type[ToolMeta]) 
ToolsMetaType = NewType("ToolsMetaType", List[ToolMeta])

class ToolCard(BaseModel):
    """
    A lightweight document representation of a ToolMeta
    for indexing and search.
    """
    name: str
    description: str
    purpose: str = ""
    tool_type: str
    args: List[FuncArg] = []
    return_type: List[str] = []
    path: str | None = None
    dependencies: List[str] = []
    result: str = ""

    @classmethod
    def from_meta(cls, meta: ToolMeta) -> "ToolCard":
        """Build a ToolCard from a ToolMeta."""
        return cls(
            name=meta.name,
            description=meta.description,
            purpose=meta.purpose,
            tool_type=meta.tool_type,
            args=meta.args,
            return_type=meta.return_type,
            path=meta.path,
            dependencies=meta.dependencies,
            result=meta.result,
        )

    def to_text(self) -> str:
        """Render into a searchable text string."""
        parts = [
            f"Tool Name: {self.name}",
            f"Description: {self.description}",
            f"Purpose: {self.purpose}",
            f"Type: {self.tool_type}",
        ]
        if self.args:
            parts.append("Arguments:")
            for arg in self.args:
                parts.append(f"  - {arg.arg_name} ({arg.arg_type}): {arg.description}")
        if self.return_type:
            parts.append("Returns: " + ", ".join(self.return_type))
        if self.path:
            parts.append(f"Path: {self.path}")
        if self.dependencies:
            parts.append("Dependencies: " + ", ".join(self.dependencies))
        if self.result:
            parts.append(f"Expected Result: {self.result}")
        return "\n".join(parts)
