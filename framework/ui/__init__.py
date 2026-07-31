"""WOLF UI Clients Package.

This package provides user interface clients (GUI and TUI) for connecting
to the WOLF agent gateway.
"""

from .tui_client import WolfTUIClient
from .gui_client import WolfGUIClient

__all__ = [
    'WolfTUIClient',
    'WolfGUIClient'
]
