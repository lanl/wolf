"""Wolf local web GUI package.

The GUI provides a visual workspace with a floating/dockable agent panel,
runtime-native controller helpers, and optional HTTP APIs for external/actionbox
callers.
"""

from framework.gui.runtime import (
    Annotation,
    Dashboard,
    DashboardPanel,
    GuiControllerClient,
    GuiRuntime,
    GuiWorkspaceController,
    WorkspaceApp,
    WorkspaceState,
)
from framework.gui.server import start_gui_server

__all__ = [
    "Annotation",
    "Dashboard",
    "DashboardPanel",
    "GuiControllerClient",
    "GuiRuntime",
    "GuiWorkspaceController",
    "WorkspaceApp",
    "WorkspaceState",
    "start_gui_server",
]
