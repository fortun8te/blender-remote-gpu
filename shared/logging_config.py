"""Centralized logging configuration for Blender Remote GPU system.

Provides structured JSON logging with:
- Structured output (JSON, plain text)
- Multiple output targets (console, files)
- Log rotation
- Session/operation tracking
- Performance metrics
- Contextual information (session_id, operation_id, module, function)

Usage:
    from shared.logging_config import setup_logging, get_logger

    # Once at startup (in addon __init__.py or server.py):
    setup_logging("remote-gpu.addon", log_file="~/.blender/addon.log")

    # In modules:
    logger = get_logger("module_name")
    logger.info("Message")
    logger.error("Error", extra={"error_code": "CONN_001", "operation_id": op_id})
"""

import logging
import logging.handlers
import json
import sys
import os
from datetime import datetime
from typing import Optional, Dict, Any
import uuid
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Convert log record to JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add extra fields (error codes, operation IDs, etc.)
        if hasattr(record, "error_code"):
            log_data["error_code"] = record.error_code
        if hasattr(record, "operation_id"):
            log_data["operation_id"] = record.operation_id
        if hasattr(record, "session_id"):
            log_data["session_id"] = record.session_id
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms
        if hasattr(record, "extra_data"):
            log_data["extra_data"] = record.extra_data

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        return json.dumps(log_data)


class PlainFormatter(logging.Formatter):
    """Format log records as human-readable text."""

    def format(self, record: logging.LogRecord) -> str:
        """Convert log record to readable text."""
        # Base format
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        parts = [
            f"{timestamp}",
            f"[{record.levelname:8s}]",
            f"{record.name:30s}",
            f"{record.funcName:20s}",
            f"L{record.lineno:4d}",
        ]

        # Add codes/IDs if present
        if hasattr(record, "error_code"):
            parts.append(f"({record.error_code})")
        if hasattr(record, "operation_id"):
            parts.append(f"op={record.operation_id[:8]}")

        parts.append(record.getMessage())

        msg = " ".join(parts)

        # Add exception if present
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)

        return msg


# Global session ID (unique per process)
_SESSION_ID = str(uuid.uuid4())[:8]

# Global logging configuration
_loggers: Dict[str, logging.Logger] = {}
_setup_done = False


