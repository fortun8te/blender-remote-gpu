#!/usr/bin/env python3
"""
Blender Remote GPU Render Server v4.0 — DISPATCHER PATTERN
Stateless job dispatcher with worker pool.
Each render job spawns a fresh Blender subprocess.
No persistent worker (eliminates blocking/state leakage).
"""

import base64
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
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any
from job_queue import JobQueue

# ── Logging Setup ──────────────────────────────────────────────────

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

log = _logger

# ── Configuration ──────────────────────────────────────────────────

HTTP_PORT = 9876
SOCKET_PORT = 9877
XMLRPC_PORT = 9878
BEACON_PORT = 9875

NUM_WORKERS = 3  # Number of worker threads
RESULT_TTL_SECONDS = 300  # Cache render results for 5 minutes

# ── Global State ───────────────────────────────────────────────────

job_queue = JobQueue(max_workers=NUM_WORKERS, result_ttl_seconds=RESULT_TTL_SECONDS)
_request_metrics = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "error_counts": {},
    "last_request_time": None,
    "avg_response_time_ms": 0,
    "response_times": [],
}
_request_metrics_lock = threading.Lock()
_last_request_log_time = time.time()
_request_log_interval = 30

GPU_NAME = "Unknown GPU"
GPU_VRAM = 0

# ── GPU Detection ──────────────────────────────────────────────────

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

# ── Input Validation ───────────────────────────────────────────────

def validate_base64(data_b64, max_size_mb=500):
    """Validate base64-encoded data."""
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
    """Validate JSON message structure."""
    if not isinstance(data, dict):
        return False, "Message must be JSON object"
    if "type" not in data:
        return False, "Message missing required 'type' field"
    return True, "OK"

# ── Metrics ────────────────────────────────────────────────────────

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

        _request_metrics["response_times"].append(response_time_ms)
        if len(_request_metrics["response_times"]) > 100:
            _request_metrics["response_times"].pop(0)
        if _request_metrics["response_times"]:
            _request_metrics["avg_response_time_ms"] = sum(_request_metrics["response_times"]) / len(_request_metrics["response_times"])

        if time.time() - _last_request_log_time >= _request_log_interval:
            success_rate = (_request_metrics["successful_requests"] / _request_metrics["total_requests"] * 100) if _request_metrics["total_requests"] > 0 else 0
            error_summary = ", ".join([f"{k}: {v}" for k, v in list(_request_metrics["error_counts"].items())[:3]])
            log.info(f"[METRICS] Total: {_request_metrics['total_requests']}, Success: {success_rate:.1f}%, "
                    f"Avg Response: {_request_metrics['avg_response_time_ms']:.0f}ms, "
                    f"Errors: {error_summary if error_summary else 'none'}")
            _last_request_log_time = time.time()

# ── Worker Pool Thread ─────────────────────────────────────────────

