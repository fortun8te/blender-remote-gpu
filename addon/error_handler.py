"""Error handling UI for Blender addon.

Converts error codes to user-friendly messages with recovery suggestions.
Shows errors in Blender's UI and logs them properly.

Usage:
    from addon.error_handler import show_error, show_warning

    try:
        conn.connect()
    except Exception as e:
        show_error("CONN_001", str(e))
"""

import logging
from typing import Optional, Callable
from shared.error_codes import ErrorCodes, ErrorCode


def get_logger() -> logging.Logger:
    """Get or create the addon logger."""
    logger = logging.getLogger("remote-gpu.addon")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


class BlenderUI:
    """Interface to Blender UI for showing messages."""

    _report_callback: Optional[Callable[[set, str], None]] = None

    @classmethod
    def set_report_callback(cls, callback: Callable[[set, str], None]) -> None:
        """Set callback for reporting messages (from bpy context).

        Args:
            callback: Function like bpy.context.window_manager.popup_menu or
                     bpy.types.Operator.report
        """
        cls._report_callback = callback

    @classmethod
    def report(cls, msg_type: str, message: str) -> None:
        """Report message to Blender UI.

        Args:
            msg_type: One of "INFO", "WARNING", "ERROR", "OPERATOR", "RUNNING_MODAL"
            message: Message to display
        """
        if cls._report_callback:
            try:
                cls._report_callback({msg_type}, message)
            except Exception as e:
                # Fallback to logging if UI callback fails
                get_logger().error(f"UI report failed: {e}")


def show_error(
    error_code: str,
    additional_info: Optional[str] = None,
    exception: Optional[Exception] = None,
) -> ErrorCode:
    """Show an error to the user with recovery suggestions.

    Args:
        error_code: Error code (e.g., "CONN_001")
        additional_info: Additional technical information
        exception: Exception object (will be logged with full traceback)

    Returns:
        ErrorCode object for programmatic access
    """
    logger = get_logger()
    error = ErrorCodes.by_code(error_code)

    if error is None:
        logger.error(f"Unknown error code: {error_code}")
        BlenderUI.report("ERROR", f"Unknown error: {error_code}")
        return None

    # Log with full details
    log_msg = f"[{error.code}] {error.message}"
    if additional_info:
        log_msg += f"\n  Additional info: {additional_info}"

    if exception:
        logger.error(log_msg, exc_info=exception)
    else:
        logger.error(log_msg)

    # Show user-friendly message
    ui_msg = error.user_message
    if additional_info:
        ui_msg += f"\n({additional_info})"

    BlenderUI.report("ERROR", ui_msg)

    # Log recovery suggestions
    if error.recovery_suggestions:
        suggestions_text = "\n  ".join(error.recovery_suggestions)
        logger.info(f"Recovery suggestions for {error.code}:\n  {suggestions_text}")

    return error


def show_warning(
    error_code: str,
    additional_info: Optional[str] = None,
) -> ErrorCode:
    """Show a warning to the user.

    Args:
        error_code: Error code (e.g., "PERF_001")
        additional_info: Additional information

    Returns:
        ErrorCode object
    """
    logger = get_logger()
    error = ErrorCodes.by_code(error_code)

    if error is None:
        logger.warning(f"Unknown warning code: {error_code}")
        BlenderUI.report("WARNING", f"Unknown warning: {error_code}")
        return None

    # Log with details
    log_msg = f"[{error.code}] {error.message}"
    if additional_info:
        log_msg += f"\n  Info: {additional_info}"
    logger.warning(log_msg)

    # Show user-friendly message
    ui_msg = error.user_message
    if additional_info:
        ui_msg += f"\n({additional_info})"

    BlenderUI.report("WARNING", ui_msg)

    return error


def show_info(message: str) -> None:
    """Show an info message to the user."""
    logger = get_logger()
    logger.info(message)
    BlenderUI.report("INFO", message)


def show_suggestion(error_code: str) -> str:
    """Get and display first recovery suggestion for an error.

    Args:
        error_code: Error code

    Returns:
        First suggestion (or empty string if none)
    """
    error = ErrorCodes.by_code(error_code)
    if error and error.recovery_suggestions:
        suggestion = error.recovery_suggestions[0]
        show_info(f"Try: {suggestion}")
        return suggestion
    return ""


def format_error_details(
    error_code: str,
    duration_ms: Optional[float] = None,
    operation_id: Optional[str] = None,
) -> str:
    """Format detailed error information for logging.

    Args:
        error_code: Error code
        duration_ms: How long operation took before error
        operation_id: Operation ID for tracing

    Returns:
        Formatted error details string
    """
    error = ErrorCodes.by_code(error_code)
    if not error:
        return f"Unknown error: {error_code}"

    details = f"Error {error.code}: {error.message}\n"
    details += f"Category: {error.category}\n"
    details += f"Severity: {error.severity}\n"

    if operation_id:
        details += f"Operation ID: {operation_id}\n"
    if duration_ms is not None:
        details += f"Duration: {duration_ms:.1f}ms\n"

    if error.recovery_suggestions:
        details += "\nRecovery steps:\n"
        for i, suggestion in enumerate(error.recovery_suggestions, 1):
            details += f"  {i}. {suggestion}\n"

    return details


class ErrorTracker:
    """Track errors during a session for diagnostics and reporting."""

    def __init__(self):
        self.errors: list[dict] = []
        self.logger = get_logger()

    def record(
        self,
        error_code: str,
        operation: str,
        duration_ms: Optional[float] = None,
        details: Optional[str] = None,
    ) -> None:
        """Record an error occurrence.

        Args:
            error_code: Error code
            operation: Operation that failed
            duration_ms: Operation duration
            details: Additional details
        """
        error = ErrorCodes.by_code(error_code)
        record = {
            "code": error_code,
            "operation": operation,
            "message": error.message if error else "Unknown",
            "category": error.category if error else "unknown",
            "duration_ms": duration_ms,
            "details": details,
        }
        self.errors.append(record)
        self.logger.debug(f"Error recorded: {error_code} ({operation})")

    def get_summary(self) -> str:
        """Get a summary of all recorded errors."""
        if not self.errors:
            return "No errors recorded"

        by_code = {}
        for error in self.errors:
            code = error["code"]
            by_code[code] = by_code.get(code, 0) + 1

        summary = f"Error summary: {len(self.errors)} total errors\n"
        for code, count in sorted(by_code.items()):
            summary += f"  {code}: {count}x\n"

        return summary

    def export_log(self) -> dict:
        """Export errors for diagnostic reporting."""
        return {
            "total_errors": len(self.errors),
            "errors": self.errors,
        }


# Singleton error tracker
_tracker = ErrorTracker()


def record_error(
    error_code: str,
    operation: str,
    duration_ms: Optional[float] = None,
) -> None:
    """Record an error for session tracking."""
    _tracker.record(error_code, operation, duration_ms)


def get_error_summary() -> str:
    """Get current error summary."""
    return _tracker.get_summary()


def get_error_log() -> dict:
    """Get error log for export."""
    return _tracker.export_log()
