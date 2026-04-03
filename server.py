#!/usr/bin/env python3
"""
Blender Remote GPU Render Server v3.0
Manages a persistent Blender render worker for near-live viewport rendering.
"""

import json
import logging
import os
import shutil
import signal
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
from logging.handlers import RotatingFileHandler

# ── Structured Logging Setup ───────────────────────────────────
# File-based logging with rotation (10MB per file, keep 5 files)
_log_dir = tempfile.gettempdir()
_log_file = os.path.join(_log_dir, "blender_server.log")
_formatter = logging.Formatter('[%(asctime)s] [Server] [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
_logger = logging.getLogger('blender_server')
_logger.setLevel(logging.DEBUG)

# Console handler
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)
_logger.addHandler(_console_handler)

# Rotating file handler
try:
    _file_handler = RotatingFileHandler(_log_file, maxBytes=10*1024*1024, backupCount=5)
    _file_handler.setFormatter(_formatter)
    _logger.addHandler(_file_handler)
except Exception as e:
    print(f"[LOGGING_ERROR] Could not setup file logging: {e}", flush=True)

log = _logger  # Use structured logger

# ── Request/Response Metrics (thread-safe) ─────────────────────
_request_metrics = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "error_counts": {},  # Count by error type
    "last_request_time": None,
    "avg_response_time_ms": 0,
    "response_times": [],  # Last 100 response times for rolling average
}
_request_metrics_lock = threading.Lock()
_last_request_log_time = time.time()
_request_log_interval = 30  # Log request metrics every 30 seconds

def _record_request(msg_type, response_time_ms, success=True, error_type=None):
    """Record HTTP request metrics (thread-safe)."""
    global _last_request_log_time
    with _request_metrics_lock:
        _request_metrics["total_requests"] += 1
        _request_metrics["last_request_time"] = time.time()

        if success:
            _request_metrics["successful_requests"] += 1
        else:
            _request_metrics["failed_requests"] += 1
            if error_type:
                _request_metrics["error_counts"][error_type] = _request_metrics["error_counts"].get(error_type, 0) + 1

        # Track rolling average of response times (last 100)
        _request_metrics["response_times"].append(response_time_ms)
        if len(_request_metrics["response_times"]) > 100:
            _request_metrics["response_times"].pop(0)
        if _request_metrics["response_times"]:
            _request_metrics["avg_response_time_ms"] = sum(_request_metrics["response_times"]) / len(_request_metrics["response_times"])

        # Log request metrics summary every 30 seconds
        if time.time() - _last_request_log_time >= _request_log_interval:
            success_rate = (_request_metrics["successful_requests"] / _request_metrics["total_requests"] * 100) if _request_metrics["total_requests"] > 0 else 0
            error_summary = ", ".join([f"{k}: {v}" for k, v in list(_request_metrics["error_counts"].items())[:3]])
            log.info(f"[METRICS] Total: {_request_metrics['total_requests']}, Success: {success_rate:.1f}%, "
                    f"Avg Response: {_request_metrics['avg_response_time_ms']:.0f}ms, "
                    f"Errors: {error_summary if error_summary else 'none'}")
            _last_request_log_time = time.time()


# ── Input Validation ──────────────────────────────────────────

def validate_base64(data_b64, max_size_mb=500):
    """
    Validate base64-encoded data.

    Checks:
    - data_b64 is valid base64
    - decoded size does not exceed max_size_mb

    Args:
        data_b64: Base64 string
        max_size_mb: Maximum size in megabytes (default 500)

    Returns:
        (is_valid: bool, decoded_data: bytes or None, message: str)
    """
    if not data_b64:
        return False, None, "No data provided"

    if not isinstance(data_b64, str):
        return False, None, "Data must be string"

    try:
        decoded = base64.b64decode(data_b64, validate=True)
    except Exception as e:
        return False, None, f"Invalid base64 encoding: {str(e)[:100]}"

    size_mb = len(decoded) / 1_048_576
    if size_mb > max_size_mb:
        return False, None, f"Data too large ({size_mb:.1f}MB > {max_size_mb}MB limit)"

    return True, decoded, "OK"


