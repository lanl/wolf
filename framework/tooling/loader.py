from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Dict, Optional, Type


@dataclass
class LoadedImplementation:
    kind: str
    name: str
    module_path: str
    class_name: str
    cls: Type[Any]

    def instantiate(self, params: Optional[Dict[str, Any]] = None) -> Any:
        params = params or {}
        try:
            return self.cls(params)
        except TypeError:
            return self.cls(**params)


TOOLBOX_VERSION_MODULES = {
    "v4": "framework.tooling.custom.toolboxes.v4",
    "toolbox_v4": "framework.tooling.custom.toolboxes.v4",
}

TOOL_VERSION_MODULES = {
    "v4": "framework.tooling.custom.tools.v4",
    "tools_v4": "framework.tooling.custom.tools.v4",
}


def import_symbol(module_path: str, class_name: Optional[str] = None) -> Type[Any]:
    """Import a class/symbol by module and optional class name.

    If class_name is omitted, this tries conventional names used by the
    custom tooling packages.
    """
    mod = importlib.import_module(module_path)
    candidates = []
    if class_name:
        candidates.append(class_name)
    candidates.extend([
        "ToolBoxV4",
        "ToolBox",
        "Toolbox",
        "ToolAdapterRegistry",
        "ToolAdapter",
        "Tool",
    ])
    for name in candidates:
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"No loadable symbol found in {module_path}; tried {candidates}")


def resolve_toolbox_module(name_or_path: str) -> str:
    return TOOLBOX_VERSION_MODULES.get(name_or_path, name_or_path)


def resolve_tool_module(name_or_path: str) -> str:
    return TOOL_VERSION_MODULES.get(name_or_path, name_or_path)


def load_toolbox_class(name_or_path: str = "v4", class_name: Optional[str] = None) -> LoadedImplementation:
    module_path = resolve_toolbox_module(name_or_path)
    cls = import_symbol(module_path, class_name=class_name)
    return LoadedImplementation(
        kind="toolbox",
        name=name_or_path,
        module_path=module_path,
        class_name=cls.__name__,
        cls=cls,
    )


def create_toolbox(name_or_path: str = "v4", params: Optional[Dict[str, Any]] = None, class_name: Optional[str] = None) -> Any:
    return load_toolbox_class(name_or_path, class_name=class_name).instantiate(params=params)


def load_tool_class(name_or_path: str = "v4", class_name: Optional[str] = None) -> LoadedImplementation:
    module_path = resolve_tool_module(name_or_path)
    cls = import_symbol(module_path, class_name=class_name)
    return LoadedImplementation(
        kind="tool",
        name=name_or_path,
        module_path=module_path,
        class_name=cls.__name__,
        cls=cls,
    )


def create_tool(name_or_path: str = "v4", params: Optional[Dict[str, Any]] = None, class_name: Optional[str] = None) -> Any:
    return load_tool_class(name_or_path, class_name=class_name).instantiate(params=params)
