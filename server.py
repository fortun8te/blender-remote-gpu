#!/usr/bin/env python3
"""
Blender Remote GPU Render Server v2.1
HTTP + TCP + XMLRPC, scene caching, viewport rendering.
Zero external dependencies.
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
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


# ── Scene Cache ───────────────────────────────────────────────

scenes = {}       # scene_id -> {"path": str, "uploaded": float}
scenes_lock = threading.Lock()
SCENE_DIR = os.path.join(tempfile.gettempdir(), "blender_remote_scenes")
os.makedirs(SCENE_DIR, exist_ok=True)


# ── Render Jobs ───────────────────────────────────────────────

jobs = {}
jobs_lock = threading.Lock()

# Viewport state: latest rendered frame per scene
viewport_frames = {}   # scene_id -> {"png_b64": str, "rendering": bool}
viewport_lock = threading.Lock()


def run_render(job_id, blend_path, width, height, samples, output_format="PNG"):
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

    # Escape backslashes for Windows paths in Python expression
    out_escaped = output_dir.replace("\\", "/")

    py_expr = (
        f"import bpy; "
        f"s = bpy.context.scene; "
        f"s.render.resolution_x = {width}; "
        f"s.render.resolution_y = {height}; "
        f"s.render.resolution_percentage = 100; "
        f"s.cycles.samples = {samples}; "
        f"s.cycles.device = 'GPU'; "
        f"s.render.filepath = '{out_escaped}/frame'"
    )

    cmd = [blender, "-b", blend_path, "--python-expr", py_expr,
           "-F", output_format, "-x", "1", "-f", "1"]

    log.info(f"Job {job_id}: Rendering {width}x{height} @ {samples}spp")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if proc.returncode != 0:
            err = proc.stderr[-500:] if proc.stderr else "Unknown error"
            with jobs_lock:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = f"Blender exit {proc.returncode}: {err}"
            return

        if not os.path.isfile(output_path):
            with jobs_lock:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = "Output file not found"
            return

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
            jobs[job_id]["error"] = "Render timed out"
    except Exception as e:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def run_viewport_render(scene_id, width, height, view_matrix, proj_matrix):
    """Quick low-sample render for viewport preview."""
    with scenes_lock:
        scene = scenes.get(scene_id)
    if not scene:
        return

    blend_path = scene["path"]
    blender = find_blender()
    if not blender:
        return

    with viewport_lock:
        viewport_frames[scene_id] = {"png_b64": "", "rendering": True}

    output_dir = tempfile.mkdtemp(prefix="viewport_")
    output_path = os.path.join(output_dir, "frame0001.png")
    out_escaped = output_dir.replace("\\", "/")

    # Build camera matrix from view/proj matrices
    # For viewport: set camera to match the 3D view
    vm_flat = str([item for row in view_matrix for item in row])
    pm_flat = str([item for row in proj_matrix for item in row])

    py_expr = (
        f"import bpy, mathutils; "
        f"s = bpy.context.scene; "
        f"s.render.resolution_x = {width}; "
        f"s.render.resolution_y = {height}; "
        f"s.render.resolution_percentage = 100; "
        f"s.cycles.samples = 16; "  # Low samples for fast preview
        f"s.cycles.device = 'GPU'; "
        f"s.render.filepath = '{out_escaped}/frame'; "
        # Create/reuse a viewport camera
        f"cam = bpy.data.cameras.get('_viewport_cam') or bpy.data.cameras.new('_viewport_cam'); "
        f"obj = bpy.data.objects.get('_viewport_cam_obj'); "
        f"exec('\\nif not obj:\\n obj = bpy.data.objects.new(\"_viewport_cam_obj\", cam)\\n bpy.context.collection.objects.link(obj)'); "
        f"s.camera = obj; "
        f"vm = {vm_flat}; "
        f"m = mathutils.Matrix((vm[0:4], vm[4:8], vm[8:12], vm[12:16])); "
        f"obj.matrix_world = m.inverted()"
    )

    cmd = [blender, "-b", blend_path, "--python-expr", py_expr,
           "-F", "PNG", "-x", "1", "-f", "1"]

    log.info(f"Viewport render for scene {scene_id}: {width}x{height} @ 16spp")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if proc.returncode == 0 and os.path.isfile(output_path):
            with open(output_path, "rb") as f:
                png_data = f.read()
            with viewport_lock:
                viewport_frames[scene_id] = {
                    "png_b64": base64.b64encode(png_data).decode("ascii"),
                    "rendering": False,
                }
            log.info(f"Viewport frame: {len(png_data)} bytes")
        else:
            with viewport_lock:
                viewport_frames[scene_id]["rendering"] = False
    except Exception as e:
        log.error(f"Viewport render error: {e}")
        with viewport_lock:
            viewport_frames[scene_id]["rendering"] = False
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


# ── Message Handler ───────────────────────────────────────────

def handle_message(data):
    msg_type = data.get("type", "unknown")

    # ── Ping ──
    if msg_type == "ping":
        return {
            "type": "pong",
            "gpu": GPU_NAME,
            "vram_free": GPU_VRAM,
            "timestamp": time.time(),
            "version": "2.1.0",
            "build": "b22",
        }

    # ── Scene Upload (cache .blend on server) ──
    elif msg_type == "scene_upload":
        blend_b64 = data.get("blend_data", "")
        if not blend_b64:
            return {"type": "error", "message": "No blend_data"}

        scene_id = str(uuid.uuid4())[:8]
        blend_data = base64.b64decode(blend_b64)
        blend_path = os.path.join(SCENE_DIR, f"scene_{scene_id}.blend")

        with open(blend_path, "wb") as f:
            f.write(blend_data)

        with scenes_lock:
            scenes[scene_id] = {"path": blend_path, "uploaded": time.time()}

        log.info(f"Scene {scene_id}: Cached ({len(blend_data) // 1024} KB)")
        return {"type": "scene_cached", "scene_id": scene_id}

    # ── F12 Render Submit ──
    elif msg_type == "render_submit":
        # Can use cached scene or inline blend_data
        scene_id = data.get("scene_id")
        blend_b64 = data.get("blend_data", "")
        width = data.get("width", 1920)
        height = data.get("height", 1080)
        samples = data.get("samples", 128)

        # Resolve blend path
        if scene_id:
            with scenes_lock:
                scene = scenes.get(scene_id)
            if not scene:
                return {"type": "error", "message": f"Scene {scene_id} not found"}
            blend_path = scene["path"]
        elif blend_b64:
            job_id = str(uuid.uuid4())[:8]
            blend_data = base64.b64decode(blend_b64)
            blend_path = os.path.join(tempfile.gettempdir(), f"render_{job_id}.blend")
            with open(blend_path, "wb") as f:
                f.write(blend_data)
        else:
            return {"type": "error", "message": "No scene_id or blend_data"}

        job_id = data.get("_job_id", str(uuid.uuid4())[:8])

        with jobs_lock:
            jobs[job_id] = {
                "status": "queued",
                "submitted": time.time(),
                "width": width, "height": height, "samples": samples,
            }

        threading.Thread(
            target=run_render,
            args=(job_id, blend_path, width, height, samples),
            daemon=True,
        ).start()

        return {"type": "render_queued", "job_id": job_id}

    # ── Job Status ──
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

    # ── Job Result ──
    elif msg_type == "job_result":
        job_id = data.get("job_id", "")
        with jobs_lock:
            job = jobs.get(job_id)
        if not job:
            return {"type": "error", "message": f"Job {job_id} not found"}
        if job["status"] != "complete":
            return {"type": "error", "message": f"Not complete: {job['status']}"}
        return {
            "type": "job_result",
            "job_id": job_id,
            "png_b64": job.get("result_b64", ""),
            "size": job.get("result_size", 0),
        }

    # ── Viewport Render (uses cached scene, fast low-sample) ──
    elif msg_type == "viewport_render":
        scene_id = data.get("scene_id", "")
        if not scene_id:
            return {"type": "error", "message": "No scene_id for viewport"}

        width = data.get("width", 640)
        height = data.get("height", 360)
        view_matrix = data.get("view_matrix", [])
        proj_matrix = data.get("proj_matrix", [])

        # Check if already rendering
        with viewport_lock:
            frame = viewport_frames.get(scene_id, {})
            if frame.get("rendering"):
                # Return last frame if still rendering
                return {
                    "type": "viewport_result",
                    "scene_id": scene_id,
                    "png_b64": frame.get("png_b64", ""),
                    "status": "rendering",
                }

        # Start viewport render in background
        threading.Thread(
            target=run_viewport_render,
            args=(scene_id, width, height, view_matrix, proj_matrix),
            daemon=True,
        ).start()

        # Return last available frame immediately
        with viewport_lock:
            frame = viewport_frames.get(scene_id, {})
        return {
            "type": "viewport_result",
            "scene_id": scene_id,
            "png_b64": frame.get("png_b64", ""),
            "status": "queued",
        }

    # ── Viewport Poll (get latest frame without starting new render) ──
    elif msg_type == "viewport_poll":
        scene_id = data.get("scene_id", "")
        with viewport_lock:
            frame = viewport_frames.get(scene_id, {})
        return {
            "type": "viewport_result",
            "scene_id": scene_id,
            "png_b64": frame.get("png_b64", ""),
            "status": "rendering" if frame.get("rendering") else "ready",
        }

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
        if length > 100_000_000:
            return
        payload = b""
        while len(payload) < length:
            chunk = conn.recv(min(65536, length - len(payload)))
            if not chunk:
                return
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
    log.info(f"TCP on :{SOCKET_PORT}")
    while True:
        conn, addr = sock.accept()
        threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True).start()


# ── XML-RPC Server ────────────────────────────────────────────

def xmlrpc_handle(json_str):
    data = json.loads(json_str)
    return json.dumps(handle_message(data))


def run_xmlrpc():
    server = SimpleXMLRPCServer(("0.0.0.0", XMLRPC_PORT), allow_none=True, logRequests=False)
    server.register_function(xmlrpc_handle, "handle")
    log.info(f"XMLRPC on :{XMLRPC_PORT}")
    server.serve_forever()


# ── Main ──────────────────────────────────────────────────────

def main():
    detect_gpu()

    blender = find_blender()

    log.info("=" * 50)
    log.info("Blender Remote GPU Render Server v2.1 (b22)")
    log.info(f"  HTTP:    http://0.0.0.0:{HTTP_PORT}")
    log.info(f"  TCP:     tcp://0.0.0.0:{SOCKET_PORT}")
    log.info(f"  XMLRPC:  http://0.0.0.0:{XMLRPC_PORT}")
    log.info(f"  GPU:     {GPU_NAME} ({GPU_VRAM} MB free)")
    log.info(f"  Blender: {blender or 'NOT FOUND'}")
    log.info(f"  Scenes:  {SCENE_DIR}")
    log.info("=" * 50)

    for t in [
        threading.Thread(target=run_http, daemon=True),
        threading.Thread(target=run_tcp, daemon=True),
        threading.Thread(target=run_xmlrpc, daemon=True),
    ]:
        t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Server stopped")


if __name__ == "__main__":
    main()
