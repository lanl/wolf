"""WOLF Pack - Interactive Agent Gateway Package.

This package provides a backend gateway for connecting multiple UIs
(GUI and TUI) to WOLF agents via WebSocket connections.
"""

from .gateway import WolfGateway, ConnectionManager, Message, SessionInfo

__all__ = [
    'WolfGateway',
    'ConnectionManager',
    'Message',
    'SessionInfo'
]
