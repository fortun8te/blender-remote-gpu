#!/usr/bin/env python3
"""
Blender Remote GPU Render Server v3.0
Manages a persistent Blender render worker for near-live viewport rendering.
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
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from xmlrpc.server import SimpleXMLRPCServer

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [Server] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

HTTP_PORT   = 9876
SOCKET_PORT = 9877
XMLRPC_PORT = 9878
WORKER_PORT = 9880
BEACON_PORT = 9875   # UDP — Mac listens here for auto-discover

# ── GPU Detection ─────────────────────────────────────────────

GPU_NAME = "Unknown GPU"
GPU_VRAM = 0

def detect_gpu():
    global GPU_NAME, GPU_VRAM
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.free",
             "--format=csv,noheader,nounits"], timeout=5
        ).decode().strip()
        parts = out.split(",")
        GPU_NAME = parts[0].strip()
        GPU_VRAM = int(parts[1].strip())
    except Exception:
        pass

def find_blender():
    blender = shutil.which("blender")
    if blender:
        return blender
    candidates = []
    if sys.platform == "win32":
        for ver in ["5.0", "4.4", "4.3", "4.2", "4.1", "4.0"]:
            candidates.append(f"C:\\Program Files\\Blender Foundation\\Blender {ver}\\blender.exe")
    elif sys.platform == "darwin":
        candidates.append("/Applications/Blender.app/Contents/MacOS/Blender")
    else:
        candidates.extend(["/usr/bin/blender", "/snap/bin/blender"])
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


# ── Persistent Blender Worker ─────────────────────────────────

worker_process = None
worker_ready = False
_worker_lock = threading.Lock()  # Protects: worker_process, worker_ready, _worker_restart_attempts
_worker_restart_attempts = 0  # Track consecutive restart attempts

def start_worker():
    """Launch persistent Blender process with render_worker.py."""
    global worker_process, worker_ready

    blender = find_blender()
    if not blender:
        log.warning("Blender not found — worker disabled")
        return False

    worker_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_worker.py")
    if not os.path.isfile(worker_script):
        log.warning(f"render_worker.py not found at {worker_script}")
        return False

    env = os.environ.copy()
    env["WORKER_PORT"] = str(WORKER_PORT)
    # Add user site-packages to PYTHONPATH for Blender (workaround for isolated Python)
    user_site = os.path.expanduser(r"~\AppData\Roaming\Python\Python311\site-packages")
    if os.path.exists(user_site):
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = user_site + (";" + existing if existing else "")

    log.info(f"Starting persistent Blender worker...")
    with _worker_lock:
        worker_process = subprocess.Popen(
            [blender, "--background", "--python", worker_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )

    # Stream worker output in background
    def _stream_output():
        with _worker_lock:
            proc = worker_process
        for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                log.info(f"[Worker] {text}")
    threading.Thread(target=_stream_output, daemon=True).start()

    # Wait for worker to be ready
    for i in range(30):  # 30 second timeout
        time.sleep(1)
        if _ping_worker():
            with _worker_lock:
                worker_ready = True
            log.info(f"Worker ready on port {WORKER_PORT}")
            return True

    log.error("Worker failed to start within 30 seconds")
    return False


def _ping_worker():
    """Check if worker is responding."""
    try:
        data = json.dumps({"type": "ping"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://localhost:{WORKER_PORT}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result.get("type") == "pong"
    except Exception:
        return False


def _health_check_worker():
    """Health check endpoint — verifies worker is alive."""
    try:
        data = json.dumps({"type": "health"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://localhost:{WORKER_PORT}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result.get("status") == "ok"
    except Exception:
        return False


def send_to_worker(data, timeout=30):
    """Forward a request to the persistent Blender worker with auto-restart on failure."""
    global worker_process, _worker_restart_attempts

    # Check if worker process is dead
    with _worker_lock:
        proc = worker_process
        attempts = _worker_restart_attempts

    if proc and proc.poll() is not None:
        # Worker process is dead
        log.warning(f"Worker process died (attempt {attempts + 1}/3), attempting auto-restart...")

        if attempts < 3:
            with _worker_lock:
                _worker_restart_attempts += 1

            # Attempt to restart
            if start_worker():
                log.info("Worker auto-restart succeeded")
                time.sleep(1)  # Brief delay before retry
                # Retry the send
                try:
                    body = json.dumps(data).encode("utf-8")
                    req = urllib.request.Request(
                        f"http://localhost:{WORKER_PORT}",
                        data=body,
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except Exception as e:
                    return {"type": "error", "message": f"Worker error after restart: {e}"}
            else:
                log.error("Worker auto-restart failed")
                return {"type": "error", "message": "Worker auto-restart failed"}
        else:
            log.error("Worker restart attempts exhausted (max 3)")
            return {"type": "error", "message": "Worker restart attempts exhausted"}

    # Worker is alive, proceed with send
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            f"http://localhost:{WORKER_PORT}",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        # Reset restart attempts on successful send
        with _worker_lock:
            _worker_restart_attempts = 0
        return result
    except Exception as e:
        # Connection error — worker may have died
        log.warning(f"Send to worker failed: {e}")
        # Don't retry here — let the caller handle or next send attempt will trigger restart check
        return {"type": "error", "message": f"Worker error: {e}"}


# ── Jobs (for fallback subprocess rendering) ──────────────────

jobs = {}
jobs_lock = threading.Lock()


def run_render_subprocess(job_id, blend_path, width, height, samples):
    """Fallback: render via subprocess if worker is not available."""
    blender = find_blender()
    if not blender:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "Blender not found"
        return

    with jobs_lock:
        jobs[job_id]["status"] = "rendering"

    output_dir = tempfile.mkdtemp(prefix="render_")
    output_path = os.path.join(output_dir, "frame0001.png")
    out_escaped = output_dir.replace("\\", "/")

    py_expr = (
        f"import bpy; s = bpy.context.scene; "
        f"s.render.resolution_x={width}; s.render.resolution_y={height}; "
        f"s.render.resolution_percentage=100; s.cycles.samples={samples}; "
        f"s.cycles.device='GPU'; s.render.filepath='{out_escaped}/frame'"
    )
    cmd = [blender, "-b", blend_path, "--python-expr", py_expr,
           "-F", "PNG", "-x", "1", "-f", "1"]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode == 0 and os.path.isfile(output_path):
            with open(output_path, "rb") as f:
                png = f.read()
            with jobs_lock:
                jobs[job_id]["status"] = "complete"
                jobs[job_id]["result_b64"] = base64.b64encode(png).decode("ascii")
                jobs[job_id]["result_size"] = len(png)
        else:
            with jobs_lock:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = proc.stderr[-500:] if proc.stderr else "Unknown"
    except Exception as e:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


# ── Message Handler ───────────────────────────────────────────

def handle_message(data):
    msg_type = data.get("type", "unknown")

    if msg_type == "ping":
        # ATOMIC: Read worker_ready under lock
        with _worker_lock:
            ready = worker_ready
        return {
            "type": "pong",
            "gpu": GPU_NAME,
            "vram_free": GPU_VRAM,
            "timestamp": time.time(),
            "version": "3.0.0",
            "build": "b36",
            "worker_ready": ready,
        }

    # ── Health check ──
    elif msg_type == "health":
        return {
            "type": "health",
            "status": "ok",
            "timestamp": time.time(),
        }

    # ── Scene Upload → forward to worker ──
    elif msg_type == "scene_upload":
        blend_b64 = data.get("blend_data", "")
        if not blend_b64:
            return {"type": "error", "message": "No blend_data"}

        # Always save to a shared temp file first — worker reads from disk,
        # so we never send the large blob over the local socket
        scene_id   = str(uuid.uuid4())[:8]
        blend_data = base64.b64decode(blend_b64)
        blend_path = os.path.join(tempfile.gettempdir(), f"scene_{scene_id}.blend")
        with open(blend_path, "wb") as f:
            f.write(blend_data)
        size_mb = len(blend_data) / 1_048_576
        log.info(f"Scene saved to {blend_path} ({size_mb:.1f} MB)")

        # ATOMIC: Check worker_ready under lock
        with _worker_lock:
            ready = worker_ready
        if ready:
            # Send just the file PATH — no large data over the socket
            result = send_to_worker({"type": "load_scene_path", "path": blend_path}, timeout=10)
            if result is None:
                return {"type": "error", "message": "Worker did not respond"}

            if result.get("type") in ("scene_loading", "scene_loaded"):
                log.info("Scene queued on worker — polling until loaded...")
                start_time = time.time()
                last_state = None
                consecutive_errors = 0
                max_wait = 120  # seconds

                for attempt in range(int(max_wait * 10)):  # up to 120 seconds with 0.1s granularity
                    time.sleep(0.1)
                    ping = send_to_worker({"type": "ping"}, timeout=5)

                    if not ping:
                        consecutive_errors += 1
                        if consecutive_errors > 10:  # 1 second of failed pings
                            log.error("Worker lost connectivity during scene load")
                            return {"type": "error", "message": "Worker lost connectivity"}
                        continue
                    consecutive_errors = 0

                    scene_loaded = ping.get("scene_loaded", False)
                    scene_loading = ping.get("scene_loading", False)
                    current_state = (scene_loading, scene_loaded)

                    # Log state changes
                    if current_state != last_state:
                        elapsed = time.time() - start_time
                        log.info(f"[{elapsed:.1f}s] scene_loading={scene_loading}, scene_loaded={scene_loaded}")
                        last_state = current_state

                    if scene_loaded:
                        elapsed = time.time() - start_time
                        log.info(f"Worker scene ready ({elapsed:.1f}s)")
                        return {"type": "scene_cached", "scene_id": "worker", "objects": 0}

                    # Detect failed load early: state stopped changing and load failed
                    if not scene_loading and not scene_loaded and last_state and last_state[0]:
                        log.error("Worker load failed (scene_loading→False without scene_loaded→True)")
                        return {"type": "error", "message": "Worker failed to load scene — check Blender logs"}

                return {"type": "error", "message": f"Worker scene load timed out ({max_wait}s)"}

            return result  # pass through any error
        else:
            # No worker — file is on disk, scene_id is the path key
            return {"type": "scene_cached", "scene_id": scene_id}

    # ── Camera-only update (no render) — b24 addition ──
    elif msg_type == "camera_update":
        # ATOMIC: Check worker_ready under lock
        with _worker_lock:
            ready = worker_ready
        if not ready:
            return {"type": "error", "message": "Worker not ready"}
        return send_to_worker({
            "type": "update_camera",
            "view_matrix": data.get("view_matrix"),
        }, timeout=5)

    # ── Viewport Render → forward to worker (FAST path) ──
    elif msg_type == "viewport_render":
        # ATOMIC: Check worker_ready under lock
        with _worker_lock:
            ready = worker_ready
        if not ready:
            return {"type": "error", "message": "Worker not ready"}

        result = send_to_worker({
            "type": "render_frame",
            "width": data.get("width", 640),
            "height": data.get("height", 360),
            "samples": data.get("samples", 1),   # b24: default 1 (denoiser handles quality)
            "quality": data.get("quality", 75),   # b24: bumped quality
            "view_matrix": data.get("view_matrix"),
        }, timeout=15)

        if result.get("type") == "frame_result":
            return {
                "type": "viewport_result",
                "jpg_b64": result.get("jpg_b64", ""),
                "width": result.get("width"),
                "height": result.get("height"),
            }
        return result

    # ── F12 Final Render ──
    elif msg_type == "render_submit":
        blend_b64 = data.get("blend_data", "")
        scene_id = data.get("scene_id", "")
        width = data.get("width", 1920)
        height = data.get("height", 1080)
        samples = data.get("samples", 128)

        # If worker is ready and scene is loaded, use worker for final render too
        # ATOMIC: Check worker_ready under lock
        with _worker_lock:
            ready = worker_ready
        if ready and (scene_id == "worker" or not blend_b64):
            result = send_to_worker({
                "type": "render_final",
                "width": width,
                "height": height,
                "samples": samples,
            }, timeout=600)

            if result.get("type") == "render_result":
                job_id = str(uuid.uuid4())[:8]
                with jobs_lock:
                    jobs[job_id] = {
                        "status": "complete",
                        "result_b64": result.get("png_b64", ""),
                        "result_size": len(result.get("png_b64", "")),
                    }
                return {"type": "render_queued", "job_id": job_id}
            return result

        # Fallback: subprocess render
        if not blend_b64:
            return {"type": "error", "message": "No blend_data or worker scene"}

        job_id = str(uuid.uuid4())[:8]
        blend_data = base64.b64decode(blend_b64)
        path = os.path.join(tempfile.gettempdir(), f"render_{job_id}.blend")
        with open(path, "wb") as f:
            f.write(blend_data)

        with jobs_lock:
            jobs[job_id] = {"status": "queued"}

        threading.Thread(
            target=run_render_subprocess,
            args=(job_id, path, width, height, samples),
            daemon=True,
        ).start()

        return {"type": "render_queued", "job_id": job_id}

    elif msg_type == "job_status":
        job_id = data.get("job_id", "")
        with jobs_lock:
            job = jobs.get(job_id)
        if not job:
            return {"type": "error", "message": f"Job {job_id} not found"}
        return {"type": "job_status", "job_id": job_id,
                "status": job["status"], "error": job.get("error", "")}

    elif msg_type == "job_result":
        job_id = data.get("job_id", "")
        with jobs_lock:
            job = jobs.get(job_id)
        if not job:
            return {"type": "error", "message": f"Job {job_id} not found"}
        if job["status"] != "complete":
            return {"type": "error", "message": f"Not complete: {job['status']}"}
        return {"type": "job_result", "job_id": job_id,
                "png_b64": job.get("result_b64", ""), "size": job.get("result_size", 0)}

    else:
        return {"type": "error", "message": f"Unknown: {msg_type}"}


# ── HTTP Server ───────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            msg_type = data.get("type", "?")
            if msg_type not in ("viewport_render", "viewport_poll"):
                log.info(f"HTTP {msg_type} from {self.client_address[0]}")
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
    server.serve_forever()

# ── TCP Socket Server ─────────────────────────────────────────

def handle_tcp_client(conn, addr):
    try:
        raw_len = b""
        while len(raw_len) < 4:
            chunk = conn.recv(4 - len(raw_len))
            if not chunk: return
            raw_len += chunk
        length = int.from_bytes(raw_len, "big")
        if length > 100_000_000: return
        payload = b""
        while len(payload) < length:
            chunk = conn.recv(min(65536, length - len(payload)))
            if not chunk: return
            payload += chunk
        data = json.loads(payload.decode("utf-8"))
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
    while True:
        conn, addr = sock.accept()
        threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True).start()

# ── XML-RPC ───────────────────────────────────────────────────

def xmlrpc_handle(json_str):
    return json.dumps(handle_message(json.loads(json_str)))

def run_xmlrpc():
    server = SimpleXMLRPCServer(("0.0.0.0", XMLRPC_PORT), allow_none=True, logRequests=False)
    server.register_function(xmlrpc_handle, "handle")
    server.serve_forever()

# ── Main ──────────────────────────────────────────────────────

def run_beacon():
    """UDP beacon — broadcasts server presence every 2s so Mac can auto-discover.
    Works over Thunderbolt 4 direct cable (169.254.x.x link-local) and LAN.
    """
    import socket as _socket
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_BROADCAST, 1)
    sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    msg = json.dumps({
        "type": "remote_gpu_beacon",
        "port": HTTP_PORT,
        "gpu":  GPU_NAME,
        "build": "b34",
    }).encode("utf-8")
    log.info(f"UDP beacon broadcasting on port {BEACON_PORT}")
    while True:
        try:
            sock.sendto(msg, ("255.255.255.255", BEACON_PORT))
        except Exception:
            pass
        time.sleep(2)


def main():
    detect_gpu()
    blender = find_blender()

    log.info("=" * 55)
    log.info("Blender Remote GPU Render Server v3.0 (b34)")
    log.info(f"  GPU:     {GPU_NAME} ({GPU_VRAM} MB)")
    log.info(f"  Blender: {blender or 'NOT FOUND'}")
    log.info(f"  HTTP:    :{HTTP_PORT}  TCP: :{SOCKET_PORT}  XMLRPC: :{XMLRPC_PORT}")
    log.info(f"  Worker:  :{WORKER_PORT} (persistent Blender process)")
    log.info("=" * 55)

    # Start protocol servers + beacon
    for fn in [run_http, run_tcp, run_xmlrpc, run_beacon]:
        threading.Thread(target=fn, daemon=True).start()

    log.info("Protocol servers started")

    # Start persistent worker
    if blender:
        if start_worker():
            log.info("LIVE viewport rendering enabled (persistent worker)")
        else:
            log.warning("Worker failed — falling back to subprocess rendering")
    else:
        log.warning("No Blender — rendering disabled")

    log.info("Server ready")

    try:
        while True:
            time.sleep(1)
            # Check if worker is still alive (atomic read under lock)
            with _worker_lock:
                proc = worker_process
            if proc and proc.poll() is not None:
                log.warning("Worker died — restarting...")
                start_worker()
    except KeyboardInterrupt:
        log.info("Shutting down")
        # ATOMIC: Get worker_process under lock
        with _worker_lock:
            proc = worker_process
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
