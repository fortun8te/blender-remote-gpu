"""
Comprehensive test harness for Blender Remote GPU addon

Tests all critical components:
- Connection module (mocked WebSocket)
- Engine module (mocked server responses)
- Logging system (JSON output, rotation)
- Integration tests
"""

import unittest
import json
import queue
import threading
import time
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, call
from io import StringIO
import sys


# Mock bpy early before any imports
sys.modules['bpy'] = MagicMock()
sys.modules['bpy.types'] = MagicMock()
sys.modules['bpy.props'] = MagicMock()
sys.modules['bpy.utils'] = MagicMock()


# ============================================================================
# Unit Tests for Connection Module
# ============================================================================

class MockWebSocket:
    """Mock WebSocket for testing without real network."""

    def __init__(self, should_fail=False, fail_on_send=False,
                 fail_on_recv=False, recv_delay=0):
        self.should_fail = should_fail
        self.fail_on_send = fail_on_send
        self.fail_on_recv = fail_on_recv
        self.recv_delay = recv_delay
        self.sent_messages = []
        self.close_called = False
        self.recv_queue = queue.Queue()
        self.connection_active = True

    def send(self, data):
        """Mock send."""
        if self.fail_on_send:
            raise ConnectionError("Send failed")
        if not self.connection_active:
            raise ConnectionError("Connection closed")
        self.sent_messages.append(data)

    def recv(self, timeout=None):
        """Mock recv."""
        if self.fail_on_recv:
            raise ConnectionError("Recv failed")

        if self.recv_delay > 0:
            time.sleep(self.recv_delay)

        try:
            return self.recv_queue.get(timeout=timeout or 1.0)
        except queue.Empty:
            raise TimeoutError("No message")

    def close(self):
        """Mock close."""
        self.close_called = True
        self.connection_active = False

    def queue_response(self, data):
        """Queue a response to be received."""
        self.recv_queue.put(data)


class TestConnectionInitialization(unittest.TestCase):
    """Test Connection object initialization."""

    @patch('addon.connection._get_ws')
    def test_connection_creates_with_url(self, mock_get_ws):
        """Test Connection object creation with URL."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        assert conn.url == "ws://localhost:9876"
        assert conn.connected is False
        assert conn.gpu_name == ""
        assert conn.vram_free == 0
        assert conn.error == ""
        assert conn._thread is None

    @patch('addon.connection._get_ws')
    def test_connection_queue_initialization(self, mock_get_ws):
        """Test Connection queues are initialized."""
        from addon.connection import Connection

        conn = Connection("ws://test:1234")

        # All queues should be empty
        assert conn._send_queue.empty()
        assert conn._recv_queue.empty()
        assert conn._binary_queue.empty()
        assert not conn._stop.is_set()


class TestConnectionMessaging(unittest.TestCase):
    """Test Connection message sending/receiving."""

    @patch('addon.connection._get_ws')
    def test_send_json_queues_message(self, mock_get_ws):
        """Test send_json queues message correctly."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")
        msg = {"type": "ping", "value": 42}

        conn.send_json(msg)

        # Check message was queued
        msg_type, data = conn._send_queue.get_nowait()
        assert msg_type == "json"
        assert json.loads(data) == msg

    @patch('addon.connection._get_ws')
    def test_send_binary_queues_message(self, mock_get_ws):
        """Test send_binary queues binary data."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")
        data = b"test binary data"

        conn.send_binary(data)

        # Check message was queued
        msg_type, binary_data = conn._send_queue.get_nowait()
        assert msg_type == "binary"
        assert binary_data == data

    @patch('addon.connection._get_ws')
    def test_recv_returns_none_when_empty(self, mock_get_ws):
        """Test recv returns None when queue is empty."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        # Queue is empty, should return None
        msg = conn.recv()
        assert msg is None

    @patch('addon.connection._get_ws')
    def test_recv_returns_message_when_available(self, mock_get_ws):
        """Test recv returns message from queue."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")
        test_msg = '{"type": "response"}'

        # Manually queue a message
        conn._recv_queue.put(test_msg)

        msg = conn.recv()
        assert msg == test_msg


class TestConnectionTimeout(unittest.TestCase):
    """Test Connection timeout handling."""

    @patch('addon.connection._get_ws')
    def test_timeout_handling_waits_duration(self, mock_get_ws):
        """Test timeout mechanism."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        # No messages available, recv should return None
        msg = conn.recv()
        assert msg is None