def setup_logging(
    name: str = "remote-gpu",
    log_file: Optional[str] = None,
    log_level: str = "INFO",
    use_json: bool = False,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> None:
    """Configure logging for the entire system.

    Args:
        name: Logger name (e.g., "remote-gpu.addon" or "remote-gpu.server")
        log_file: Path to log file (None = no file logging)
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        use_json: Use JSON output format (default: human-readable)
        max_bytes: Rotate log file when it reaches this size
        backup_count: Keep this many rotated log files
    """
    global _setup_done, _SESSION_ID

    if _setup_done:
        return  # Prevent reconfiguration

    _setup_done = True
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Choose formatter
    formatter = JSONFormatter() if use_json else PlainFormatter()

    # Console handler (always)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (if requested)
    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            str(log_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        logging.info(f"Logging to {log_path} (format={'JSON' if use_json else 'text'})")


def get_logger(name: str) -> logging.Logger:
    """Get or create a logger with the given name.

    Args:
        name: Logger name (e.g., "connection", "renderer", "sync")

    Returns:
        Configured logger instance
    """
    if name not in _loggers:
        _loggers[name] = logging.getLogger(f"remote-gpu.{name}")
    return _loggers[name]


class LogContext:
    """Context manager for tracking operation lifecycle with logging.

    Usage:
        with LogContext("render", logger) as ctx:
            ctx.log_info("Starting render")
            # ... do work ...
            # Automatically logs duration and result
    """

    def __init__(
        self,
        operation: str,
        logger: logging.Logger,
        operation_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.operation = operation
        self.logger = logger
        self.operation_id = operation_id or str(uuid.uuid4())[:12]
        self.extra = extra or {}
        self.start_time = None
        self.end_time = None
        self.result = None
        self.error = None

    def __enter__(self):
        """Log operation start."""
        self.start_time = datetime.utcnow()
        self.log_info(f"Starting {self.operation}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Log operation end (success or error)."""
        self.end_time = datetime.utcnow()
        duration_ms = (self.end_time - self.start_time).total_seconds() * 1000

        if exc_type is not None:
            self.error = exc_val
            self.log_error(
                f"{self.operation} failed: {exc_val}",
                duration_ms=duration_ms,
            )
        else:
            self.log_info(
                f"Completed {self.operation}",
                duration_ms=duration_ms,
            )

        return False  # Don't suppress exceptions

    def log_debug(self, msg: str, **kwargs):
        """Log debug message in this operation."""
        self._log(logging.DEBUG, msg, **kwargs)

    def log_info(self, msg: str, **kwargs):
        """Log info message in this operation."""
        self._log(logging.INFO, msg, **kwargs)

    def log_warning(self, msg: str, **kwargs):
        """Log warning message in this operation."""
        self._log(logging.WARNING, msg, **kwargs)

    def log_error(self, msg: str, **kwargs):
        """Log error message in this operation."""
        self._log(logging.ERROR, msg, **kwargs)

    def _log(self, level: int, msg: str, **kwargs):
        """Internal logging with context injection."""
        extra = {
            "operation_id": self.operation_id,
            "session_id": _SESSION_ID,
        }
        extra.update(kwargs)

        # Inject extra fields into logger record
        for key, value in extra.items():
            setattr(logging.getLogger(), key, value)

        self.logger.log(level, msg, extra=extra)


class PerformanceLogger:
    """Track performance metrics (frame time, upload time, etc.)."""

    def __init__(self, logger: logging.Logger, operation: str):
        self.logger = logger
        self.operation = operation
        self.metrics: Dict[str, float] = {}
        self.start_time = datetime.utcnow()

    def mark(self, label: str) -> float:
        """Record elapsed time since start."""
        elapsed = (datetime.utcnow() - self.start_time).total_seconds() * 1000
        self.metrics[label] = elapsed
        return elapsed

    def log_summary(self):
        """Log all collected metrics."""
        if not self.metrics:
            return

        msg = f"Performance summary for {self.operation}: "
        parts = [f"{k}={v:.1f}ms" for k, v in self.metrics.items()]
        msg += ", ".join(parts)

        self.logger.info(msg, extra={"extra_data": self.metrics})


def log_error_with_code(
    logger: logging.Logger,
    operation_id: str,
    error_code: str,
    message: str,
    exception: Optional[Exception] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Log an error with error code tracking.

    Args:
        logger: Logger instance
        operation_id: Unique operation ID
        error_code: Error code (e.g., "CONN_001")
        message: User-friendly message
        exception: Exception instance (if any)
        extra: Additional data to log
    """
    log_extra = {
        "operation_id": operation_id,
        "error_code": error_code,
        "session_id": _SESSION_ID,
    }
    if extra:
        log_extra["extra_data"] = extra

    if exception:
        logger.error(
            f"[{error_code}] {message}",
            exc_info=exception,
            extra=log_extra,
        )
    else:
        logger.error(
            f"[{error_code}] {message}",
            extra=log_extra,
        )


def log_performance(
    logger: logging.Logger,
    operation: str,
    duration_ms: float,
    success: bool = True,
    metrics: Optional[Dict[str, float]] = None,
) -> None:
    """Log performance metrics for an operation.

    Args:
        logger: Logger instance
        operation: Operation name
        duration_ms: Total duration in milliseconds
        success: Whether operation succeeded
        metrics: Additional metrics (frame size, upload speed, etc.)
    """
    status = "succeeded" if success else "failed"
    msg = f"{operation} {status} in {duration_ms:.1f}ms"

    log_extra = {
        "duration_ms": duration_ms,
        "success": success,
    }
    if metrics:
        log_extra["metrics"] = metrics

    logger.info(msg, extra=log_extra)


# Default configuration (can be overridden)
def setup_default_logging() -> None:
    """Set up logging with reasonable defaults."""
    log_file = None

    # Try to use standard log locations
    if sys.platform == "darwin":  # macOS
        log_file = "~/Library/Logs/blender_remote_gpu.log"
    elif sys.platform == "win32":  # Windows
        log_file = "~\\AppData\\Local\\Temp\\blender_remote_gpu.log"
    else:  # Linux
        log_file = "~/.local/share/blender/logs/remote_gpu.log"

    setup_logging(
        name="remote-gpu",
        log_file=log_file,
        log_level="INFO",
        use_json=False,
    )
