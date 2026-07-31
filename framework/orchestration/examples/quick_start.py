#!/usr/bin/env python3
"""Quick start example for trying the async orchestration observability system.

This example demonstrates:
1. Setting up structured logging
2. Initializing metrics collection
3. Running health checks
4. Monitoring runtime statistics
5. Generating alerts
"""

import asyncio
import time
from pathlib import Path

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from framework.orchestration.logging_config import (
    setup_logging, get_logger, set_correlation_id, set_task_context, 
    TimingLogger, log_event
)
from framework.orchestration.metrics import MetricsCollector, RuntimeMetrics
from framework.orchestration.monitoring import (
    HealthChecker, AlertManager, RuntimeMonitor
)
from framework.orchestration.runtime import AsyncWorkflowRuntime
from framework.orchestration.agent_pool import AgentPool
from framework.orchestration.task_infra import TaskInfrastructureFactory
from framework.orchestration.models import TaskSpec


async def demo_structured_logging():
    """Demonstrate structured logging capabilities."""
    print("\n" + "="*80)
    print("DEMO 1: Structured Logging")
    print("="*80)
    
    # Setup logging with JSON format
    setup_logging(level='INFO', use_json=True)
    logger = get_logger(__name__)
    
    # Set correlation ID for request tracing
    correlation_id = set_correlation_id()
    print(f"\nCorrelation ID: {correlation_id}")
    
    # Log with context
    logger.info("Starting task processing", extra_fields={'stage': 'initialization'})
    
    # Set task context
    set_task_context('demo-task-123', 'demo-agent')
    logger.info("Task context set", extra_fields={'workflow_type': 'generic'})
    
    # Time an operation
    with TimingLogger(logger, 'data_processing', extra_fields={'dataset': 'demo.csv'}):
        await asyncio.sleep(0.5)  # Simulate work
    
    # Log structured event
    log_event(logger, 'task_completed', 
              summary='Demo task completed successfully', confidence=0.95)
    
    print("\nCheck console output above for JSON-formatted logs")


async def demo_metrics_collection():
    """Demonstrate metrics collection and reporting."""
    print("\n" + "="*80)
    print("DEMO 2: Metrics Collection")
    print("="*80)
    
    # Create metrics collector
    collector = MetricsCollector()
    runtime_metrics = RuntimeMetrics(collector)
    
    # Simulate some metrics
    await runtime_metrics.record_task_submitted('task-1', 'generic')
    await asyncio.sleep(0.1)
    await runtime_metrics.record_task_completed('task-1', 0.5, 'generic')
    
    await runtime_metrics.record_task_submitted('task-2', 'chat')
    await runtime_metrics.record_task_failed('task-2', 'chat', 'timeout')
    
    await runtime_metrics.update_task_counts(pending=5, running=2, waiting=1)
    await runtime_metrics.update_agent_stats(total=4, busy=2, idle=2)
    
    # Get metrics summary
    print("\nMetrics Summary:")
    summary = collector.get_summary()
    print(f"  Uptime: {summary['uptime_seconds']:.2f}s")
    print(f"\n  Available metrics:")
    for name, data in summary['metrics'].items():
        if data['current'] is not None:
            print(f"    - {name}: {data['current']}")
    
    # Get throughput stats
    print("\nThroughput Statistics:")
    throughput = runtime_metrics.get_throughput_stats()
    for key, value in throughput.items():
        print(f"    {key}: {value:.3f}")
    
    # Get agent utilization
    utilization = runtime_metrics.get_agent_utilization()
    print(f"\nAgent Utilization: {utilization:.1f}%")
    
    # Export to Prometheus format
    print("\nPrometheus Format Export:")
    print(collector.to_prometheus_format()[:500] + "...\n")


