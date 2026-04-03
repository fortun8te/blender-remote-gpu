"""HTTP client for render server. Simple, built-in, no external dependencies."""

import json
import threading
import queue
import time
import random
import urllib.request
import urllib.error


class Connection:
    """Thread-safe HTTP connection to the render server.

    Usage:
        conn = Connection("http://100.74.135.83:9876")
        conn.connect()
        conn.send_json({"type": "ping"})
        response = conn.recv()
        conn.close()
    """

    def __init__(self, url):
        # Convert ws:// to http://
        self.url = url.replace('ws://', 'http://').replace('wss://', 'https://')
        if not self.url.startswith('http'):
            self.url = 'http://' + self.url

        self.connected = False
        self.gpu_name = ""
        self.vram_free = 0
        self.error = ""

        self._recv_queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = None

        # Backoff state for reconnection
        self._reconnect_delay = 0.5
        self._reconnect_delay_max = 30.0
        self._reconnect_backoff = 2.0

        # Connection metadata
        self.connected_at = None
        self.latency_ms = 0
        self.server_version = ""
        self.server_build = ""

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
        """Send JSON message to server."""
        if not self.connected:
            print(f"[Connection] ERROR: Not connected, cannot send")
            return False

        try:
            json_data = json.dumps(data).encode('utf-8')
            req = urllib.request.Request(
                self.url,
                data=json_data,
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=5.0) as response:
                response_data = response.read().decode('utf-8')
                response_json = json.loads(response_data)
                self._recv_queue.put(response_json)
                return True
        except Exception as e:
            print(f"[Connection] ERROR sending message: {e}")
            return False

    def recv(self):
        """Non-blocking: get next JSON message or None."""
        try:
            return self._recv_queue.get_nowait()
        except queue.Empty:
            return None

    def _worker(self):
        """Background thread: ping to establish connection."""
        while not self._stop.is_set():
            try:
                print(f"[Connection] Connecting to {self.url}...")

                # Send ping to verify connection
                ping_time = time.time()
                json_data = json.dumps({"type": "ping"}).encode('utf-8')
                req = urllib.request.Request(
                    self.url,
                    data=json_data,
                    headers={'Content-Type': 'application/json'}
                )

                with urllib.request.urlopen(req, timeout=5.0) as response:
                    response_data = response.read().decode('utf-8')
                    pong = json.loads(response_data)

                    if pong.get("type") == "pong":
                        # Calculate latency (ms)
                        self.latency_ms = int((time.time() - ping_time) * 1000)
                        self.gpu_name = pong.get("gpu", "Unknown")
                        self.vram_free = pong.get("vram_free", 0)
                        self.server_version = pong.get("version", "")
                        self.server_build = pong.get("build", "")
                        self.connected = True
                        self.connected_at = time.time()
                        self.error = ""
                        print(f"[Connection] ✓ Connected to {self.gpu_name} ({self.latency_ms}ms)")
                        break
                    else:
                        raise Exception(f"Unexpected response: {pong}")

            except urllib.error.URLError as e:
                self.error = f"Connection failed: {e.reason}"
                print(f"[Connection] {self.error}")
                self.connected = False

                delay = min(self._reconnect_delay, self._reconnect_delay_max)
                jitter = delay * random.uniform(0, 0.1)
                wait_time = delay + jitter
                print(f"[Connection] Retrying in {wait_time:.2f}s...")

                if not self._stop.wait(wait_time):
                    self._reconnect_delay = min(
                        self._reconnect_delay * self._reconnect_backoff,
                        self._reconnect_delay_max
                    )
                    continue
                return

            except Exception as e:
                self.error = f"Connection failed: {e}"
                print(f"[Connection] {self.error}")
                self.connected = False

                delay = min(self._reconnect_delay, self._reconnect_delay_max)
                jitter = delay * random.uniform(0, 0.1)
                wait_time = delay + jitter
                print(f"[Connection] Retrying in {wait_time:.2f}s...")

                if not self._stop.wait(wait_time):
                    self._reconnect_delay = min(
                        self._reconnect_delay * self._reconnect_backoff,
                        self._reconnect_delay_max
                    )
                    continue
                return
