"""
Structured logging and telemetry system for Blender Remote GPU

Provides:
- JSON-formatted structured logging
- Log rotation (keep last N logs)
- Debug mode with verbose output
- Optional anonymized telemetry collection
- Export functionality for bug reports
"""

import json
import logging
import os
import sys
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
import hashlib
import uuid


class Severity(Enum):
    """Log severity levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogEntry:
    """Single structured log entry."""

    def __init__(self, timestamp: str, severity: str, component: str,
                 message: str, details: Optional[Dict] = None):
        self.timestamp = timestamp
        self.severity = severity
        self.component = component
        self.message = message
        self.details = details or {}
        self.context_id = str(uuid.uuid4())[:8]  # For tracing related events

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "severity": self.severity,
            "component": self.component,
            "message": self.message,
            "details": self.details,
            "context_id": self.context_id
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


class LogRotationHandler:
    """Manages log file rotation and cleanup."""

    def __init__(self, log_dir: str, max_files: int = 10):
        self.log_dir = Path(log_dir)
        self.max_files = max_files
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def cleanup_old_logs(self):
        """Keep only the most recent N log files."""
        log_files = sorted(self.log_dir.glob("*.log"))

        if len(log_files) > self.max_files:
            for old_file in log_files[:-self.max_files]:
                try:
                    old_file.unlink()
                except Exception:
                    pass

    def get_current_log_path(self) -> Path:
        """Get path for today's log file."""
        timestamp = datetime.now().strftime("%Y-%m-%d")
        return self.log_dir / f"remote_gpu_{timestamp}.log"


class StructuredLogger:
    """Structured JSON logger with rotation."""

    def __init__(self, log_dir: Optional[str] = None, debug: bool = False,
                 enable_telemetry: bool = False):
        """
        Initialize logger.

        Args:
            log_dir: Directory for log files. Defaults to user's home/.blender/remote-gpu
            debug: Enable debug-level logging
            enable_telemetry: Allow optional telemetry collection
        """
        if log_dir is None:
            log_dir = str(Path.home() / ".blender" / "remote-gpu" / "logs")

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.debug = debug
        self.enable_telemetry = enable_telemetry
        self.rotation_handler = LogRotationHandler(str(self.log_dir))

        # In-memory log buffer (for current session)
        self.log_buffer: list[LogEntry] = []
        self.buffer_lock = threading.Lock()

        # Session identifier
        self.session_id = str(uuid.uuid4())[:12]
        self.start_time = datetime.now().isoformat()

        # Log initial session info
        self._write_header()

    def _write_header(self):
        """Write session header to log file."""
        header = {
            "event": "session_start",
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "debug_mode": self.debug,
            "telemetry_enabled": self.enable_telemetry,
            "blender_remote_gpu_version": "1.0.4",
            "platform": sys.platform
        }

        log_path = self.rotation_handler.get_current_log_path()
        try:
            with open(log_path, "a") as f:
                f.write(json.dumps(header) + "\n")
        except Exception:
            pass

    def log(self, component: str, message: str, severity: Severity = Severity.INFO,
            details: Optional[Dict] = None):
        """Log a structured message."""
        entry = LogEntry(
            timestamp=datetime.now().isoformat(),
            severity=severity.value,
            component=component,
            message=message,
            details=details or {}
        )

        with self.buffer_lock:
            self.log_buffer.append(entry)

        # Write to file
        log_path = self.rotation_handler.get_current_log_path()
        try:
            with open(log_path, "a") as f:
                f.write(entry.to_json() + "\n")
        except Exception:
            pass

        # Also print if debug enabled
        if self.debug:
            print(f"[{component}] {message}", file=sys.stderr)

    def debug(self, component: str, message: str, details: Optional[Dict] = None):
        """Log debug message."""
        if self.debug:
            self.log(component, message, Severity.DEBUG, details)

    def info(self, component: str, message: str, details: Optional[Dict] = None):
        """Log info message."""
        self.log(component, message, Severity.INFO, details)

    def warning(self, component: str, message: str, details: Optional[Dict] = None):
        """Log warning message."""
        self.log(component, message, Severity.WARNING, details)

    def error(self, component: str, message: str, details: Optional[Dict] = None):
        """Log error message."""
        self.log(component, message, Severity.ERROR, details)

    def critical(self, component: str, message: str, details: Optional[Dict] = None):
        """Log critical message."""
        self.log(component, message, Severity.CRITICAL, details)

    def log_connection_event(self, connected: bool, host: str, port: int, duration: float,
                           gpu_name: str = "", vram_free: int = 0, error: str = ""):
        """Log connection event."""
        details = {
            "host": host,
            "port": port,
            "duration_ms": round(duration * 1000, 2),
            "gpu_name": gpu_name,
            "vram_free_mb": vram_free,
        }
        if error:
            details["error"] = error

        severity = Severity.INFO if connected else Severity.ERROR
        message = f"Connection {'established' if connected else 'failed'}"

        self.log("Connection", message, severity, details)

    def log_render_event(self, stage: str, duration: float, success: bool,
                        scene_size: int = 0, output_size: int = 0, error: str = ""):
        """Log render event."""
        details = {
            "stage": stage,
            "duration_ms": round(duration * 1000, 2),
            "scene_size_bytes": scene_size,
            "output_size_bytes": output_size,
        }
        if error:
            details["error"] = error

        severity = Severity.INFO if success else Severity.ERROR
        message = f"Render {stage} {'completed' if success else 'failed'}"

        self.log("Render", message, severity, details)

    def log_network_event(self, event_type: str, data_size: int = 0,
                         latency_ms: float = 0, error: str = ""):
        """Log network event."""
        details = {
            "event_type": event_type,
            "data_size_bytes": data_size,
            "latency_ms": round(latency_ms, 2),
        }
        if error:
            details["error"] = error

        severity = Severity.WARNING if error else Severity.DEBUG
        self.log("Network", f"Network {event_type}", severity, details)

    def get_session_log(self) -> Dict[str, Any]:
        """Get complete session log."""
        with self.buffer_lock:
            entries = [entry.to_dict() for entry in self.log_buffer]

        return {
            "session_id": self.session_id,
            "start_time": self.start_time,
            "end_time": datetime.now().isoformat(),
            "total_entries": len(entries),
            "entries": entries
        }

    def export_session_log(self, path: Optional[str] = None) -> str:
        """Export session log to file."""
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = str(self.log_dir / f"session_export_{timestamp}.json")

        log_data = self.get_session_log()

        Path(path).write_text(json.dumps(log_data, indent=2))
        return path

    def get_statistics(self) -> Dict[str, Any]:
        """Get log statistics."""
        with self.buffer_lock:
            entries = self.log_buffer

        severity_counts = {}
        component_counts = {}

        for entry in entries:
            severity_counts[entry.severity] = severity_counts.get(entry.severity, 0) + 1
            component_counts[entry.component] = component_counts.get(entry.component, 0) + 1

        return {
            "total_entries": len(entries),
            "severity_counts": severity_counts,
            "component_counts": component_counts,
            "session_id": self.session_id,
            "session_duration_seconds": (datetime.fromisoformat(
                self.log_buffer[-1].timestamp if entries else self.start_time
            ) - datetime.fromisoformat(self.start_time)).total_seconds() if entries else 0
        }

    def print_statistics(self):
        """Print log statistics to console."""
        stats = self.get_statistics()

        print(f"\n{'='*60}")
        print("SESSION LOG STATISTICS")
        print(f"{'='*60}")
        print(f"Session ID: {stats['session_id']}")
        print(f"Total Entries: {stats['total_entries']}")
        print(f"Duration: {stats['session_duration_seconds']:.1f}s")

        print("\nSeverity Counts:")
        for severity, count in sorted(stats['severity_counts'].items()):
            print(f"  {severity}: {count}")

        print("\nComponent Counts:")
        for component, count in sorted(stats['component_counts'].items()):
            print(f"  {component}: {count}")

    def cleanup_old_logs(self):
        """Cleanup old log files."""
        self.rotation_handler.cleanup_old_logs()