async def demo_health_monitoring():
    """Demonstrate health checks and monitoring."""
    print("\n" + "="*80)
    print("DEMO 3: Health Monitoring & Alerts")
    print("="*80)
    
    # Create a minimal runtime for health checks
    agent_pool = AgentPool([])  # Empty pool for demo
    infra_factory = TaskInfrastructureFactory()
    
    runtime = AsyncWorkflowRuntime(
        agent_pool=agent_pool,
        infra_factory=infra_factory,
        enable_streaming=True,
        enable_websocket=False
    )
    
    await runtime.start()
    
    # Create health checker
    health_checker = HealthChecker(runtime)
    
    # Run health checks
    print("\nRunning health checks...")
    health_statuses = await health_checker.check_all()
    
    print("\nHealth Check Results:")
    for status in health_statuses:
        health_indicator = "✓" if status.healthy else "✗"
        print(f"  {health_indicator} {status.component}: {status.message}")
        if status.details:
            for key, value in status.details.items():
                print(f"      {key}: {value}")
    
    # Alert manager
    alert_manager = AlertManager()
    
    # Generate some alerts
    alert_manager.add_alert(
        severity='warning',
        component='agent_pool',
        message='Agent pool near capacity',
        details={'utilization': 0.95}
    )
    
    alert_manager.add_alert(
        severity='info',
        component='system',
        message='Health check completed',
        details={'checks_passed': len([s for s in health_statuses if s.healthy])}
    )
    
    # Get active alerts
    print("\nActive Alerts:")
    active_alerts = alert_manager.get_active_alerts()
    for alert in active_alerts:
        print(f"  [{alert.severity.upper()}] {alert.component}: {alert.message}")
    
    await runtime.stop()


async def demo_runtime_monitor():
    """Demonstrate comprehensive runtime monitoring."""
    print("\n" + "="*80)
    print("DEMO 4: Comprehensive Runtime Monitoring")
    print("="*80)
    
    # Create runtime
    agent_pool = AgentPool([])  # Empty pool for demo
    infra_factory = TaskInfrastructureFactory()
    
    runtime = AsyncWorkflowRuntime(
        agent_pool=agent_pool,
        infra_factory=infra_factory,
        enable_streaming=True,
        enable_websocket=False
    )
    
    await runtime.start()
    
    # Create runtime monitor
    monitor = RuntimeMonitor(runtime)
    
    # Start background monitoring (with short interval for demo)
    await monitor.start(check_interval=5.0)
    
    print("\nMonitoring started (will run for 10 seconds)...")
    await asyncio.sleep(10)
    
    # Get dashboard data (NOW WITH AWAIT)
    print("\nDashboard Data:")
    dashboard = await monitor.get_dashboard_data()
    
    print("\n  Health Status:")
    for health in dashboard['health']:
        indicator = "✓" if health['healthy'] else "✗"
        print(f"    {indicator} {health['component']}: {health['message']}")
    
    print("\n  Active Alerts:")
    if dashboard['alerts']:
        for alert in dashboard['alerts']:
            print(f"    [{alert['severity'].upper()}] {alert['component']}: {alert['message']}")
    else:
        print("    No active alerts")
    
    print("\n  Throughput:")
    for key, value in dashboard['throughput'].items():
        print(f"    {key}: {value:.3f}")
    
    print(f"\n  Agent Utilization: {dashboard['agent_utilization']:.1f}%")
    
    # Stop monitoring
    await monitor.stop()
    await runtime.stop()


async def main():
    """Run all demos."""
    print("\n" + "#"*80)
    print("# WOLF Async Orchestration Observability Demo")
    print("#"*80)
    
    await demo_structured_logging()
    await asyncio.sleep(1)
    
    await demo_metrics_collection()
    await asyncio.sleep(1)
    
    await demo_health_monitoring()
    await asyncio.sleep(1)
    
    await demo_runtime_monitor()
    
    print("\n" + "#"*80)
    print("# Demo Complete!")
    print("#"*80)
    print("\nNext steps:")
    print("  1. Review the JSON logs above to see structured logging")
    print("  2. Check the metrics summary and Prometheus export format")
    print("  3. Examine health check results and alerts")
    print("  4. Explore the dashboard data structure")
    print("\nTo integrate with your runtime:")
    print("  - Call setup_logging() at startup")
    print("  - Create MetricsCollector and RuntimeMetrics instances")
    print("  - Initialize RuntimeMonitor and call monitor.start()")
    print("  - Use await monitor.get_dashboard_data() for UI integration\n")


if __name__ == "__main__":
    asyncio.run(main())
