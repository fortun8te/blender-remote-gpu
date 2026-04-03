"""HTTP client for render server b24.

b24 additions:
- viewport_render() now accepts samples parameter (for progressive rendering)
- update_camera() — lightweight camera-only update, no render

Connection methods tried in order (all Python builtins, zero external deps):
1. urllib.request (HTTP POST)
2. http.client (lower-level HTTP)
3. Raw TCP socket (length-prefixed JSON)
4. xmlrpc.client (XML-RPC over HTTP)
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
        self.method = ""

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
                    print(f"[Connection] Connected via {name} — {self.gpu_name}")
                    return
            except Exception as e:
                print(f"[Connection] {name} failed: {e}")
                continue

        self.error = "All connection methods failed"
        print(f"[Connection] {self.error}")

    def close(self):
        self.connected = False
        self.method = ""

    def send(self, data, timeout=30):
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
            return fn(data, timeout=timeout)
        except Exception as e:
            print(f"[Connection] Send error ({self.method}): {e}")
            self.error = str(e)
            return None

    # ── Method 1: urllib.request ──────────────────────────────

    def _try_urllib(self, data, timeout=5):
        import urllib.request

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
            sock.connect((self.host, self.port + 1))
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

        start = time.time()
        proxy = xmlrpc.client.ServerProxy(
            f"http://{self.host}:{self.port + 2}",
            allow_none=True,
        )
        proxy._ServerProxy__transport.timeout = timeout
        result = proxy.handle(json.dumps(data))
        self.latency_ms = int((time.time() - start) * 1000)
        return json.loads(result)

    # ── Scene + Render helpers ────────────────────────────────

    _scene_id = None

    def upload_scene(self, blend_data_b64):
        """Upload .blend to server, get scene_id for reuse."""
        result = self.send({
            "type": "scene_upload",
            "blend_data": blend_data_b64,
        }, timeout=60)
        if result and result.get("type") == "scene_cached":
            self._scene_id = result.get("scene_id")
            return self._scene_id
        return None

    def submit_render(self, blend_data_b64, width, height, samples):
        """Submit a render job, returns job_id."""
        result = self.send({
            "type": "render_submit",
            "blend_data": blend_data_b64,
            "width": width,
            "height": height,
            "samples": samples,
        }, timeout=60)
        if result and result.get("type") == "render_queued":
            return result.get("job_id")
        return None

    def submit_render_cached(self, scene_id, width, height, samples):
        """Submit render using cached scene (no upload needed)."""
        result = self.send({
            "type": "render_submit",
            "scene_id": scene_id,
            "width": width,
            "height": height,
            "samples": samples,
        }, timeout=60)
        if result and result.get("type") == "render_queued":
            return result.get("job_id")
        return None

    def poll_status(self, job_id):
        """Poll render job status."""
        return self.send({"type": "job_status", "job_id": job_id})

    def get_result(self, job_id):
        """Get render result (base64 PNG)."""
        return self.send({"type": "job_result", "job_id": job_id}, timeout=60)

    def viewport_render(self, scene_id, width, height, view_matrix, proj_matrix, samples=1):
        """Request a viewport render frame.

        b24: samples parameter for progressive rendering (1, 4, 16).
        Default is 1 — worker's OptiX denoiser makes 1spp look clean.
        """
        return self.send({
            "type": "viewport_render",
            "scene_id": scene_id,
            "width": width,
            "height": height,
            "samples": samples,
            "view_matrix": view_matrix,
            "proj_matrix": proj_matrix,
        }, timeout=15)

    def update_camera(self, view_matrix):
        """Lightweight camera-only update — no render triggered.
        Use this to pre-set camera position before viewport_render.
        b24 addition.
        """
        return self.send({
            "type": "camera_update",
            "view_matrix": view_matrix,
        }, timeout=5)

    def viewport_poll(self, scene_id):
        """Get latest viewport frame without starting new render."""
        return self.send({
            "type": "viewport_poll",
            "scene_id": scene_id,
        })
