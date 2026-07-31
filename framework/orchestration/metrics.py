"""Metrics collection and monitoring for async workflow orchestration.

Provides:
- Real-time metrics collection (task throughput, agent utilization, etc.)
- Prometheus-compatible metrics export
- Performance monitoring and diagnostics
- Custom metric registration
- Time-series data aggregation
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional
import asyncio

from .models import TaskStatus


@dataclass(slots=True)
class MetricValue:
    """Single metric value with timestamp."""
    value: float
    timestamp: float = field(default_factory=time.time)
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class MetricSeries:
    """Time series of metric values."""
    name: str
    metric_type: str  # counter, gauge, histogram, summary
    help_text: str
    values: Deque[MetricValue] = field(default_factory=lambda: deque(maxlen=1000))
    labels: Dict[str, str] = field(default_factory=dict)
    
    def add(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Add a new metric value."""
        merged_labels = {**self.labels, **(labels or {})}
        self.values.append(MetricValue(value=value, labels=merged_labels))
    
    def get_current(self) -> Optional[float]:
        """Get most recent value."""
        return self.values[-1].value if self.values else None
    
    def get_average(self, window_seconds: Optional[float] = None) -> float:
        """Calculate average over time window."""
        if not self.values:
            return 0.0
        
        if window_seconds is None:
            values = [v.value for v in self.values]
        else:
            cutoff = time.time() - window_seconds
            values = [v.value for v in self.values if v.timestamp >= cutoff]
        
        return sum(values) / len(values) if values else 0.0
    
    def get_rate(self, window_seconds: float = 60.0) -> float:
        """Calculate rate (values per second) over time window."""
        cutoff = time.time() - window_seconds
        recent_values = [v for v in self.values if v.timestamp >= cutoff]
        
        if len(recent_values) < 2:
            return 0.0
        
        time_span = recent_values[-1].timestamp - recent_values[0].timestamp
        if time_span == 0:
            return 0.0
        
        return len(recent_values) / time_span