def worker_thread(worker_id: int):
    """
    Worker thread: continuously poll job queue and execute renders.
    Each job spawns a fresh Blender subprocess.
    """
    log.info(f"[WORKER-{worker_id}] Started")
    blender = find_blender()
    if not blender:
        log.error(f"[WORKER-{worker_id}] Blender not found, exiting")
        return

    worker_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "single_job_worker.py")
    if not os.path.isfile(worker_script):
        log.error(f"[WORKER-{worker_id}] single_job_worker.py not found, exiting")
        return

    while True:
        # Poll for next job
        job = job_queue.get_next_job()
        if not job:
            time.sleep(0.5)  # Poll interval
            continue

        job_id, job_type, params = job
        log.info(f"[WORKER-{worker_id}] Processing job {job_id} ({job_type})")

        try:
            # Prepare job data
            job_data = {
                "type": job_type,
                **params
            }
            job_json = json.dumps(job_data)

            # Spawn Blender subprocess
            env = os.environ.copy()
            env["RENDER_JOB_JSON"] = job_json

            log.info(f"[WORKER-{worker_id}] Spawning Blender for job {job_id}")
            proc = subprocess.Popen(
                [blender, "--background", "--python", worker_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
            )

            # Wait for completion (with timeout)
            job_timeout = params.get("timeout", 3600)  # Default 1 hour
            try:
                stdout, _ = proc.communicate(timeout=job_timeout)
                return_code = proc.returncode
                output = stdout.decode('utf-8', errors='replace') if stdout else ""

                if return_code == 0:
                    log.info(f"[WORKER-{worker_id}] Job {job_id} completed successfully")
                    result = {
                        "type": "render_complete",
                        "job_id": job_id,
                        "status": "success",
                    }

                    # Try to extract output path from params or result
                    if "output_path" in params and os.path.isfile(params["output_path"]):
                        result["output_path"] = params["output_path"]
                        result["file_size_mb"] = os.path.getsize(params["output_path"]) / (1024 * 1024)

                    job_queue.set_result(job_id, result)
                else:
                    log.error(f"[WORKER-{worker_id}] Job {job_id} failed with return code {return_code}")
                    job_queue.set_error(job_id, f"Render failed (exit code {return_code})")

            except subprocess.TimeoutExpired:
                proc.kill()
                log.error(f"[WORKER-{worker_id}] Job {job_id} timed out after {job_timeout}s")
                job_queue.set_error(job_id, f"Render timeout ({job_timeout}s)")

        except Exception as e:
            log.error(f"[WORKER-{worker_id}] Exception processing job {job_id}: {type(e).__name__}: {e}")
            job_queue.set_error(job_id, f"{type(e).__name__}: {str(e)[:100]}")

# ── Message Handler ────────────────────────────────────────────────

def handle_message(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle incoming messages and dispatch to appropriate handler."""
    msg_type = data.get("type", "unknown")

    # ── Ping / Health ──
    if msg_type == "ping":
        return {
            "type": "pong",
            "gpu": GPU_NAME,
            "vram_free": GPU_VRAM,
            "timestamp": time.time(),
            "version": "4.0.0",
            "build": "dispatcher",
            "worker_ready": True,  # Always true for dispatcher (stateless)
        }

    elif msg_type == "health":
        queue_status = job_queue.get_queue_status()
        return {
            "type": "health",
            "status": "ok",
            "timestamp": time.time(),
            "queue": queue_status,
        }

    # ── Scene Upload ──
    elif msg_type == "scene_upload":
        blend_b64 = data.get("blend_data", "")
        is_valid, blend_data, validation_msg = validate_base64(blend_b64, max_size_mb=500)
        if not is_valid:
            log.error(f"[VALIDATION_ERROR] scene_upload: {validation_msg}")
            return {"type": "error", "message": f"Invalid scene data: {validation_msg}"}

        # Save scene to temp file
        scene_id = str(uuid.uuid4())[:8]
        blend_path = os.path.join(tempfile.gettempdir(), f"scene_{scene_id}.blend")
        with open(blend_path, "wb") as f:
            f.write(blend_data)
        size_mb = len(blend_data) / 1_048_576
        log.info(f"Scene saved to {blend_path} ({size_mb:.1f} MB)")

        return {
            "type": "scene_cached",
            "scene_id": scene_id,
            "blend_path": blend_path,
            "size_mb": size_mb,
        }

    # ── Viewport Render (submit job to queue) ──
    elif msg_type == "viewport_render":
        blend_path = data.get("blend_path") or data.get("path")
        if not blend_path:
            return {"type": "error", "message": "Missing blend_path"}

        if not os.path.isfile(blend_path):
            return {"type": "error", "message": f"Blend file not found: {blend_path}"}

        # Create output path
        output_dir = tempfile.gettempdir()
        render_id = str(uuid.uuid4())[:8]
        output_path = os.path.join(output_dir, f"render_{render_id}.png")

        # Submit to queue
        job_params = {
            "blend_file": blend_path,
            "output_path": output_path,
            "frame": data.get("frame", 1),
            "view_matrix": data.get("view_matrix"),
            "timeout": data.get("timeout", 3600),
        }

        job_id = job_queue.submit_job("viewport_render", job_params)
        log.info(f"Render job {job_id} submitted")

        return {
            "type": "job_submitted",
            "job_id": job_id,
            "status": "queued",
        }

    # ── Get Job Status ──
    elif msg_type == "job_status":
        job_id = data.get("job_id")
        if not job_id:
            return {"type": "error", "message": "Missing job_id"}

        status = job_queue.get_status(job_id)
        return {
            "type": "job_status",
            "job_id": job_id,
            "status": status,
        }

    # ── Get Job Result ──
    elif msg_type == "job_result":
        job_id = data.get("job_id")
        if not job_id:
            return {"type": "error", "message": "Missing job_id"}

        result = job_queue.get_result(job_id)
        if result is None:
            status = job_queue.get_status(job_id)
            if status == "not_found":
                return {"type": "error", "message": f"Job {job_id} not found"}
            return {"type": "error", "message": f"Result not ready (status: {status})"}

        return result

    # ── Queue Status ──
    elif msg_type == "queue_status":
        return {
            "type": "queue_status",
            **job_queue.get_queue_status(),
        }

    # ── Cleanup (for testing) ──
    elif msg_type == "cleanup_expired":
        expired = job_queue.clear_expired_results()
        return {
            "type": "cleanup_complete",
            "expired_removed": expired,
        }

    else:
        return {"type": "error", "message": f"Unknown message type: {msg_type}"}

# ── HTTP Handler ───────────────────────────────────────────────────

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

            is_valid, validation_msg = validate_json_message(data)
            if not is_valid:
                log.error(f"[VALIDATION_ERROR] HTTP: {validation_msg} from {client_ip}")
                response = {"type": "error", "message": f"Invalid message: {validation_msg}"}
                response_status = 400
                _record_request("invalid_json", 0, success=False, error_type="validation_error")
            else:
                msg_type = data.get("type", "?")
                response = handle_message(data)

                response_type = response.get("type", "")
                is_success = response_type != "error"
                response_status = 200

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
        pass  # Suppress default HTTP logging

def run_http():
    log.info(f"HTTP server starting on port {HTTP_PORT}")
    server = HTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    server.serve_forever()

# ── Startup ────────────────────────────────────────────────────────

def main():
    log.info("=" * 80)
    log.info("Blender Remote GPU Render Server v4.0 (DISPATCHER)")
    log.info("=" * 80)

    # Detect GPU
    detect_gpu()
    log.info(f"GPU: {GPU_NAME} ({GPU_VRAM} MB VRAM)")

    # Verify Blender
    blender = find_blender()
    if blender:
        log.info(f"Blender found: {blender}")
    else:
        log.warning("Blender not found — renders will fail")

    # Verify single_job_worker.py
    worker_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "single_job_worker.py")
    if os.path.isfile(worker_script):
        log.info(f"Worker script: {worker_script}")
    else:
        log.error(f"Worker script not found: {worker_script}")
        sys.exit(1)

    # Start worker threads
    log.info(f"Starting {NUM_WORKERS} worker threads...")
    for i in range(NUM_WORKERS):
        t = threading.Thread(target=worker_thread, args=(i,), daemon=True)
        t.start()

    # Start HTTP server
    log.info(f"Starting HTTP server on port {HTTP_PORT}...")
    try:
        run_http()
    except KeyboardInterrupt:
        log.info("Shutdown signal received")
        sys.exit(0)

if __name__ == "__main__":
    main()
