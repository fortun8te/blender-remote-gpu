#!/usr/bin/env python3
"""
Blender Remote GPU Render Server — Unified HTTP + TCP + XMLRPC
Zero external dependencies. Handles render jobs via Blender CLI subprocess.
"""

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from xmlrpc.server import SimpleXMLRPCServer

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [Server] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

HTTP_PORT = 9876
SOCKET_PORT = 9877
XMLRPC_PORT = 9878

# ── GPU Detection ─────────────────────────────────────────────

GPU_NAME = "Unknown GPU"
GPU_VRAM = 0

def detect_gpu():
    global GPU_NAME, GPU_VRAM
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.free",
             "--format=csv,noheader,nounits"],
            timeout=5
        ).decode().strip()
        parts = out.split(",")
        GPU_NAME = parts[0].strip()
        GPU_VRAM = int(parts[1].strip())
        log.info(f"GPU: {GPU_NAME}, VRAM free: {GPU_VRAM} MB")
    except Exception as e:
        log.warning(f"GPU detection failed: {e}")

# ── Render Job Manager ────────────────────────────────────────

jobs = {}  # job_id -> job dict
jobs_lock = threading.Lock()

def find_blender():
    """Find Blender executable."""
    # Check PATH first
    blender = shutil.which("blender")
    if blender:
        return blender

    # Platform-specific paths
    candidates = []
    if sys.platform == "win32":
        for ver in ["5.0", "4.4", "4.3", "4.2", "4.1", "4.0"]:
            candidates.append(f"C:\\Program Files\\Blender Foundation\\Blender {ver}\\blender.exe")
    elif sys.platform == "darwin":
        candidates.append("/Applications/Blender.app/Contents/MacOS/Blender")
    else:
        candidates.extend(["/usr/bin/blender", "/snap/bin/blender"])

    for path in candidates:
        if os.path.isfile(path):
            return path

    return None

def run_render(job_id, blend_path, width, height, samples):
    """Render a .blend file using Blender CLI subprocess."""
    blender = find_blender()
    if not blender:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "Blender not found on server"
        return

    with jobs_lock:
        jobs[job_id]["status"] = "rendering"

    output_dir = tempfile.mkdtemp(prefix="render_")
    output_path = os.path.join(output_dir, "frame0001.png")

    # Python expression to configure render settings
    py_expr = (
        f"import bpy; "
        f"s = bpy.context.scene; "
        f"s.render.resolution_x = {width}; "
        f"s.render.resolution_y = {height}; "
        f"s.render.resolution_percentage = 100; "
        f"s.cycles.samples = {samples}; "
        f"s.cycles.device = 'GPU'; "
        f"s.render.filepath = '{output_dir}/frame'"
    )

    cmd = [
        blender, "-b", blend_path,
        "--python-expr", py_expr,
        "-F", "PNG", "-x", "1", "-f", "1"
    ]

    log.info(f"Job {job_id}: Starting render {width}x{height} @ {samples} samples")

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600  # 10 min max
        )

        if proc.returncode != 0:
            error_msg = proc.stderr[-500:] if proc.stderr else "Unknown error"
            with jobs_lock:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = f"Blender exited {proc.returncode}: {error_msg}"
            log.error(f"Job {job_id}: Render failed (exit {proc.returncode})")
            return

        if not os.path.isfile(output_path):
            with jobs_lock:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = "Render completed but output file not found"
            return

        # Read PNG and encode as base64
        with open(output_path, "rb") as f:
            png_data = f.read()

        with jobs_lock:
            jobs[job_id]["status"] = "complete"
            jobs[job_id]["result_b64"] = base64.b64encode(png_data).decode("ascii")
            jobs[job_id]["result_size"] = len(png_data)

        log.info(f"Job {job_id}: Complete ({len(png_data)} bytes)")

    except subprocess.TimeoutExpired:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "Render timed out (10 min limit)"
    except Exception as e:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)
    finally:
        # Cleanup
        try:
            shutil.rmtree(output_dir, ignore_errors=True)
            if os.path.isfile(blend_path):
                os.remove(blend_path)
        except Exception:
            pass


# ── Message Handler ───────────────────────────────────────────