def validate_json_message(data):
    """
    Validate JSON message structure.

    Checks:
    - data is dict
    - has required 'type' field

    Returns:
        (is_valid: bool, message: str)
    """
    if not isinstance(data, dict):
        return False, "Message must be JSON object"

    if "type" not in data:
        return False, "Message missing required 'type' field"

    return True, "OK"

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
_worker_lock = threading.Lock()  # Protects: worker_process, worker_ready, _worker_restart_attempts, _worker_corrupted
_worker_restart_attempts = 0  # Track consecutive restart attempts
_worker_corrupted = False  # Track if worker is in a bad state (scene load failed, etc.)


def _mark_worker_corrupted(reason=""):
    """Mark worker as corrupted and log reason."""
    global _worker_corrupted
    with _worker_lock:
        if not _worker_corrupted:
            _worker_corrupted = True
            log.error(f"[WORKER_CORRUPTION] Worker marked corrupted: {reason}")


def _clear_worker_corruption():
    """Clear corruption flag (e.g., after successful restart)."""
    global _worker_corrupted
    with _worker_lock:
        if _worker_corrupted:
            _worker_corrupted = False
            log.info(f"[WORKER_CORRUPTION] Worker corruption cleared after restart")

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
            _clear_worker_corruption()  # Clear corruption flag on successful start
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


