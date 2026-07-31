"""Structured logging configuration for async workflow orchestration.

Provides:
- JSON-formatted structured logging
- Correlation IDs for request tracing
- Performance timing instrumentation
- Context-aware logging with task/agent information
- Log level management
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional
import uuid


# Context variables for request tracing
correlation_id_var: ContextVar[Optional[str]] = ContextVar('correlation_id', default=None)
task_id_var: ContextVar[Optional[str]] = ContextVar('task_id', default=None)
agent_name_var: ContextVar[Optional[str]] = ContextVar('agent_name', default=None)


@dataclass(slots=True)
class LogContext:
    """Context information for structured logging."""
    correlation_id: Optional[str] = None
    task_id: Optional[str] = None
    agent_name: Optional[str] = None
    workflow_type: Optional[str] = None
    session_id: Optional[str] = None
    
    @classmethod
    def current(cls) -> 'LogContext':
        """Get current log context from context vars."""
        return cls(
            correlation_id=correlation_id_var.get(),
            task_id=task_id_var.get(),
            agent_name=agent_name_var.get()
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        # Base log entry
        log_entry = {
            'timestamp': time.time(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # Add context information
        context = LogContext.current()
        log_entry.update(context.to_dict())
        
        # Add extra fields from record
        if hasattr(record, 'extra_fields'):
            log_entry.update(record.extra_fields)
        
        # Add exception info if present
        if record.exc_info:
            log_entry['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]),
                'traceback': traceback.format_exception(*record.exc_info)
            }
        
        return json.dumps(log_entry)


class ContextLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that includes context information."""
    
    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """Process log message and kwargs to include context."""
        # Extract extra fields
        extra_fields = kwargs.pop('extra_fields', {})
        
        # Add context to extra
        context = LogContext.current()
        extra_fields.update(context.to_dict())
        
        # Store in record for formatter
        extra = kwargs.get('extra', {})
        extra['extra_fields'] = extra_fields
        kwargs['extra'] = extra
        
        return msg, kwargs


def setup_logging(level: str = 'INFO', 
                  log_file: Optional[str] = None,
                  use_json: bool = True) -> None:
    """Setup structured logging configuration.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for log output
        use_json: Use JSON formatting (default: True)
    """
    # Create formatter
    if use_json:
        formatter = StructuredFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s:%(funcName)s:%(lineno)d - %(message)s'
        )
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> ContextLoggerAdapter:
    """Get a context-aware logger.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        ContextLoggerAdapter instance
    """
    base_logger = logging.getLogger(name)
    return ContextLoggerAdapter(base_logger, {})


def set_correlation_id(correlation_id: Optional[str] = None) -> str:
    """Set correlation ID for request tracing.
    
    Args:
        correlation_id: Correlation ID (generated if None)
        
    Returns:
        The correlation ID that was set
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())
    correlation_id_var.set(correlation_id)
    return correlation_id


def set_task_context(task_id: str, agent_name: Optional[str] = None) -> None:
    """Set task context for logging.
    
    Args:
        task_id: Task ID
        agent_name: Agent name (optional)
    """
    task_id_var.set(task_id)
    if agent_name:
        agent_name_var.set(agent_name)


def clear_context() -> None:
    """Clear logging context."""
    correlation_id_var.set(None)
    task_id_var.set(None)
    agent_name_var.set(None)


class TimingLogger:
    """Context manager for timing operations and logging duration."""
    
    def __init__(self, logger: logging.Logger, operation: str, 
                 level: int = logging.INFO, **extra_fields):
        """Initialize timing logger.
        
        Args:
            logger: Logger instance
            operation: Name of operation being timed
            level: Log level for timing message
            extra_fields: Additional fields to include in log
        """
        self.logger = logger
        self.operation = operation
        self.level = level
        self.extra_fields = extra_fields
        self.start_time: Optional[float] = None
    
    def __enter__(self) -> 'TimingLogger':
        """Start timing."""
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Stop timing and log duration."""
        duration = time.time() - self.start_time
        
        extra_fields = {
            **self.extra_fields,
            'operation': self.operation,
            'duration_seconds': duration,
            'duration_ms': duration * 1000
        }
        
        if exc_type is not None:
            extra_fields['status'] = 'failed'
            extra_fields['error'] = str(exc_val)
        else:
            extra_fields['status'] = 'success'
        
        self.logger.log(
            self.level,
            f"Operation '{self.operation}' completed in {duration:.3f}s",
            extra_fields=extra_fields
        )


def log_event(logger: logging.Logger, event_type: str, level: int = logging.INFO, **fields) -> None:
    """Log a structured event.
    
    Args:
        logger: Logger instance
        event_type: Type of event (e.g., 'task_started', 'agent_leased')
        level: Log level
        fields: Additional event fields
    """
    extra_fields = {
        'event_type': event_type,
        **fields
    }
    
    logger.log(
        level,
        f"Event: {event_type}",
        extra_fields=extra_fields
    )


# Example usage patterns
if __name__ == "__main__":
    # Setup logging
    setup_logging(level='INFO', use_json=True)
    
    # Get logger
    logger = get_logger(__name__)
    
    # Set correlation ID for request tracing
    correlation_id = set_correlation_id()
    print(f"Correlation ID: {correlation_id}")
    
    # Set task context
    set_task_context('task-123', 'agent-alpha')
    
    # Log with context
    logger.info("Task execution started", extra_fields={'stage': 'initialization'})
    
    # Time an operation
    with TimingLogger(logger, 'data_processing', extra_fields={'dataset': 'train.csv'}):
        time.sleep(0.5)  # Simulate work
    
    # Log structured event
    log_event(logger, 'task_completed', logging.INFO, 
              summary='Successfully processed data', confidence=0.95)
    
    # Clear context
    clear_context()
