"""WebSocket server for real-time bidirectional communication with async orchestration.

Provides:
- WebSocket endpoint for streaming events to clients
- Bidirectional messaging for user input injection
- Task control commands (pause, resume, cancel, retry)
- Connection state management and reconnection support
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    WebSocketServerProtocol = None

from .events import Event
from .streaming import StreamingInterface
from .runtime import AsyncWorkflowRuntime


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ClientConnection:
    """Represents a connected WebSocket client."""
    ws: WebSocketServerProtocol
    client_id: str
    subscription_id: Optional[str] = None
    task_ids: Set[str] = field(default_factory=set)
    event_types: Set[str] = field(default_factory=set)
    connected_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    
    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()


class WebSocketServer:
    """WebSocket server for async workflow runtime.
    
    Provides real-time bidirectional communication:
    - Streams events to connected clients
    - Accepts user input and task control commands
    - Manages client connections and subscriptions
    """
    
    def __init__(self, streaming: StreamingInterface, runtime: AsyncWorkflowRuntime,
                 host: str = '0.0.0.0', port: int = 8765) -> None:
        """
        Args:
            streaming: StreamingInterface for event subscriptions
            runtime: AsyncWorkflowRuntime for task control
            host: Host to bind to
            port: Port to listen on
        """
        if not WEBSOCKETS_AVAILABLE:
            raise ImportError("websockets package is required for WebSocket server")
        
        self.streaming = streaming
        self.runtime = runtime
        self.host = host
        self.port = port
        self.clients: Dict[str, ClientConnection] = {}
        self.server: Optional[Any] = None
        self._running = False
    
    async def start(self) -> None:
        """Start the WebSocket server."""
        self.server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port
        )
        self._running = True
        logger.info(f"WebSocket server started on {self.host}:{self.port}")
    
    async def stop(self) -> None:
        """Stop the WebSocket server."""
        self._running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logger.info("WebSocket server stopped")
    
    async def _handle_client(self, websocket: WebSocketServerProtocol, path: str) -> None:
        """Handle a new client connection."""
        client_id = id(websocket)
        client = ClientConnection(
            ws=websocket,
            client_id=str(client_id)
        )
        self.clients[client.client_id] = client
        
        logger.info(f"Client {client.client_id} connected")
        
        try:
            # Send welcome message
            await websocket.send(json.dumps({
                'type': 'connection',
                'client_id': client.client_id,
                'status': 'connected',
                'timestamp': time.time()
            }))
            
            # Start streaming events to client
            stream_task = asyncio.create_task(self._stream_to_client(client))
            
            # Handle incoming messages from client
            async for message in websocket:
                client.touch()
                await self._handle_message(client, message)
            
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client {client.client_id} disconnected")
        except Exception as e:
            logger.error(f"Error handling client {client.client_id}: {e}")
        finally:
            # Cleanup
            if client.subscription_id:
                await self.streaming.unsubscribe(client.subscription_id)
            del self.clients[client.client_id]
            if 'stream_task' in locals():
                stream_task.cancel()
    
    async def _stream_to_client(self, client: ClientConnection) -> None:
        """Stream events to a client."""
        try:
            # Create subscription if filters are set
            if client.task_ids or client.event_types:
                client.subscription_id = self.streaming.subscribe(
                    task_ids=client.task_ids or None,
                    event_types=client.event_types or None
                )
            else:
                # Subscribe to all events
                client.subscription_id = self.streaming.subscribe()
            
            # Stream events
            while self._running:
                event = await self.streaming.get_events(client.subscription_id, timeout=1.0)
                if event:
                    await self._send_event(client, event)
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error streaming to client {client.client_id}: {e}")
    
    async def _send_event(self, client: ClientConnection, event: Event) -> None:
        """Send an event to a client."""
        try:
            message = {
                'type': 'event',
                'event': {
                    'type': event.type,
                    'task_id': event.task_id,
                    'actor': event.actor,
                    'payload': event.payload,
                    'timestamp': event.ts,
                    'id': event.id
                }
            }
            await client.ws.send(json.dumps(message))
        except Exception as e:
            logger.error(f"Error sending event to client {client.client_id}: {e}")
    
    async def _handle_message(self, client: ClientConnection, message: str) -> None:
        """Handle incoming message from client."""
        try:
            data = json.loads(message)
            msg_type = data.get('type')
            
            if msg_type == 'subscribe':
                await self._handle_subscribe(client, data)
            elif msg_type == 'unsubscribe':
                await self._handle_unsubscribe(client)
            elif msg_type == 'inject_message':
                await self._handle_inject_message(data)
            elif msg_type == 'pause_task':
                await self._handle_pause_task(data)
            elif msg_type == 'resume_task':
                await self._handle_resume_task(data)
            elif msg_type == 'cancel_task':
                await self._handle_cancel_task(data)
            elif msg_type == 'retry_task':
                await self._handle_retry_task(data)
            elif msg_type == 'get_task_detail':
                await self._handle_get_task_detail(client, data)
            elif msg_type == 'replay_events':
                await self._handle_replay_events(client, data)
            elif msg_type == 'ping':
                await client.ws.send(json.dumps({'type': 'pong', 'timestamp': time.time()}))
            else:
                await self._send_error(client, f"Unknown message type: {msg_type}")
        
        except json.JSONDecodeError:
            await self._send_error(client, "Invalid JSON")
        except Exception as e:
            logger.error(f"Error handling message from client {client.client_id}: {e}")
            await self._send_error(client, str(e))
    
    async def _handle_subscribe(self, client: ClientConnection, data: Dict[str, Any]) -> None:
        """Handle subscription request."""
        task_ids = set(data.get('task_ids', []))
        event_types = set(data.get('event_types', []))
        
        # Unsubscribe from existing subscription
        if client.subscription_id:
            await self.streaming.unsubscribe(client.subscription_id)
        
        # Update client filters
        client.task_ids = task_ids
        client.event_types = event_types
        
        # Create new subscription
        client.subscription_id = self.streaming.subscribe(
            task_ids=task_ids or None,
            event_types=event_types or None
        )
        
        await client.ws.send(json.dumps({
            'type': 'subscribed',
            'subscription_id': client.subscription_id,
            'task_ids': list(task_ids) if task_ids else 'all',
            'event_types': list(event_types) if event_types else 'all'
        }))
    
    async def _handle_unsubscribe(self, client: ClientConnection) -> None:
        """Handle unsubscribe request."""
        if client.subscription_id:
            await self.streaming.unsubscribe(client.subscription_id)
            client.subscription_id = None
            client.task_ids.clear()
            client.event_types.clear()
        
        await client.ws.send(json.dumps({'type': 'unsubscribed'}))
    
    async def _handle_inject_message(self, data: Dict[str, Any]) -> None:
        """Handle user message injection."""
        task_id = data.get('task_id')
        content = data.get('content')
        role = data.get('role', 'user')
        wake = data.get('wake', True)
        
        if not task_id or not content:
            raise ValueError("task_id and content are required")
        
        await self.runtime.inject_user_message(task_id, content, role=role, wake=wake)
    
    async def _handle_pause_task(self, data: Dict[str, Any]) -> None:
        """Handle task pause request."""
        task_id = data.get('task_id')
        reason = data.get('reason', 'paused by user via WebSocket')
        
        if not task_id:
            raise ValueError("task_id is required")
        
        await self.runtime.pause_task(task_id, reason=reason)
    
    async def _handle_resume_task(self, data: Dict[str, Any]) -> None:
        """Handle task resume request."""
        task_id = data.get('task_id')
        reason = data.get('reason', 'resumed by user via WebSocket')
        
        if not task_id:
            raise ValueError("task_id is required")
        
        await self.runtime.resume_task(task_id, reason=reason)
    
    async def _handle_cancel_task(self, data: Dict[str, Any]) -> None:
        """Handle task cancellation request."""
        task_id = data.get('task_id')
        reason = data.get('reason', 'cancelled by user via WebSocket')
        
        if not task_id:
            raise ValueError("task_id is required")
        
        await self.runtime.cancel_task(task_id, reason=reason)
    
    async def _handle_retry_task(self, data: Dict[str, Any]) -> None:
        """Handle task retry request."""
        task_id = data.get('task_id')
        reason = data.get('reason', 'retried by user via WebSocket')
        
        if not task_id:
            raise ValueError("task_id is required")
        
        await self.runtime.retry_task(task_id, reason=reason)
    
    async def _handle_get_task_detail(self, client: ClientConnection, data: Dict[str, Any]) -> None:
        """Handle task detail request."""
        task_id = data.get('task_id')
        
        if not task_id:
            raise ValueError("task_id is required")
        
        detail = await self.runtime.get_task_detail(task_id)
        
        await client.ws.send(json.dumps({
            'type': 'task_detail',
            'task_id': task_id,
            'detail': detail
        }))
    
    async def _handle_replay_events(self, client: ClientConnection, data: Dict[str, Any]) -> None:
        """Handle event replay request."""
        since_timestamp = data.get('since_timestamp')
        last_n = data.get('last_n')
        task_id = data.get('task_id')
        
        events = self.streaming.replay_events(
            since_timestamp=since_timestamp,
            last_n=last_n,
            task_id=task_id
        )
        
        await client.ws.send(json.dumps({
            'type': 'replay',
            'events': [
                {
                    'type': e.type,
                    'task_id': e.task_id,
                    'actor': e.actor,
                    'payload': e.payload,
                    'timestamp': e.ts,
                    'id': e.id
                }
                for e in events
            ]
        }))
    
    async def _send_error(self, client: ClientConnection, error: str) -> None:
        """Send error message to client."""
        try:
            await client.ws.send(json.dumps({
                'type': 'error',
                'error': error,
                'timestamp': time.time()
            }))
        except Exception as e:
            logger.error(f"Error sending error to client {client.client_id}: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get server statistics."""
        now = time.time()
        return {
            'running': self._running,
            'host': self.host,
            'port': self.port,
            'active_clients': len(self.clients),
            'clients': [
                {
                    'client_id': c.client_id[:8],
                    'subscription_id': c.subscription_id[:8] if c.subscription_id else None,
                    'task_ids': list(c.task_ids) if c.task_ids else 'all',
                    'event_types': list(c.event_types) if c.event_types else 'all',
                    'connected_seconds': now - c.connected_at,
                    'inactive_seconds': now - c.last_activity
                }
                for c in self.clients.values()
            ]
        }
