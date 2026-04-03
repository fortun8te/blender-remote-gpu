"""HTTP client for render server with multi-method fallback.

Tries 4 connection methods in order:
1. urllib.request (HTTP POST)
2. http.client (lower-level HTTP)
3. Raw TCP socket (length-prefixed JSON)
4. xmlrpc.client (XML-RPC over HTTP)

All use Python builtins only — zero external dependencies.
"""

import json
import time
import base64


class Connection:
    """Connection to the remote GPU render server."""

    def __init__(self, host, port=9876):
        self.host = host
        self.port = int(port)
        self.url = f"http://{host}:{port}"
        self.connected = False
        self.gpu_name = ""
        self.vram_free = 0
        self.error = ""
        self.latency_ms = 0
        self.server_version = ""
        self.server_build = ""
        self.connected_at = None
        self.method = ""  # Which method worked

    def connect(self):
        """Try all connection methods, use first that works."""
        self.error = ""
        self.connected = False

        methods = [
            ("urllib", self._try_urllib),
            ("http.client", self._try_http_client),
            ("socket", self._try_socket),
            ("xmlrpc", self._try_xmlrpc),
        ]

        for name, fn in methods:
            print(f"[Connection] Trying {name}...")
            try:
                result = fn({"type": "ping"})
                if result and result.get("type") == "pong":
                    self.connected = True
                    self.method = name
                    self.gpu_name = result.get("gpu", "Unknown")
                    self.vram_free = result.get("vram_free", 0)
                    self.server_version = result.get("version", "")
                    self.server_build = result.get("build", "")
                    self.connected_at = time.time()
                    print(f"[Connection] ✓ Connected via {name} — {self.gpu_name}")
                    return
            except Exception as e:
                print(f"[Connection] ✗ {name} failed: {e}")
                continue

        self.error = "All connection methods failed"
        print(f"[Connection] ✗ {self.error}")

    def close(self):
        self.connected = False
        self.method = ""

    def send(self, data):
        """Send JSON to server via the working method, return response."""
        if not self.connected:
            return None

        dispatch = {
            "urllib": self._try_urllib,
            "http.client": self._try_http_client,
            "socket": self._try_socket,
            "xmlrpc": self._try_xmlrpc,
        }
        fn = dispatch.get(self.method)
        if not fn:
            return None

        try:
            return fn(data)
        except Exception as e:
            print(f"[Connection] Send error ({self.method}): {e}")
            self.error = str(e)
            return None

    # ── Method 1: urllib.request ──────────────────────────────

    def _try_urllib(self, data, timeout=5):
        import urllib.request
        import urllib.error

        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        start = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        self.latency_ms = int((time.time() - start) * 1000)
        return result

    # ── Method 2: http.client ─────────────────────────────────

    def _try_http_client(self, data, timeout=5):
        import http.client

        body = json.dumps(data).encode("utf-8")
        start = time.time()
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request("POST", "/", body=body,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            result = json.loads(resp.read().decode("utf-8"))
            self.latency_ms = int((time.time() - start) * 1000)
            return result
        finally:
            conn.close()

    # ── Method 3: Raw TCP socket ──────────────────────────────

    def _try_socket(self, data, timeout=5):
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((self.host, self.port + 1))  # Socket on port+1
            payload = json.dumps(data).encode("utf-8")
            start = time.time()
            sock.sendall(len(payload).to_bytes(4, "big"))
            sock.sendall(payload)
            length = int.from_bytes(self._recv_exact(sock, 4), "big")
            response = self._recv_exact(sock, length)
            self.latency_ms = int((time.time() - start) * 1000)
            return json.loads(response.decode("utf-8"))
        finally:
            sock.close()

    def _recv_exact(self, sock, n):
        chunks = []
        received = 0
        while received < n:
            chunk = sock.recv(min(4096, n - received))
            if not chunk:
                raise ConnectionError("Server closed connection")
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)

    # ── Method 4: XML-RPC ─────────────────────────────────────

    def _try_xmlrpc(self, data, timeout=5):
        import xmlrpc.client

        # xmlrpc expects method calls, so we use a generic 'handle' method
        start = time.time()
        proxy = xmlrpc.client.ServerProxy(
            f"http://{self.host}:{self.port + 2}",  # XMLRPC on port+2
            allow_none=True,
        )
        proxy._ServerProxy__transport.timeout = timeout
        result = proxy.handle(json.dumps(data))
        self.latency_ms = int((time.time() - start) * 1000)
        return json.loads(result)

    # ── Render helpers ────────────────────────────────────────

    def submit_render(self, blend_data_b64, width, height, samples):
        """Submit a render job, returns job_id."""
        result = self.send({
            "type": "render_submit",
            "blend_data": blend_data_b64,
            "width": width,
            "height": height,
            "samples": samples,
        })
        if result and result.get("type") == "render_queued":
            return result.get("job_id")
        return None

    def poll_status(self, job_id):
        """Poll render job status."""
        return self.send({
            "type": "job_status",
            "job_id": job_id,
        })

    def get_result(self, job_id):
        """Get render result (base64 PNG)."""
        return self.send({
            "type": "job_result",
            "job_id": job_id,
        })
