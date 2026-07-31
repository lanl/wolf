"""Monitoring and diagnostics for async workflow orchestration.

Provides:
- Health checks for runtime components
- Performance diagnostics
- System status monitoring
- Alerting mechanisms
- Dashboard data aggregation
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
import logging

from .runtime import AsyncWorkflowRuntime
from .metrics import MetricsCollector, RuntimeMetrics
from .models import TaskStatus
from .logging_config import get_logger, set_correlation_id


logger = get_logger(__name__)


@dataclass(slots=True)
class HealthStatus:
    """Health status of a component."""
    component: str
    healthy: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    checked_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class Alert:
    """System alert."""
    severity: str  # info, warning, error, critical
    component: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False


class HealthChecker:
    """Performs health checks on runtime components."""
    
    def __init__(self, runtime: AsyncWorkflowRuntime) -> None:
        self.runtime = runtime
    
    async def check_agent_pool(self) -> HealthStatus:
        """Check agent pool health."""
        try:
            stats = await self.runtime.agent_pool.stats()
            total_agents = len(stats)
            busy_agents = sum(1 for a in stats if a['busy'])
            
            if total_agents == 0:
                return HealthStatus(
                    component='agent_pool',
                    healthy=False,
                    message='No agents available',
                    details={'total': 0, 'busy': 0}
                )
            
            utilization = busy_agents / total_agents if total_agents > 0 else 0
            
            if utilization >= 0.95:
                return HealthStatus(
                    component='agent_pool',
                    healthy=False,
                    message='Agent pool near capacity',
                    details={'total': total_agents, 'busy': busy_agents, 'utilization': utilization}
                )
            
            return HealthStatus(
                component='agent_pool',
                healthy=True,
                message='Agent pool operational',
                details={'total': total_agents, 'busy': busy_agents, 'utilization': utilization}
            )
        
        except Exception as e:
            return HealthStatus(
                component='agent_pool',
                healthy=False,
                message=f'Health check failed: {e}',
                details={'error': str(e)}
            )
    
    async def check_task_repository(self) -> HealthStatus:
        """Check task repository health."""
        try:
            task_count = await self.runtime.repository.count()
            all_tasks = await self.runtime.repository.list()
            
            status_counts = {}
            for status in TaskStatus:
                status_counts[status.value] = sum(1 for t in all_tasks if t.status == status)
            
            stuck_tasks = [
                t for t in all_tasks 
                if t.status == TaskStatus.RUNNING and (time.time() - t.updated_at) > 3600
            ]
            
            if len(stuck_tasks) > 0:
                return HealthStatus(
                    component='task_repository',
                    healthy=False,
                    message=f'{len(stuck_tasks)} tasks may be stuck',
                    details={'total_tasks': task_count, 'status_counts': status_counts, 'stuck_tasks': len(stuck_tasks)}
                )
            
            return HealthStatus(
                component='task_repository',
                healthy=True,
                message='Task repository operational',
                details={'total_tasks': task_count, 'status_counts': status_counts}
            )
        
        except Exception as e:
            return HealthStatus(
                component='task_repository',
                healthy=False,
                message=f'Health check failed: {e}',
                details={'error': str(e)}
            )
    
    async def check_event_bus(self) -> HealthStatus:
        """Check event bus health."""
        try:
            queue_size = self.runtime.event_bus._queue.qsize()
            
            if queue_size > 1000:
                return HealthStatus(
                    component='event_bus',
                    healthy=False,
                    message='Event queue backlog',
                    details={'queue_size': queue_size}
                )
            
            return HealthStatus(
                component='event_bus',
                healthy=True,
                message='Event bus operational',
                details={'queue_size': queue_size}
            )
        
        except Exception as e:
            return HealthStatus(
                component='event_bus',
                healthy=False,
                message=f'Health check failed: {e}',
                details={'error': str(e)}
            )
    
    async def check_streaming(self) -> HealthStatus:
        """Check streaming interface health."""
        if not self.runtime.streaming:
            return HealthStatus(
                component='streaming',
                healthy=True,
                message='Streaming disabled',
                details={'enabled': False}
            )
        
        try:
            stats = self.runtime.streaming.get_subscription_stats()
            
            return HealthStatus(
                component='streaming',
                healthy=True,
                message='Streaming operational',
                details=stats
            )
        
        except Exception as e:
            return HealthStatus(
                component='streaming',
                healthy=False,
                message=f'Health check failed: {e}',
                details={'error': str(e)}
            )
    
    async def check_websocket(self) -> HealthStatus:
        """Check WebSocket server health."""
        if not self.runtime.websocket_server:
            return HealthStatus(
                component='websocket',
                healthy=True,
                message='WebSocket disabled',
                details={'enabled': False}
            )
        
        try:
            stats = self.runtime.websocket_server.get_stats()
            
            return HealthStatus(
                component='websocket',
                healthy=stats['running'],
                message='WebSocket operational' if stats['running'] else 'WebSocket not running',
                details=stats
            )
        
        except Exception as e:
            return HealthStatus(
                component='websocket',
                healthy=False,
                message=f'Health check failed: {e}',
                details={'error': str(e)}
            )
    
    async def check_all(self) -> List[HealthStatus]:
        """Run all health checks."""
        correlation_id = set_correlation_id()
        logger.info("Running health checks", extra_fields={'correlation_id': correlation_id})
        
        checks = [
            self.check_agent_pool(),
            self.check_task_repository(),
            self.check_event_bus(),
            self.check_streaming(),
            self.check_websocket()
        ]
        
        results = await asyncio.gather(*checks, return_exceptions=True)
        
        health_statuses = []
        for result in results:
            if isinstance(result, HealthStatus):
                health_statuses.append(result)
                if not result.healthy:
                    logger.warning(
                        f"Health check failed: {result.component}",
                        extra_fields={
                            'component': result.component,
                            'message': result.message,
                            'details': result.details
                        }
                    )
            else:
                logger.error(
                    f"Health check raised exception: {result}",
                    extra_fields={'exception': str(result)}
                )
        
        return health_statuses


class AlertManager:
    """Manages system alerts."""
    
    def __init__(self, max_alerts: int = 100) -> None:
        self.alerts: List[Alert] = []
        self.max_alerts = max_alerts
        self.alert_callbacks: List[Callable[[Alert], None]] = []
    
    def add_alert(self, severity: str, component: str, message: str,
                  details: Optional[Dict[str, Any]] = None) -> Alert:
        """Add a new alert."""
        alert = Alert(
            severity=severity,
            component=component,
            message=message,
            details=details or {}
        )
        
        self.alerts.append(alert)
        
        # Trim to max alerts
        if len(self.alerts) > self.max_alerts:
            self.alerts = self.alerts[-self.max_alerts:]
        
        # Notify callbacks
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"Alert callback failed: {e}")
        
        # Log alert
        log_level = {
            'info': logging.INFO,
            'warning': logging.WARNING,
            'error': logging.ERROR,
            'critical': logging.CRITICAL
        }.get(severity, logging.INFO)
        
        logger.log(
            log_level,
            f"Alert: {message}",
            extra_fields={
                'alert_severity': severity,
                'alert_component': component,
                'alert_details': details or {}
            }
        )
        
        return alert
    
    def resolve_alert(self, alert: Alert) -> None:
        """Mark an alert as resolved."""
        alert.resolved = True
        logger.info(
            f"Alert resolved: {alert.message}",
            extra_fields={'component': alert.component}
        )
    
    def get_active_alerts(self, severity: Optional[str] = None) -> List[Alert]:
        """Get active (unresolved) alerts."""
        active = [a for a in self.alerts if not a.resolved]
        
        if severity:
            active = [a for a in active if a.severity == severity]
        
        return active
    
    def register_callback(self, callback: Callable[[Alert], None]) -> None:
        """Register a callback for new alerts."""
        self.alert_callbacks.append(callback)


class RuntimeMonitor:
    """Comprehensive runtime monitoring."""
    
    def __init__(self, runtime: AsyncWorkflowRuntime, 
                 metrics_collector: Optional[MetricsCollector] = None) -> None:
        self.runtime = runtime
        self.metrics_collector = metrics_collector or MetricsCollector()
        self.runtime_metrics = RuntimeMetrics(self.metrics_collector)
        self.health_checker = HealthChecker(runtime)
        self.alert_manager = AlertManager()
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self, check_interval: float = 60.0) -> None:
        """Start background monitoring."""
        if self._running:
            return
        
        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(check_interval)
        )
        logger.info("Runtime monitor started")
    
    async def stop(self) -> None:
        """Stop background monitoring."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Runtime monitor stopped")
    
    async def _monitor_loop(self, interval: float) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                await self._perform_monitoring()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitoring loop error: {e}")
                await asyncio.sleep(interval)
    
    async def _perform_monitoring(self) -> None:
        """Perform monitoring checks."""
        # Health checks
        health_statuses = await self.health_checker.check_all()
        
        # Generate alerts for unhealthy components
        for status in health_statuses:
            if not status.healthy:
                self.alert_manager.add_alert(
                    severity='warning',
                    component=status.component,
                    message=status.message,
                    details=status.details
                )
        
        # Update metrics
        all_tasks = await self.runtime.repository.list()
        pending = sum(1 for t in all_tasks if t.status == TaskStatus.PENDING)
        running = sum(1 for t in all_tasks if t.status == TaskStatus.RUNNING)
        waiting = sum(1 for t in all_tasks if t.status == TaskStatus.WAITING)
        
        await self.runtime_metrics.update_task_counts(pending, running, waiting)
        
        # Agent stats
        agent_stats = await self.runtime.agent_pool.stats()
        total_agents = len(agent_stats)
        busy_agents = sum(1 for a in agent_stats if a['busy'])
        idle_agents = total_agents - busy_agents
        
        await self.runtime_metrics.update_agent_stats(total_agents, busy_agents, idle_agents)
    
    async def get_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive dashboard data."""
        health_statuses = await self.health_checker.check_all()
        
        return {
            'metrics': self.metrics_collector.get_summary(),
            'health': [{
                'component': h.component,
                'healthy': h.healthy,
                'message': h.message,
                'details': h.details,
                'checked_at': h.checked_at
            } for h in health_statuses],
            'alerts': [{
                'severity': a.severity,
                'component': a.component,
                'message': a.message,
                'details': a.details,
                'timestamp': a.timestamp,
                'resolved': a.resolved
            } for a in self.alert_manager.get_active_alerts()],
            'throughput': self.runtime_metrics.get_throughput_stats(),
            'agent_utilization': self.runtime_metrics.get_agent_utilization()
        }
