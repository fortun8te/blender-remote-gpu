"""HTTP client for render server. Uses only Python builtins — no external deps."""

import json
import time
import urllib.request
import urllib.error


class Connection:
    """Simple HTTP connection to the render server.

    Uses urllib.request (built-in) for all communication.
    No threading, no websockets, no external dependencies.
    """

    def __init__(self, url):
        # Normalize URL
        self.url = url.replace('ws://', 'http://').replace('wss://', 'https://')
        if not self.url.startswith('http'):
            self.url = 'http://' + self.url

        self.connected = False
        self.gpu_name = ""
        self.vram_free = 0
        self.error = ""
        self.latency_ms = 0
        self.server_version = ""
        self.server_build = ""
        self.connected_at = None

    def connect(self):
        """Synchronous connect — sends ping, waits for pong."""
        self.error = ""
        self.connected = False

        try:
            print(f"[Connection] Pinging {self.url} ...")
            start = time.time()
            result = self._post({"type": "ping"})
            elapsed = time.time() - start

            if result and result.get("type") == "pong":
                self.connected = True
                self.gpu_name = result.get("gpu", "Unknown")
                self.vram_free = result.get("vram_free", 0)
                self.server_version = result.get("version", "")
                self.server_build = result.get("build", "")
                self.latency_ms = int(elapsed * 1000)
                self.connected_at = time.time()
                print(f"[Connection] ✓ Connected — {self.gpu_name} ({self.latency_ms}ms)")
            else:
                self.error = f"Unexpected response: {result}"
                print(f"[Connection] ✗ {self.error}")

        except urllib.error.URLError as e:
            self.error = f"Cannot reach server: {e.reason}"
            print(f"[Connection] ✗ {self.error}")
        except Exception as e:
            self.error = f"Connection failed: {e}"
            print(f"[Connection] ✗ {self.error}")

    def close(self):
        """Mark as disconnected."""
        self.connected = False

    def send_json(self, data):
        """Send JSON to server and return response."""
        try:
            return self._post(data)
        except Exception as e:
            print(f"[Connection] Send error: {e}")
            return None

    def recv(self):
        """Not used in HTTP mode — responses come from send_json."""
        return None

    def _post(self, data, timeout=10):
        """HTTP POST with JSON body. Returns parsed JSON response."""
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
