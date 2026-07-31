"""Streaming interface for real-time bidirectional communication with async orchestration.

Provides WebSocket and Server-Sent Events (SSE) support for:
- Real-time task progress updates
- Bidirectional user-agent communication
- Event stream filtering and replay
- Reconnection handling
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from .events import Event, EventBus


@dataclass(slots=True)
class StreamSubscription:
    """Represents a client subscription to event streams."""
    subscription_id: str
    task_ids: Set[str] = field(default_factory=set)
    event_types: Set[str] = field(default_factory=set)
    queue: asyncio.Queue[Event] = field(default_factory=asyncio.Queue)
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    
    def matches(self, event: Event) -> bool:
        """Check if event matches subscription filters."""
        task_match = not self.task_ids or event.task_id in self.task_ids
        type_match = not self.event_types or event.type in self.event_types
        return task_match and type_match
    
    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()


@dataclass(slots=True)
class EventHistory:
    """Maintains event history for replay on reconnection."""
    max_size: int = 1000
    events: Deque[Event] = field(default_factory=deque)
    events_by_task: Dict[str, Deque[Event]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=100)))
    
    def add(self, event: Event) -> None:
        """Add event to history with size limit."""
        self.events.append(event)
        if len(self.events) > self.max_size:
            self.events.popleft()
        
        self.events_by_task[event.task_id].append(event)
    
    def get_since(self, timestamp: float, task_id: Optional[str] = None) -> List[Event]:
        """Retrieve events since given timestamp."""
        source = self.events_by_task.get(task_id, self.events) if task_id else self.events
        return [e for e in source if e.ts >= timestamp]
    
    def get_last_n(self, n: int, task_id: Optional[str] = None) -> List[Event]:
        """Retrieve last N events."""
        source = self.events_by_task.get(task_id, self.events) if task_id else self.events
        return list(source)[-n:]


class StreamingInterface:
    """Central streaming interface for async workflow runtime.
    
    Provides:
    - Event subscription management
    - Event broadcasting to matched subscriptions
    - Event history and replay
    - Automatic cleanup of stale subscriptions
    """
    
    def __init__(self, event_bus: EventBus, history_size: int = 1000, 
                 subscription_timeout: float = 300.0) -> None:
        """
        Args:
            event_bus: EventBus to subscribe to
            history_size: Maximum events to keep in history
            subscription_timeout: Timeout for inactive subscriptions (seconds)
        """
        self.event_bus = event_bus
        self.history = EventHistory(max_size=history_size)
        self.subscriptions: Dict[str, StreamSubscription] = {}
        self.subscription_timeout = subscription_timeout
        self._cleanup_task: Optional[asyncio.Task] = None
        
        # Register as event bus subscriber
        self.event_bus.subscribe(self._handle_event)
    
    async def start(self) -> None:
        """Start the streaming interface background tasks."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop(self) -> None:
        """Stop background tasks and cleanup."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
    
    async def _handle_event(self, event: Event) -> None:
        """Handle incoming event from event bus."""
        # Add to history
        self.history.add(event)
        
        # Broadcast to matching subscriptions
        for sub in list(self.subscriptions.values()):
            if sub.matches(event):
                try:
                    await sub.queue.put(event)
                    sub.touch()
                except asyncio.QueueFull:
                    # Skip if queue is full (client too slow)
                    pass
    
    async def _cleanup_loop(self) -> None:
        """Periodically cleanup stale subscriptions."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                now = time.time()
                stale = [
                    sub_id for sub_id, sub in self.subscriptions.items()
                    if now - sub.last_activity > self.subscription_timeout
                ]
                for sub_id in stale:
                    await self.unsubscribe(sub_id)
            except asyncio.CancelledError:
                break
    
    def subscribe(self, task_ids: Optional[Set[str]] = None, 
                  event_types: Optional[Set[str]] = None) -> str:
        """Create a new subscription.
        
        Args:
            task_ids: Set of task IDs to filter (None = all tasks)
            event_types: Set of event types to filter (None = all types)
            
        Returns:
            subscription_id: Unique subscription identifier
        """
        subscription_id = str(uuid.uuid4())
        sub = StreamSubscription(
            subscription_id=subscription_id,
            task_ids=task_ids or set(),
            event_types=event_types or set()
        )
        self.subscriptions[subscription_id] = sub
        return subscription_id
    
    async def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription."""
        if subscription_id in self.subscriptions:
            del self.subscriptions[subscription_id]
    
    async def get_events(self, subscription_id: str, timeout: Optional[float] = None) -> Optional[Event]:
        """Get next event for subscription (blocking).
        
        Args:
            subscription_id: Subscription to read from
            timeout: Maximum time to wait (None = wait forever)
            
        Returns:
            Event or None if timeout
        """
        sub = self.subscriptions.get(subscription_id)
        if not sub:
            return None
        
        try:
            if timeout:
                event = await asyncio.wait_for(sub.queue.get(), timeout=timeout)
            else:
                event = await sub.queue.get()
            sub.touch()
            return event
        except asyncio.TimeoutError:
            return None
    
    async def stream_events(self, subscription_id: str):
        """Async generator yielding events for subscription.
        
        Usage:
            async for event in streaming.stream_events(sub_id):
                # Process event
        """
        sub = self.subscriptions.get(subscription_id)
        if not sub:
            return
        
        try:
            while True:
                event = await sub.queue.get()
                sub.touch()
                yield event
        except asyncio.CancelledError:
            pass
    
    def replay_events(self, since_timestamp: Optional[float] = None, 
                      last_n: Optional[int] = None,
                      task_id: Optional[str] = None) -> List[Event]:
        """Replay historical events.
        
        Args:
            since_timestamp: Get events since this timestamp
            last_n: Get last N events (ignored if since_timestamp set)
            task_id: Filter by task ID
            
        Returns:
            List of matching events
        """
        if since_timestamp is not None:
            return self.history.get_since(since_timestamp, task_id)
        elif last_n is not None:
            return self.history.get_last_n(last_n, task_id)
        else:
            return []
    
    def get_subscription_stats(self) -> Dict[str, Any]:
        """Get statistics about active subscriptions."""
        now = time.time()
        return {
            'active_subscriptions': len(self.subscriptions),
            'total_events_in_history': len(self.history.events),
            'subscriptions': [
                {
                    'id': sub.subscription_id[:8],
                    'task_ids': list(sub.task_ids) if sub.task_ids else 'all',
                    'event_types': list(sub.event_types) if sub.event_types else 'all',
                    'queue_size': sub.queue.qsize(),
                    'age_seconds': now - sub.created_at,
                    'inactive_seconds': now - sub.last_activity
                }
                for sub in self.subscriptions.values()
            ]
        }


# Helper functions for common streaming patterns

async def stream_task_progress(streaming: StreamingInterface, task_id: str):
    """Stream progress updates for a specific task.
    
    Usage:
        async for event in stream_task_progress(streaming, task_id):
            if event.type == 'task_completed':
                break
    """
    sub_id = streaming.subscribe(
        task_ids={task_id},
        event_types={'task_progress', 'task_completed', 'task_failed', 'assistant_message'}
    )
    
    try:
        async for event in streaming.stream_events(sub_id):
            yield event
            if event.type in {'task_completed', 'task_failed', 'task_cancelled'}:
                break
    finally:
        await streaming.unsubscribe(sub_id)


async def wait_for_task_completion(streaming: StreamingInterface, task_id: str, 
                                   timeout: Optional[float] = None) -> Optional[Event]:
    """Wait for task completion event.
    
    Args:
        streaming: StreamingInterface instance
        task_id: Task to wait for
        timeout: Maximum time to wait (seconds)
        
    Returns:
        Completion event or None if timeout
    """
    sub_id = streaming.subscribe(
        task_ids={task_id},
        event_types={'task_completed', 'task_failed', 'task_cancelled'}
    )
    
    try:
        start = time.time()
        while True:
            remaining = None if timeout is None else timeout - (time.time() - start)
            if remaining is not None and remaining <= 0:
                return None
            
            event = await streaming.get_events(sub_id, timeout=remaining)
            if event:
                return event
            if remaining is not None:
                return None
    finally:
        await streaming.unsubscribe(sub_id)