class TelemetryCollector:
    """Optional anonymized telemetry collection."""

    def __init__(self, enabled: bool = False, logger: Optional[StructuredLogger] = None):
        self.enabled = enabled
        self.logger = logger
        self.machine_id = self._generate_machine_id()
        self.telemetry_data: Dict[str, Any] = {
            "machine_id": self.machine_id,
            "events": []
        }

    def _generate_machine_id(self) -> str:
        """Generate anonymous machine identifier."""
        machine_info = f"{sys.platform}-{os.name}"
        return hashlib.sha256(machine_info.encode()).hexdigest()[:16]

    def record_event(self, event_type: str, details: Optional[Dict] = None):
        """Record a telemetry event."""
        if not self.enabled:
            return

        event = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "details": details or {}
        }

        self.telemetry_data["events"].append(event)

        if self.logger:
            self.logger.debug("Telemetry", f"Recorded event: {event_type}", details)

    def record_connection_attempt(self, success: bool, duration_ms: float):
        """Record connection attempt telemetry."""
        self.record_event("connection_attempt", {
            "success": success,
            "duration_ms": round(duration_ms, 2)
        })

    def record_render_attempt(self, success: bool, duration_ms: float):
        """Record render attempt telemetry."""
        self.record_event("render_attempt", {
            "success": success,
            "duration_ms": round(duration_ms, 2)
        })

    def export_telemetry(self, path: Optional[str] = None) -> str:
        """Export telemetry data."""
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = f"telemetry_export_{timestamp}.json"

        Path(path).write_text(json.dumps(self.telemetry_data, indent=2))
        return path


# Global logger instance
_logger: Optional[StructuredLogger] = None


def get_logger(debug: bool = False) -> StructuredLogger:
    """Get or create global logger instance."""
    global _logger
    if _logger is None:
        _logger = StructuredLogger(debug=debug)
    return _logger


def log_event(component: str, message: str, severity: Severity = Severity.INFO,
              details: Optional[Dict] = None):
    """Convenience function to log an event."""
    logger = get_logger()
    logger.log(component, message, severity, details)