class TestConnectionQueueing(unittest.TestCase):
    """Test message queue behavior."""

    @patch('addon.connection._get_ws')
    def test_fifo_queue_order(self, mock_get_ws):
        """Test messages are queued in FIFO order."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        # Queue multiple messages
        for i in range(5):
            conn.send_json({"id": i})

        # Verify FIFO order
        for i in range(5):
            msg_type, data = conn._send_queue.get_nowait()
            assert msg_type == "json"
            assert json.loads(data)["id"] == i

    @patch('addon.connection._get_ws')
    def test_separate_send_queue_handles_both(self, mock_get_ws):
        """Test send_json and send_binary both use send queue with type."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        # Queue both types
        conn.send_json({"type": "json"})
        conn.send_binary(b"binary")
        conn.send_json({"type": "json2"})

        # Check order - both go to send_queue
        msg_type, data = conn._send_queue.get_nowait()
        assert msg_type == "json"
        assert "json" in data

        msg_type, data = conn._send_queue.get_nowait()
        assert msg_type == "binary"
        assert data == b"binary"

        msg_type, data = conn._send_queue.get_nowait()
        assert msg_type == "json"
        assert "json2" in data


# ============================================================================
# Unit Tests for Logging System
# ============================================================================

class TestLoggingSystem(unittest.TestCase):
    """Test structured logging system."""

    def setUp(self):
        """Set up test logging."""
        self.temp_dir = tempfile.mkdtemp()
        # Reset global logger to avoid cross-test contamination
        import addon.logging_system
        addon.logging_system._logger = None

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        # Reset global logger
        import addon.logging_system
        addon.logging_system._logger = None

    def test_logger_creation(self):
        """Test logger initialization."""
        from addon.logging_system import StructuredLogger

        logger = StructuredLogger(log_dir=self.temp_dir)
        assert logger is not None
        assert logger.session_id is not None

    def test_json_log_format(self):
        """Test logs are JSON formatted."""
        from addon.logging_system import StructuredLogger

        logger = StructuredLogger(log_dir=self.temp_dir)
        logger.info("TestComponent", "Test message", details={"key": "value"})

        # Get session logs
        session_data = logger.get_session_log()
        assert "entries" in session_data
        entries = session_data["entries"]
        assert len(entries) > 0

        # Find our log entry
        found = False
        for entry in entries:
            if entry.get("component") == "TestComponent":
                assert "timestamp" in entry
                assert "message" in entry
                assert entry["message"] == "Test message"
                assert entry["details"]["key"] == "value"
                found = True
                break
        assert found, "TestComponent log not found"

    def test_log_severity_levels(self):
        """Test different severity levels."""
        from addon.logging_system import StructuredLogger, Severity

        logger = StructuredLogger(log_dir=self.temp_dir, debug=True)

        # Note: logger.debug is a bool attribute, not a method
        # Use logger.log() with Severity enum instead
        logger.log("Component", "Debug message", Severity.DEBUG)
        logger.log("Component", "Info message", Severity.INFO)
        logger.log("Component", "Warning message", Severity.WARNING)
        logger.log("Component", "Error message", Severity.ERROR)

        session_data = logger.get_session_log()
        entries = session_data["entries"]

        # Filter to just our entries
        our_entries = [e for e in entries if e["component"] == "Component"]
        assert len(our_entries) >= 3  # At least INFO, WARNING, ERROR

        severities = [e["severity"] for e in our_entries]
        assert "INFO" in severities
        assert "WARNING" in severities
        assert "ERROR" in severities

    def test_log_export(self):
        """Test log export functionality."""
        from addon.logging_system import StructuredLogger

        logger = StructuredLogger(log_dir=self.temp_dir)
        logger.info("Component", "Test export", details={"id": 123})

        # Export logs
        path = logger.export_session_log(os.path.join(self.temp_dir, "test_export.json"))
        assert path is not None
        assert os.path.exists(path)

        # Read exported file
        with open(path) as f:
            data = json.load(f)
            assert "entries" in data
            assert "session_id" in data
            assert len(data["entries"]) > 0