def handle_message(data):
    """Process a message and return response."""
    msg_type = data.get("type", "unknown")

    if msg_type == "ping":
        return {
            "type": "pong",
            "gpu": GPU_NAME,
            "vram_free": GPU_VRAM,
            "timestamp": time.time(),
            "version": "2.0.0",
            "build": "b16",
        }

    elif msg_type == "render_submit":
        # Decode blend file from base64
        blend_b64 = data.get("blend_data", "")
        width = data.get("width", 1920)
        height = data.get("height", 1080)
        samples = data.get("samples", 128)

        if not blend_b64:
            return {"type": "error", "message": "No blend_data provided"}

        job_id = str(uuid.uuid4())[:8]

        # Save blend to temp file
        blend_data = base64.b64decode(blend_b64)
        blend_path = os.path.join(tempfile.gettempdir(), f"render_{job_id}.blend")
        with open(blend_path, "wb") as f:
            f.write(blend_data)

        with jobs_lock:
            jobs[job_id] = {
                "status": "queued",
                "submitted": time.time(),
                "width": width,
                "height": height,
                "samples": samples,
            }

        # Start render in background
        thread = threading.Thread(
            target=run_render,
            args=(job_id, blend_path, width, height, samples),
            daemon=True,
        )
        thread.start()

        log.info(f"Job {job_id}: Queued ({width}x{height} @ {samples}spp)")
        return {"type": "render_queued", "job_id": job_id}

    elif msg_type == "job_status":
        job_id = data.get("job_id", "")
        with jobs_lock:
            job = jobs.get(job_id)
        if not job:
            return {"type": "error", "message": f"Job {job_id} not found"}
        return {
            "type": "job_status",
            "job_id": job_id,
            "status": job["status"],
            "error": job.get("error", ""),
        }

    elif msg_type == "job_result":
        job_id = data.get("job_id", "")
        with jobs_lock:
            job = jobs.get(job_id)
        if not job:
            return {"type": "error", "message": f"Job {job_id} not found"}
        if job["status"] != "complete":
            return {"type": "error", "message": f"Job not complete: {job['status']}"}
        return {
            "type": "job_result",
            "job_id": job_id,
            "png_b64": job.get("result_b64", ""),
            "size": job.get("result_size", 0),
        }

    else:
        return {"type": "error", "message": f"Unknown message type: {msg_type}"}


# ── HTTP Server ───────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            log.info(f"HTTP {data.get('type')} from {self.client_address[0]}")

            response = handle_message(data)
            resp_bytes = json.dumps(response).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
        except Exception as e:
            log.error(f"HTTP error: {e}")
            err = json.dumps({"error": str(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def log_message(self, format, *args):
        pass


def run_http():
    server = HTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    log.info(f"HTTP on :{HTTP_PORT}")
    server.serve_forever()


# ── TCP Socket Server ─────────────────────────────────────────

def handle_tcp_client(conn, addr):
    try:
        raw_len = b""
        while len(raw_len) < 4:
            chunk = conn.recv(4 - len(raw_len))
            if not chunk:
                return
            raw_len += chunk

        length = int.from_bytes(raw_len, "big")
        if length > 50_000_000:
            return

        payload = b""
        while len(payload) < length:
            chunk = conn.recv(min(65536, length - len(payload)))
            if not chunk:
                return
            payload += chunk

        data = json.loads(payload.decode("utf-8"))
        log.info(f"TCP {data.get('type')} from {addr[0]}")

        response = handle_message(data)
        resp_bytes = json.dumps(response).encode("utf-8")
        conn.sendall(len(resp_bytes).to_bytes(4, "big"))
        conn.sendall(resp_bytes)
    except Exception as e:
        log.error(f"TCP error: {e}")
    finally:
        conn.close()


def run_tcp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", SOCKET_PORT))
    sock.listen(5)
    log.info(f"TCP on :{SOCKET_PORT}")
    while True:
        conn, addr = sock.accept()
        threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True).start()


# ── XML-RPC Server ────────────────────────────────────────────

def xmlrpc_handle(json_str):
    """Handle a JSON message via XML-RPC."""
    data = json.loads(json_str)
    log.info(f"XMLRPC {data.get('type')}")
    response = handle_message(data)
    return json.dumps(response)


def run_xmlrpc():
    server = SimpleXMLRPCServer(("0.0.0.0", XMLRPC_PORT), allow_none=True, logRequests=False)
    server.register_function(xmlrpc_handle, "handle")
    log.info(f"XMLRPC on :{XMLRPC_PORT}")
    server.serve_forever()


# ── Main ──────────────────────────────────────────────────────

def main():
    detect_gpu()

    log.info("=" * 50)
    log.info("Blender Remote GPU Render Server v2.0 (b16)")
    log.info(f"  HTTP:   http://0.0.0.0:{HTTP_PORT}")
    log.info(f"  TCP:    tcp://0.0.0.0:{SOCKET_PORT}")
    log.info(f"  XMLRPC: http://0.0.0.0:{XMLRPC_PORT}")
    log.info(f"  GPU:    {GPU_NAME} ({GPU_VRAM} MB free)")

    blender = find_blender()
    if blender:
        log.info(f"  Blender: {blender}")
    else:
        log.warning("  Blender: NOT FOUND (rendering disabled)")

    log.info("=" * 50)
    log.info("Server ready. Waiting for connections...")

    threads = [
        threading.Thread(target=run_http, daemon=True),
        threading.Thread(target=run_tcp, daemon=True),
        threading.Thread(target=run_xmlrpc, daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Server stopped")


if __name__ == "__main__":
    main()
