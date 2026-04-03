"""Threaded synchronous WebSocket client. Simple, reliable, no asyncio complexity."""

import json
import threading
import queue
import time
import random

# Lazy import -- websockets loaded only when connect() is called
_ws_module = None


def _get_ws():
    global _ws_module
    if _ws_module is None:
        from websockets.sync.client import connect
        _ws_module = connect
    return _ws_module


class Connection:
    """Thread-safe WebSocket connection to the render server.

    Usage:
        conn = Connection("ws://100.74.135.83:9876")
        conn.connect()
        conn.send_json({"type": "ping"})
        msg = conn.recv()  # Non-blocking, returns None if no message
        conn.close()
    """

    def __init__(self, url):
        self.url = url
        self.connected = False
        self.gpu_name = ""
        self.vram_free = 0
        self.error = ""

        self._send_queue = queue.Queue()
        self._recv_queue = queue.Queue()
        self._binary_queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = None

        # Backoff state for reconnection (FIX #2)
        self._reconnect_delay = 0.5  # Start at 500ms
        self._reconnect_delay_max = 30.0  # Cap at 30s
        self._reconnect_backoff = 2.0  # Double each attempt

    def connect(self):
        """Start connection in background thread."""
        if self._thread and self._thread.is_alive():
            self.close()

        self._stop.clear()
        self.error = ""
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

        # Wait up to 10s for connection
        deadline = time.time() + 10.0
        while not self.connected and not self.error and time.time() < deadline:
            time.sleep(0.1)

        if not self.connected and not self.error:
            self.error = "Connection timed out"

    def close(self):
        """Stop the connection."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.connected = False

    def send_json(self, data):
        """Queue a JSON message to send."""
        self._send_queue.put(("json", json.dumps(data)))

    def send_binary(self, data):
        """Queue binary data to send."""
        self._send_queue.put(("binary", data))

    def recv(self):
        """Non-blocking: get next JSON message or None."""
        try:
            return self._recv_queue.get_nowait()
        except queue.Empty:
            return None

    def recv_binary(self):
        """Non-blocking: get next binary message or None."""
        try:
            return self._binary_queue.get_nowait()
        except queue.Empty:
            return None

    def _handle_binary_frame(self, frame):
        """Validate and buffer incoming binary frames (FIX #3)."""
        if len(frame) > 500 * 1024 * 1024:
            print(f"[Connection] ERROR: Binary frame exceeds max size: {len(frame)} bytes")
            self.connected = False
            return

        if len(frame) == 0:
            print(f"[Connection] WARNING: Received empty binary frame")
            return

        self._binary_queue.put(bytes(frame))
        print(f"[Connection] Received binary frame: {len(frame)} bytes")

    def _worker(self):
        """Background thread: connect, send/receive, auto-reconnect."""
        connect_fn = _get_ws()

        while not self._stop.is_set():
            try:
                print(f"[Connection] Connecting to {self.url}...")
                ws = connect_fn(
                    self.url,
                    open_timeout=10,
                    close_timeout=5,
                    max_size=500 * 1024 * 1024,
                )
            except Exception as e:
                self.error = f"Connection failed: {e}"
                print(f"[Connection] {self.error}")
                self.connected = False
                # FIX #2: Exponential backoff with jitter
                delay = min(self._reconnect_delay, self._reconnect_delay_max)
                jitter = delay * random.uniform(0, 0.1)
                wait_time = delay + jitter
                print(f"[Connection] Retrying in {wait_time:.2f}s (backoff: {delay:.2f}s)...")
                if not self._stop.wait(wait_time):
                    self._reconnect_delay = min(
                        self._reconnect_delay * self._reconnect_backoff,
                        self._reconnect_delay_max
                    )
                    continue
                return

            try:
                # Send ping to verify connection
                ws.send(json.dumps({"type": "ping"}))
                pong_raw = ws.recv(timeout=5.0)
                pong = json.loads(pong_raw)
                if pong.get("type") == "pong":
                    self.gpu_name = pong.get("gpu", "Unknown")
                    self.vram_free = pong.get("vram_free", 0)
                    self.error = ""
                    print(f"[Connection] Connected! GPU: {self.gpu_name}")
                    # FIX #2: Reset backoff on successful connection
                    self._reconnect_delay = 0.5
                else:
                    self.error = "Unexpected server response"
                    ws.close()
                    continue

                # FIX #1: Set connected flag ONLY when entering main loop
                self.connected = True

                # Main send/receive loop
                while not self._stop.is_set() and self.connected:
                    # Send queued messages
                    while not self._send_queue.empty():
                        try:
                            msg_type, data = self._send_queue.get_nowait()
                            if msg_type == "json":
                                ws.send(data)
                            else:
                                ws.send(data)
                        except queue.Empty:
                            break
                        except Exception as e:
                            print(f"[Connection] Send error: {e}")
                            self.connected = False
                            break

                    # Receive messages (non-blocking with short timeout)
                    try:
                        msg = ws.recv(timeout=0.05)
                        if isinstance(msg, bytes):
                            # FIX #3: Use validation method for binary frames
                            self._handle_binary_frame(msg)
                        else:
                            self._recv_queue.put(json.loads(msg))
                    except TimeoutError:
                        pass  # No message available, that's fine
                    except Exception as e:
                        print(f"[Connection] Recv error: {e}")
                        self.connected = False
                        self.error = f"Recv failed: {e}"
                        # FIX #3: Explicit close on error
                        try:
                            ws.close()
                        except:
                            pass
                        break

            except Exception as e:
                print(f"[Connection] Error: {e}")
                self.connected = False
            finally:
                # FIX #7: Ensure close is called on all error paths
                try:
                    ws.close()
                except Exception:
                    pass

            # If we got here without stop, try reconnecting
            if not self._stop.is_set():
                # FIX #2: Apply backoff to reconnection delay in main loop
                delay = min(self._reconnect_delay, self._reconnect_delay_max)
                jitter = delay * random.uniform(0, 0.1)
                wait_time = delay + jitter
                print(f"[Connection] Reconnecting in {wait_time:.2f}s...")
                self._stop.wait(wait_time)
                self._reconnect_delay = min(
                    self._reconnect_delay * self._reconnect_backoff,
                    self._reconnect_delay_max
                )

        self.connected = False
        print("[Connection] Worker stopped.")