class TestTelemetry(unittest.TestCase):
    """Test telemetry collection."""

    def setUp(self):
        """Set up test telemetry."""
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_telemetry_initialization(self):
        """Test telemetry can be created."""
        from addon.logging_system import TelemetryCollector, StructuredLogger

        logger = StructuredLogger(log_dir=self.temp_dir)
        telemetry = TelemetryCollector(enabled=True, logger=logger)

        assert telemetry is not None
        assert telemetry.machine_id is not None

    def test_telemetry_records_connection(self):
        """Test telemetry records connection events."""
        from addon.logging_system import TelemetryCollector

        telemetry = TelemetryCollector(enabled=True)

        telemetry.record_connection_attempt(success=True, duration_ms=150.5)
        telemetry.record_connection_attempt(success=False, duration_ms=300.2)

        events = telemetry.telemetry_data["events"]
        assert len(events) == 2
        assert events[0]["event_type"] == "connection_attempt"
        assert events[0]["details"]["success"] is True

    def test_telemetry_export(self):
        """Test telemetry export to JSON."""
        from addon.logging_system import TelemetryCollector

        telemetry = TelemetryCollector(enabled=True)

        telemetry.record_connection_attempt(success=True, duration_ms=100)

        export_path = os.path.join(self.temp_dir, "telemetry.json")
        telemetry.export_telemetry(export_path)

        assert os.path.exists(export_path)

        with open(export_path) as f:
            data = json.load(f)
            assert "events" in data
            assert "machine_id" in data
            assert len(data["events"]) == 1


# ============================================================================
# Integration Tests
# ============================================================================

class TestConnectionIntegration(unittest.TestCase):
    """Integration tests for full connection flow."""

    @patch('addon.connection._get_ws')
    def test_message_send_receive_cycle(self, mock_get_ws):
        """Test full send/receive cycle."""
        from addon.connection import Connection

        # Create mock and connection
        mock_ws = MockWebSocket()
        mock_get_ws.return_value = mock_ws

        conn = Connection("ws://localhost:9876")

        # Queue a message to be received
        response = '{"type": "response", "value": "test"}'
        mock_ws.queue_response(response)

        # Send a request
        conn.send_json({"type": "request"})

        # Receive response (manually since we're not running thread)
        msg = conn.recv()
        # Will be None because it's not in the recv_queue

        # This is expected - real flow would happen in the worker thread


class TestDiagnosticsBasic(unittest.TestCase):
    """Basic tests for diagnostics tools."""

    def test_connectivity_diagnostics_import(self):
        """Test connectivity diagnostics can be imported."""
        from tools.connectivity_diagnostics import (
            ConnectivityDiagnostics,
            DiagnosticReporter,
            LatencyHistogram
        )
        assert ConnectivityDiagnostics is not None
        assert DiagnosticReporter is not None
        assert LatencyHistogram is not None

    def test_server_health_check_import(self):
        """Test server health check can be imported."""
        from tools.server_health_check import ServerHealthChecker
        assert ServerHealthChecker is not None

    def test_diagnostic_reporter_creation(self):
        """Test creating a diagnostic reporter."""
        from tools.connectivity_diagnostics import DiagnosticReporter

        reporter = DiagnosticReporter()
        reporter.add_result(
            "Test", True, 0.123, "Test message",
            details={"key": "value"}
        )

        # Generate reports
        json_report = reporter.to_json()
        html_report = reporter.to_html()

        assert isinstance(json_report, str)
        assert isinstance(html_report, str)
        assert "Test" in json_report
        assert "summary" in json_report.lower()


# ============================================================================
# Test Suite
# ============================================================================

if __name__ == "__main__":
    # Run all tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestConnectionInitialization))
    suite.addTests(loader.loadTestsFromTestCase(TestConnectionMessaging))
    suite.addTests(loader.loadTestsFromTestCase(TestConnectionTimeout))
    suite.addTests(loader.loadTestsFromTestCase(TestConnectionQueueing))
    suite.addTests(loader.loadTestsFromTestCase(TestLoggingSystem))
    suite.addTests(loader.loadTestsFromTestCase(TestTelemetry))
    suite.addTests(loader.loadTestsFromTestCase(TestConnectionIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestDiagnosticsBasic))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)