class MetricsCollector:
    """Central metrics collection and aggregation."""
    
    def __init__(self) -> None:
        self.metrics: Dict[str, MetricSeries] = {}
        self._lock = asyncio.Lock()
        self._start_time = time.time()
    
    def register_counter(self, name: str, help_text: str, 
                        labels: Optional[Dict[str, str]] = None) -> None:
        """Register a counter metric (monotonically increasing)."""
        self.metrics[name] = MetricSeries(
            name=name,
            metric_type='counter',
            help_text=help_text,
            labels=labels or {}
        )
    
    def register_gauge(self, name: str, help_text: str,
                      labels: Optional[Dict[str, str]] = None) -> None:
        """Register a gauge metric (can go up or down)."""
        self.metrics[name] = MetricSeries(
            name=name,
            metric_type='gauge',
            help_text=help_text,
            labels=labels or {}
        )
    
    def register_histogram(self, name: str, help_text: str,
                          labels: Optional[Dict[str, str]] = None) -> None:
        """Register a histogram metric (distribution of values)."""
        self.metrics[name] = MetricSeries(
            name=name,
            metric_type='histogram',
            help_text=help_text,
            labels=labels or {}
        )
    
    async def increment(self, name: str, value: float = 1.0,
                       labels: Optional[Dict[str, str]] = None) -> None:
        """Increment a counter metric."""
        async with self._lock:
            if name not in self.metrics:
                self.register_counter(name, f"Auto-registered counter: {name}")
            
            current = self.metrics[name].get_current() or 0.0
            self.metrics[name].add(current + value, labels)
    
    async def set_gauge(self, name: str, value: float,
                       labels: Optional[Dict[str, str]] = None) -> None:
        """Set a gauge metric value."""
        async with self._lock:
            if name not in self.metrics:
                self.register_gauge(name, f"Auto-registered gauge: {name}")
            
            self.metrics[name].add(value, labels)
    
    async def observe(self, name: str, value: float,
                     labels: Optional[Dict[str, str]] = None) -> None:
        """Observe a value for histogram metric."""
        async with self._lock:
            if name not in self.metrics:
                self.register_histogram(name, f"Auto-registered histogram: {name}")
            
            self.metrics[name].add(value, labels)
    
    def get_metric(self, name: str) -> Optional[MetricSeries]:
        """Get a metric series by name."""
        return self.metrics.get(name)
    
    def get_all_metrics(self) -> Dict[str, MetricSeries]:
        """Get all registered metrics."""
        return self.metrics.copy()
    
    def to_prometheus_format(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        
        for name, series in self.metrics.items():
            # Help text
            lines.append(f"# HELP {name} {series.help_text}")
            lines.append(f"# TYPE {name} {series.metric_type}")
            
            # Current value with labels
            current = series.get_current()
            if current is not None:
                if series.labels:
                    label_str = ','.join(f'{k}="{v}"' for k, v in series.labels.items())
                    lines.append(f"{name}{{{label_str}}} {current}")
                else:
                    lines.append(f"{name} {current}")
        
        return '\n'.join(lines)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all metrics."""
        return {
            'uptime_seconds': time.time() - self._start_time,
            'metrics': {
                name: {
                    'type': series.metric_type,
                    'current': series.get_current(),
                    'average_60s': series.get_average(60),
                    'rate_60s': series.get_rate(60) if series.metric_type == 'counter' else None
                }
                for name, series in self.metrics.items()
            }
        }


class RuntimeMetrics:
    """Workflow runtime metrics tracking."""
    
    def __init__(self, collector: MetricsCollector) -> None:
        self.collector = collector
        self._register_metrics()
    
    def _register_metrics(self) -> None:
        """Register standard runtime metrics."""
        # Task metrics
        self.collector.register_counter('tasks_submitted_total', 'Total tasks submitted')
        self.collector.register_counter('tasks_completed_total', 'Total tasks completed')
        self.collector.register_counter('tasks_failed_total', 'Total tasks failed')
        self.collector.register_counter('tasks_cancelled_total', 'Total tasks cancelled')
        
        self.collector.register_gauge('tasks_pending', 'Current pending tasks')
        self.collector.register_gauge('tasks_running', 'Current running tasks')
        self.collector.register_gauge('tasks_waiting', 'Current waiting tasks')
        
        self.collector.register_histogram('task_duration_seconds', 'Task execution duration')
        self.collector.register_histogram('task_queue_time_seconds', 'Time tasks spend in queue')
        
        # Agent metrics
        self.collector.register_gauge('agents_total', 'Total number of agents')
        self.collector.register_gauge('agents_busy', 'Number of busy agents')
        self.collector.register_gauge('agents_idle', 'Number of idle agents')
        self.collector.register_histogram('agent_lease_duration_seconds', 'Agent lease duration')
        
        # Event metrics
        self.collector.register_counter('events_published_total', 'Total events published')
        self.collector.register_gauge('event_queue_size', 'Current event queue size')
        
        # Memory metrics
        self.collector.register_gauge('memory_contexts_active', 'Active memory contexts')
        self.collector.register_gauge('memory_summaries_total', 'Total memory summaries')
        
        # Infrastructure metrics
        self.collector.register_gauge('streaming_subscriptions', 'Active streaming subscriptions')
        self.collector.register_gauge('websocket_connections', 'Active WebSocket connections')
    
    async def record_task_submitted(self, task_id: str, workflow_type: str) -> None:
        """Record task submission."""
        await self.collector.increment('tasks_submitted_total', 
                                      labels={'workflow_type': workflow_type})
    
    async def record_task_completed(self, task_id: str, duration: float, 
                                   workflow_type: str) -> None:
        """Record task completion."""
        await self.collector.increment('tasks_completed_total',
                                      labels={'workflow_type': workflow_type})
        await self.collector.observe('task_duration_seconds', duration,
                                    labels={'workflow_type': workflow_type})
    
    async def record_task_failed(self, task_id: str, workflow_type: str,
                                error_type: str) -> None:
        """Record task failure."""
        await self.collector.increment('tasks_failed_total',
                                      labels={'workflow_type': workflow_type,
                                             'error_type': error_type})
    
    async def record_task_cancelled(self, task_id: str, workflow_type: str) -> None:
        """Record task cancellation."""
        await self.collector.increment('tasks_cancelled_total',
                                      labels={'workflow_type': workflow_type})
    
    async def update_task_counts(self, pending: int, running: int, 
                                waiting: int) -> None:
        """Update current task count gauges."""
        await self.collector.set_gauge('tasks_pending', pending)
        await self.collector.set_gauge('tasks_running', running)
        await self.collector.set_gauge('tasks_waiting', waiting)
    
    async def update_agent_stats(self, total: int, busy: int, idle: int) -> None:
        """Update agent statistics."""
        await self.collector.set_gauge('agents_total', total)
        await self.collector.set_gauge('agents_busy', busy)
        await self.collector.set_gauge('agents_idle', idle)
    
    async def record_agent_lease(self, agent_name: str, duration: float) -> None:
        """Record agent lease duration."""
        await self.collector.observe('agent_lease_duration_seconds', duration,
                                    labels={'agent': agent_name})
    
    async def record_event_published(self, event_type: str) -> None:
        """Record event publication."""
        await self.collector.increment('events_published_total',
                                      labels={'event_type': event_type})
    
    def get_throughput_stats(self) -> Dict[str, float]:
        """Get task throughput statistics."""
        completed = self.collector.get_metric('tasks_completed_total')
        failed = self.collector.get_metric('tasks_failed_total')
        
        return {
            'tasks_per_second_60s': completed.get_rate(60) if completed else 0.0,
            'failures_per_second_60s': failed.get_rate(60) if failed else 0.0,
            'average_duration_60s': self.collector.get_metric('task_duration_seconds').get_average(60)
                                   if 'task_duration_seconds' in self.collector.metrics else 0.0
        }
    
    def get_agent_utilization(self) -> float:
        """Calculate agent utilization percentage."""
        total = self.collector.get_metric('agents_total')
        busy = self.collector.get_metric('agents_busy')
        
        if not total or not busy:
            return 0.0
        
        total_val = total.get_current() or 0
        busy_val = busy.get_current() or 0
        
        return (busy_val / total_val * 100) if total_val > 0 else 0.0


# Decorator for timing function execution
def timed_metric(metric_name: str, collector: MetricsCollector):
    """Decorator to automatically record function execution time."""
    def decorator(func: Callable) -> Callable:
        async def async_wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                duration = time.time() - start
                await collector.observe(metric_name, duration)
        
        def sync_wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration = time.time() - start
                # For sync functions, we can't await, so just add directly
                collector.metrics[metric_name].add(duration)
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    
    return decorator