def send_to_worker(data, timeout=30, render_retry=False):
    """
    Forward a request to the persistent Blender worker with auto-restart on failure.

    Args:
        data: JSON request data
        timeout: Request timeout in seconds
        render_retry: If True, retry render failures up to 2 times after reconnect

    Returns:
        Response dict from worker
    """
    global worker_process, _worker_restart_attempts

    # Check if worker process is dead
    with _worker_lock:
        proc = worker_process
        attempts = _worker_restart_attempts

    if proc and proc.poll() is not None:
        # Worker process is dead
        log.warning(f"[WORKER_FALLBACK] Worker process died (attempt {attempts + 1}/3), attempting auto-restart...")

        if attempts < 3:
            with _worker_lock:
                _worker_restart_attempts += 1

            # Attempt to restart
            if start_worker():
                log.info("[WORKER_FALLBACK] Worker auto-restart succeeded")
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
                        result = json.loads(resp.read().decode("utf-8"))
                    return result
                except Exception as e:
                    log.error(f"[WORKER_FALLBACK] Send after restart failed: {e}")
                    return {"type": "error", "message": f"Worker error after restart: {e}"}
            else:
                log.error("[WORKER_FALLBACK] Worker auto-restart failed")
                _mark_worker_corrupted("Failed to auto-restart after crash")
                return {"type": "error", "message": "Worker auto-restart failed"}
        else:
            log.error("[WORKER_FALLBACK] Worker restart attempts exhausted (max 3)")
            _mark_worker_corrupted("Restart attempts exhausted")
            return {"type": "error", "message": "Worker restart attempts exhausted"}

    # Worker is alive, proceed with send
    for retry_attempt in range(3 if render_retry else 1):
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
        except urllib.error.HTTPError as e:
            # HTTP error — could be render timeout or other issue
            if render_retry and retry_attempt < 2 and e.code in (500, 502, 504):
                log.warning(f"[RENDER_FALLBACK] HTTP {e.code} on render attempt {retry_attempt + 1}/3, retrying after reconnect...")
                # Try to reconnect
                time.sleep(1)
                continue
            else:
                log.warning(f"[WORKER_ERROR] HTTP error {e.code}: {e}")
                return {"type": "error", "message": f"Worker HTTP error: {e.code}"}
        except (urllib.error.URLError, Exception) as e:
            # Connection error — worker may have died or timed out
            if render_retry and retry_attempt < 2 and "timeout" in str(e).lower():
                log.warning(f"[RENDER_FALLBACK] Render timeout on attempt {retry_attempt + 1}/3, retrying after reconnect...")
                # Auto-restart and retry
                if start_worker():
                    log.info("[RENDER_FALLBACK] Reconnected after timeout")
                    time.sleep(1)
                    continue
                else:
                    log.error("[RENDER_FALLBACK] Failed to reconnect after timeout")
                    _mark_worker_corrupted("Failed to reconnect after render timeout")
                    return {"type": "error", "message": "Failed to reconnect after render timeout"}
            else:
                log.warning(f"[WORKER_ERROR] Send to worker failed: {e}")
                return {"type": "error", "message": f"Worker error: {e}"}

    # All retries exhausted
    if render_retry:
        log.error("[RENDER_FALLBACK] All render retry attempts exhausted")
        _mark_worker_corrupted("Render failed after 3 attempts")
        return {"type": "error", "message": "Render failed after 3 retry attempts"}


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

        # Validate base64 and size (500MB limit)
        is_valid, blend_data, validation_msg = validate_base64(blend_b64, max_size_mb=500)
        if not is_valid:
            log.error(f"[VALIDATION_ERROR] scene_upload: {validation_msg}")
            return {"type": "error", "message": f"Invalid scene data: {validation_msg}"}

        # Always save to a shared temp file first — worker reads from disk,
        # so we never send the large blob over the local socket
        scene_id   = str(uuid.uuid4())[:8]
        blend_path = os.path.join(tempfile.gettempdir(), f"scene_{scene_id}.blend")
        with open(blend_path, "wb") as f:
            f.write(blend_data)
        size_mb = len(blend_data) / 1_048_576
        log.info(f"Scene saved to {blend_path} ({size_mb:.1f} MB)")

        # ATOMIC: Check worker_ready under lock
        with _worker_lock:
            ready = worker_ready
        if ready:
            # Try to load scene with fallback: clear scene and retry if first attempt fails
            for load_attempt in range(2):
                # Send just the file PATH — no large data over the socket
                result = send_to_worker({"type": "load_scene_path", "path": blend_path}, timeout=10)
                if result is None:
                    return {"type": "error", "message": "Worker did not respond"}

                if result.get("type") in ("scene_loading", "scene_loaded"):
                    log.info(f"Scene queued on worker (attempt {load_attempt + 1}/2) — polling until loaded...")
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
                                log.error("[SCENE_LOAD] Worker lost connectivity during scene load")
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
                            log.info(f"[SCENE_LOAD] Worker scene ready ({elapsed:.1f}s)")
                            return {"type": "scene_cached", "scene_id": "worker", "objects": 0}

                        # Detect failed load early: state stopped changing and load failed
                        if not scene_loading and not scene_loaded and last_state and last_state[0]:
                            log.error("[SCENE_LOAD_FALLBACK] Worker load failed (scene_loading→False without scene_loaded→True)")

                            # If this is the first attempt, try fallback: clear scene and retry
                            if load_attempt == 0:
                                log.info("[SCENE_LOAD_FALLBACK] Attempting fallback: clear scene and retry...")
                                _mark_worker_corrupted("Scene load failed, will try clear/reload")
                                # Try to clear the scene (this may fail if worker is really broken)
                                try:
                                    send_to_worker({"type": "clear_scene"}, timeout=5)
                                except Exception as e:
                                    log.warning(f"[SCENE_LOAD_FALLBACK] Could not clear scene: {e}")
                                # Continue to next load_attempt (which will retry)
                                break
                            else:
                                return {"type": "error", "message": "Worker failed to load scene — worker is in bad state, try reconnecting"}

                    if load_attempt < 1:
                        # First attempt timed out, but we didn't catch early failure
                        log.warning(f"[SCENE_LOAD_FALLBACK] Scene load timed out on attempt 1/2, retrying...")
                        _mark_worker_corrupted("Scene load timeout, will try clear/reload")
                        # Try clear and retry
                        try:
                            send_to_worker({"type": "clear_scene"}, timeout=5)
                        except Exception as e:
                            log.warning(f"[SCENE_LOAD_FALLBACK] Could not clear scene: {e}")
                        continue
                    else:
                        return {"type": "error", "message": f"Worker scene load timed out after fallback ({max_wait}s)"}

                # If we get here with an error, try fallback on first attempt
                if load_attempt == 0 and result.get("type") == "error":
                    log.info("[SCENE_LOAD_FALLBACK] Scene load returned error, attempting fallback...")
                    _mark_worker_corrupted(f"Scene load error: {result.get('message', 'unknown')}")
                    try:
                        send_to_worker({"type": "clear_scene"}, timeout=5)
                    except Exception as e:
                        log.warning(f"[SCENE_LOAD_FALLBACK] Could not clear scene: {e}")
                    continue

                # Final attempt failed, pass through error
                return result
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

        # Render with fallback: auto-reconnect and retry up to 2 times on timeout
        result = send_to_worker({
            "type": "render_frame",
            "width": data.get("width", 640),
            "height": data.get("height", 360),
            "samples": data.get("samples", 1),   # b24: default 1 (denoiser handles quality)
            "quality": data.get("quality", 75),   # b24: bumped quality
            "view_matrix": data.get("view_matrix"),
        }, timeout=15, render_retry=True)

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
            # Final render with fallback: auto-reconnect and retry up to 2 times on timeout
            result = send_to_worker({
                "type": "render_final",
                "width": width,
                "height": height,
                "samples": samples,
            }, timeout=600, render_retry=True)

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

        # Validate base64 and size (500MB limit)
        is_valid, blend_data, validation_msg = validate_base64(blend_b64, max_size_mb=500)
        if not is_valid:
            log.error(f"[VALIDATION_ERROR] render_submit fallback: {validation_msg}")
            return {"type": "error", "message": f"Invalid scene data: {validation_msg}"}

        job_id = str(uuid.uuid4())[:8]
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
        request_start_time = time.time()
        client_ip = self.client_address[0]
        msg_type = "unknown"
        response_status = 500
        is_success = False

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))

            # Validate JSON message structure
            is_valid, validation_msg = validate_json_message(data)
            if not is_valid:
                log.error(f"[VALIDATION_ERROR] HTTP: {validation_msg} from {client_ip}")
                response = {"type": "error", "message": f"Invalid message: {validation_msg}"}
                response_status = 400
                _record_request("invalid_json", 0, success=False, error_type="validation_error")
            else:
                msg_type = data.get("type", "?")
                response = handle_message(data)

                # Determine success based on response type
                response_type = response.get("type", "")
                is_success = response_type != "error"
                response_status = 200

                # Log all requests with client IP and message type (except high-frequency ones)
                response_time_ms = int((time.time() - request_start_time) * 1000)
                if msg_type not in ("viewport_render", "viewport_poll"):
                    status = "OK" if is_success else "ERROR"
                    log.info(f"[HTTP] [{client_ip}] {msg_type} {status} in {response_time_ms}ms")

                _record_request(msg_type, response_time_ms, success=is_success,
                               error_type=response_type if not is_success else None)

            resp_bytes = json.dumps(response).encode("utf-8")
            self.send_response(response_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)

        except json.JSONDecodeError as e:
            response_time_ms = int((time.time() - request_start_time) * 1000)
            log.error(f"[HTTP] [{client_ip}] JSON decode error: {e} in {response_time_ms}ms")
            _record_request("json_decode", response_time_ms, success=False, error_type="json_decode_error")
            err = json.dumps({"error": f"Invalid JSON: {str(e)}"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
        except Exception as e:
            response_time_ms = int((time.time() - request_start_time) * 1000)
            log.error(f"[HTTP] [{client_ip}] Exception: {type(e).__name__}: {e} in {response_time_ms}ms")
            _record_request(msg_type, response_time_ms, success=False, error_type=type(e).__name__)
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
    request_start_time = time.time()
    client_ip = addr[0] if addr else "unknown"
    msg_type = "unknown"
    is_success = False

    try:
        raw_len = b""
        while len(raw_len) < 4:
            chunk = conn.recv(4 - len(raw_len))
            if not chunk: return
            raw_len += chunk
        length = int.from_bytes(raw_len, "big")
        if length > 100_000_000:
            log.error(f"[TCP] [{client_ip}] Message size exceeds 100MB limit")
            _record_request("tcp_oversized", int((time.time() - request_start_time) * 1000), success=False, error_type="oversized_message")
            return

        payload = b""
        while len(payload) < length:
            chunk = conn.recv(min(65536, length - len(payload)))
            if not chunk: return
            payload += chunk
        data = json.loads(payload.decode("utf-8"))

        # Validate JSON message structure
        is_valid, validation_msg = validate_json_message(data)
        if not is_valid:
            log.error(f"[TCP] [{client_ip}] Validation error: {validation_msg}")
            response = {"type": "error", "message": f"Invalid message: {validation_msg}"}
            _record_request("tcp_validation", int((time.time() - request_start_time) * 1000), success=False, error_type="validation_error")
        else:
            msg_type = data.get("type", "?")
            response = handle_message(data)
            is_success = response.get("type") != "error"
            response_time_ms = int((time.time() - request_start_time) * 1000)
            _record_request(msg_type, response_time_ms, success=is_success, error_type=response.get("type") if not is_success else None)
            log.info(f"[TCP] [{client_ip}] {msg_type} {'OK' if is_success else 'ERROR'} in {response_time_ms}ms")

        resp_bytes = json.dumps(response).encode("utf-8")
        conn.sendall(len(resp_bytes).to_bytes(4, "big"))
        conn.sendall(resp_bytes)
    except json.JSONDecodeError as e:
        response_time_ms = int((time.time() - request_start_time) * 1000)
        log.error(f"[TCP] [{client_ip}] JSON decode error: {e} in {response_time_ms}ms")
        _record_request("tcp_json_decode", response_time_ms, success=False, error_type="json_decode_error")
    except Exception as e:
        response_time_ms = int((time.time() - request_start_time) * 1000)
        log.error(f"[TCP] [{client_ip}] Exception: {type(e).__name__}: {e} in {response_time_ms}ms")
        _record_request(msg_type, response_time_ms, success=False, error_type=type(e).__name__)
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

# ── Graceful Shutdown ─────────────────────────────────────────

def shutdown_worker():
    """Gracefully shutdown the persistent worker process."""
    global worker_process

    with _worker_lock:
        proc = worker_process

    if not proc:
        log.info("[SHUTDOWN] Worker process is not running")
        return

    # Check if already dead
    if proc.poll() is not None:
        log.info("[SHUTDOWN] Worker process already dead")
        return

    log.info("[SHUTDOWN] Terminating worker process (SIGTERM)...")
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
            log.info("[SHUTDOWN] Worker process terminated gracefully")
        except subprocess.TimeoutExpired:
            log.warning("[SHUTDOWN] Worker process did not exit after 5s, sending SIGKILL...")
            proc.kill()
            try:
                proc.wait(timeout=2)
                log.info("[SHUTDOWN] Worker process killed")
            except subprocess.TimeoutExpired:
                log.error("[SHUTDOWN] Worker process still alive after SIGKILL (should not happen)")
    except Exception as e:
        log.error(f"[SHUTDOWN] Error terminating worker: {e}")


def shutdown_handler(signum, frame):
    """Signal handler for graceful shutdown."""
    log.info("[SHUTDOWN] Received SIGINT (Ctrl+C)")

    # Log final metrics summary
    try:
        with _request_metrics_lock:
            success_rate = (_request_metrics["successful_requests"] / _request_metrics["total_requests"] * 100) if _request_metrics["total_requests"] > 0 else 0
            error_summary = ", ".join([f"{k}: {v}" for k, v in list(_request_metrics["error_counts"].items())[:5]])
            log.info(f"[SHUTDOWN_METRICS] Total requests: {_request_metrics['total_requests']}, "
                    f"Success rate: {success_rate:.1f}%, "
                    f"Avg response: {_request_metrics['avg_response_time_ms']:.0f}ms, "
                    f"Top errors: {error_summary if error_summary else 'none'}")
    except Exception as e:
        log.warning(f"[SHUTDOWN] Error logging final metrics: {e}")

    shutdown_worker()
    log.info("[SHUTDOWN] Shutdown complete, exiting...")
    sys.exit(0)


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
    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGINT, shutdown_handler)

    detect_gpu()
    blender = find_blender()

    log.info("=" * 55)
    log.info("Blender Remote GPU Render Server v3.0 (b36)")
    log.info(f"  GPU:     {GPU_NAME} ({GPU_VRAM} MB)")
    log.info(f"  Blender: {blender or 'NOT FOUND'}")
    log.info(f"  HTTP:    :{HTTP_PORT}  TCP: :{SOCKET_PORT}  XMLRPC: :{XMLRPC_PORT}")
    log.info(f"  Worker:  :{WORKER_PORT} (persistent Blender process)")
    log.info(f"  Log file: {_log_file}")
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
        log.info("[SHUTDOWN] KeyboardInterrupt caught in main loop")
        shutdown_worker()
        log.info("[SHUTDOWN] Server shutdown complete")
        sys.exit(0)


if __name__ == "__main__":
    main()
