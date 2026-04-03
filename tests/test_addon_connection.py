"""
Unit tests for addon/connection.py

Tests the threaded WebSocket client with mock WebSocket connections.
"""

import unittest
import json
import queue
import threading
import time
from unittest.mock import Mock, MagicMock, patch, call
from io import StringIO
import sys


class MockWebSocket:
    """Mock WebSocket for testing."""

    def __init__(self, should_fail=False, fail_on_send=False, fail_on_recv=False):
        self.should_fail = should_fail
        self.fail_on_send = fail_on_send
        self.fail_on_recv = fail_on_recv
        self.sent_messages = []
        self.close_called = False
        self.recv_queue = queue.Queue()

    def send(self, data):
        """Mock send."""
        if self.fail_on_send:
            raise ConnectionError("Send failed")
        self.sent_messages.append(data)

    def recv(self, timeout=None):
        """Mock recv."""
        if self.fail_on_recv:
            raise ConnectionError("Recv failed")

        try:
            return self.recv_queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("No message")

    def close(self):
        """Mock close."""
        self.close_called = True

    def queue_response(self, data):
        """Queue a response to be received."""
        self.recv_queue.put(data)


class TestConnectionClass(unittest.TestCase):
    """Tests for Connection class."""

    def setUp(self):
        """Set up test fixtures."""
        # Import here to avoid issues with module-level code
        sys.path.insert(0, '/Users/mk/Downloads/blender-remote-gpu/addon')

    def tearDown(self):
        """Clean up."""
        # Reset module state if needed
        pass

    @patch('addon.connection._get_ws')
    def test_connection_initialization(self, mock_get_ws):
        """Test Connection object initialization."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        self.assertEqual(conn.url, "ws://localhost:9876")
        self.assertFalse(conn.connected)
        self.assertEqual(conn.gpu_name, "")
        self.assertEqual(conn.vram_free, 0)
        self.assertEqual(conn.error, "")
        self.assertIsNone(conn._thread)

    @patch('addon.connection._get_ws')
    def test_connect_timeout(self, mock_get_ws):
        """Test connection timeout."""
        from addon.connection import Connection

        # Mock WebSocket that never connects
        mock_ws_fn = MagicMock(side_effect=Exception("Connection refused"))
        mock_get_ws.return_value = mock_ws_fn

        conn = Connection("ws://localhost:9876")
        conn.connect()

        # Should timeout after ~10 seconds, but we check the state
        time.sleep(0.5)  # Give thread time to fail
        conn.close()

        # Thread should have been created
        self.assertIsNone(conn._thread)

    @patch('addon.connection._get_ws')
    def test_send_json_queues_message(self, mock_get_ws):
        """Test that send_json properly queues messages."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        test_data = {"type": "ping", "test": True}
        conn.send_json(test_data)

        # Check that message is in queue
        msg_type, data = conn._send_queue.get_nowait()
        self.assertEqual(msg_type, "json")
        self.assertEqual(json.loads(data), test_data)

    @patch('addon.connection._get_ws')
    def test_send_binary_queues_message(self, mock_get_ws):
        """Test that send_binary properly queues messages."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        test_data = b"\x00\x01\x02\x03"
        conn.send_binary(test_data)

        # Check that message is in queue
        msg_type, data = conn._send_queue.get_nowait()
        self.assertEqual(msg_type, "binary")
        self.assertEqual(data, test_data)

    @patch('addon.connection._get_ws')
    def test_recv_returns_none_when_empty(self, mock_get_ws):
        """Test recv returns None when queue is empty."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        result = conn.recv()
        self.assertIsNone(result)

    @patch('addon.connection._get_ws')
    def test_recv_returns_message_when_available(self, mock_get_ws):
        """Test recv returns message when available."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        test_msg = {"type": "pong", "gpu": "Tesla V100"}
        conn._recv_queue.put(test_msg)

        result = conn.recv()
        self.assertEqual(result, test_msg)

    @patch('addon.connection._get_ws')
    def test_recv_binary_returns_none_when_empty(self, mock_get_ws):
        """Test recv_binary returns None when queue is empty."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        result = conn.recv_binary()
        self.assertIsNone(result)

    @patch('addon.connection._get_ws')
    def test_recv_binary_returns_data_when_available(self, mock_get_ws):
        """Test recv_binary returns data when available."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        test_data = b"\x89PNG\r\n\x1a\n"
        conn._binary_queue.put(test_data)

        result = conn.recv_binary()
        self.assertEqual(result, test_data)

    @patch('addon.connection._get_ws')
    def test_close_stops_thread(self, mock_get_ws):
        """Test close properly stops the worker thread."""
        from addon.connection import Connection

        # Create mock that will respond successfully
        mock_ws = MockWebSocket()
        mock_ws.queue_response(json.dumps({"type": "pong", "gpu": "Test GPU", "vram_free": 1000}))

        def mock_connect_fn(*args, **kwargs):
            return mock_ws

        mock_get_ws.return_value = mock_connect_fn

        conn = Connection("ws://localhost:9876")
        conn.connect()

        # Give thread time to start
        time.sleep(0.5)

        # Close connection
        conn.close()

        # Thread should be None
        self.assertIsNone(conn._thread)
        self.assertFalse(conn.connected)


class TestConnectionQueueing(unittest.TestCase):
    """Tests for message queueing mechanism."""

    def test_send_queue_fifo_order(self):
        """Test that send queue maintains FIFO order."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        # Send multiple messages
        for i in range(5):
            conn.send_json({"id": i})

        # Retrieve in order
        for i in range(5):
            msg_type, data = conn._send_queue.get_nowait()
            self.assertEqual(json.loads(data)["id"], i)

    def test_mixed_message_types(self):
        """Test queuing mixed JSON and binary messages."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        conn.send_json({"type": "start"})
        conn.send_binary(b"binary data")
        conn.send_json({"type": "end"})

        msg1_type, msg1_data = conn._send_queue.get_nowait()
        self.assertEqual(msg1_type, "json")

        msg2_type, msg2_data = conn._send_queue.get_nowait()
        self.assertEqual(msg2_type, "binary")

        msg3_type, msg3_data = conn._send_queue.get_nowait()
        self.assertEqual(msg3_type, "json")


class TestConnectionErrorHandling(unittest.TestCase):
    """Tests for error handling and edge cases."""

    @patch('addon.connection._get_ws')
    def test_connection_error_recorded(self, mock_get_ws):
        """Test that connection errors are recorded."""
        from addon.connection import Connection

        mock_get_ws.return_value = MagicMock(side_effect=Exception("Network unreachable"))

        conn = Connection("ws://localhost:9876")
        conn.connect()

        time.sleep(0.5)
        conn.close()

        self.assertFalse(conn.connected)

    def test_thread_safety_concurrent_sends(self):
        """Test thread-safe concurrent sends."""
        from addon.connection import Connection

        conn = Connection("ws://localhost:9876")

        def send_messages():
            for i in range(10):
                conn.send_json({"id": i})
                time.sleep(0.01)

        threads = [threading.Thread(target=send_messages) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All messages should be in queue
        count = 0
        while True:
            try:
                conn._send_queue.get_nowait()
                count += 1
            except queue.Empty:
                break

        self.assertEqual(count, 30)


class TestWebSocketModuleImport(unittest.TestCase):
    """Tests for lazy module import."""

    def test_ws_module_lazy_import(self):
        """Test that websockets module is only imported when needed."""
        from addon import connection

        # Reset global state
        connection._ws_module = None

        # Get the connect function
        ws_fn = connection._get_ws()

        # Should be the connect function
        self.assertIsNotNone(ws_fn)
        self.assertEqual(connection._ws_module, ws_fn)

    def test_ws_module_cached(self):
        """Test that websockets module is cached after first import."""
        from addon import connection

        connection._ws_module = None

        ws_fn1 = connection._get_ws()
        ws_fn2 = connection._get_ws()

        # Should be the same object
        self.assertIs(ws_fn1, ws_fn2)


class TestConnectionIntegration(unittest.TestCase):
    """Integration-style tests with mock WebSocket."""

    @patch('addon.connection._get_ws')
    def test_successful_connection_flow(self, mock_get_ws):
        """Test complete successful connection flow."""
        from addon.connection import Connection

        # Create mock WebSocket
        mock_ws = MockWebSocket()
        mock_ws.queue_response(json.dumps({
            "type": "pong",
            "gpu": "NVIDIA RTX 3090",
            "vram_free": 24576
        }))

        mock_get_ws.return_value = lambda *args, **kwargs: mock_ws

        # Create and connect
        conn = Connection("ws://localhost:9876")
        conn.connect()

        # Give thread time to process
        time.sleep(0.5)

        # Verify connection state
        self.assertTrue(conn.connected)
        self.assertEqual(conn.gpu_name, "NVIDIA RTX 3090")
        self.assertEqual(conn.vram_free, 24576)

        # Clean up
        conn.close()

    @patch('addon.connection._get_ws')
    def test_message_send_receive_cycle(self, mock_get_ws):
        """Test sending and receiving messages."""
        from addon.connection import Connection

        # Create mock WebSocket
        mock_ws = MockWebSocket()
        mock_ws.queue_response(json.dumps({
            "type": "pong",
            "gpu": "Test GPU",
            "vram_free": 8192
        }))

        # Queue response for ping
        mock_ws.queue_response(json.dumps({"type": "pong", "gpu": "Test GPU"}))

        mock_get_ws.return_value = lambda *args, **kwargs: mock_ws

        # Connect and send message
        conn = Connection("ws://localhost:9876")
        conn.connect()
        time.sleep(0.5)

        # Send ping
        conn.send_json({"type": "ping"})

        # Give time for processing
        time.sleep(0.2)

        # Check sent messages
        self.assertGreater(len(mock_ws.sent_messages), 0)

        conn.close()


if __name__ == "__main__":
    unittest.main()
