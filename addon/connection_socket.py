"""Raw TCP socket client — ultimate fallback if urllib fails in Blender.

Uses only Python's socket module (guaranteed to exist in every Python build).
Protocol: 4-byte big-endian length prefix + JSON payload.
"""

import json
import socket
import time


class SocketConnection:
    """Raw TCP connection to the render server.

    Protocol:
        Client sends: [4 bytes: length][JSON bytes]
        Server sends: [4 bytes: length][JSON bytes]
    """

    def __init__(self, host, port=9877):
        self.host = host
        self.port = int(port) + 1  # Socket server runs on HTTP port + 1
        self.connected = False
        self.gpu_name = ""
        self.vram_free = 0
        self.error = ""
        self.latency_ms = 0
        self.server_version = ""
        self.server_build = ""
        self.connected_at = None

    def connect(self):
        """Connect and ping the server."""
        self.error = ""
        self.connected = False

        try:
            print(f"[Socket] Connecting to {self.host}:{self.port} ...")
            start = time.time()
            result = self._send_recv({"type": "ping"})
            elapsed = time.time() - start

            if result and result.get("type") == "pong":
                self.connected = True
                self.gpu_name = result.get("gpu", "Unknown")
                self.vram_free = result.get("vram_free", 0)
                self.server_version = result.get("version", "")
                self.server_build = result.get("build", "")
                self.latency_ms = int(elapsed * 1000)
                self.connected_at = time.time()
                print(f"[Socket] ✓ Connected — {self.gpu_name} ({self.latency_ms}ms)")
            else:
                self.error = f"Unexpected response: {result}"
                print(f"[Socket] ✗ {self.error}")

        except socket.timeout:
            self.error = "Connection timed out"
            print(f"[Socket] ✗ {self.error}")
        except ConnectionRefusedError:
            self.error = "Connection refused — server not running?"
            print(f"[Socket] ✗ {self.error}")
        except Exception as e:
            self.error = f"Connection failed: {e}"
            print(f"[Socket] ✗ {self.error}")

    def close(self):
        """Mark as disconnected."""
        self.connected = False

    def send_json(self, data):
        """Send JSON and get response."""
        try:
            return self._send_recv(data)
        except Exception as e:
            print(f"[Socket] Send error: {e}")
            return None

    def recv(self):
        """Not used — responses come from send_json."""
        return None

    def _send_recv(self, data, timeout=10):
        """Open TCP socket, send length-prefixed JSON, read response, close."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((self.host, self.port))

            # Send: 4-byte length + JSON
            payload = json.dumps(data).encode("utf-8")
            sock.sendall(len(payload).to_bytes(4, "big"))
            sock.sendall(payload)

            # Recv: 4-byte length + JSON
            length_bytes = self._recv_exact(sock, 4)
            length = int.from_bytes(length_bytes, "big")
            response_bytes = self._recv_exact(sock, length)
            return json.loads(response_bytes.decode("utf-8"))
        finally:
            sock.close()

    def _recv_exact(self, sock, n):
        """Read exactly n bytes from socket."""
        chunks = []
        received = 0
        while received < n:
            chunk = sock.recv(min(4096, n - received))
            if not chunk:
                raise ConnectionError("Server closed connection")
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)
