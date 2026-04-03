#!/usr/bin/env python3
"""
Persistent Blender Render Worker b34 — runs INSIDE Blender, keeps scene in GPU memory.

Launch: blender --background --python render_worker.py

b34: TESTED AND PROVEN locally on Blender 5.0:
  - while True main loop (required — script must NOT return in --background mode)
  - HTTP handler queues path via global + lock
  - Main loop calls open_mainfile safely on main thread
  - HTTP daemon thread survives open_mainfile
  - Ping returns scene_loaded=true after load completes
"""

import bpy
import json
import base64
import os
import tempfile
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

WORKER_PORT = int(os.environ.get("WORKER_PORT", "9880"))

_scene_loaded   = False
_scene_loading  = False
_rendering      = False
_render_lock    = threading.Lock()
_compute_type   = "NONE"

# Pending scene path — HTTP thread writes, main loop reads
_pending_path = None
_pending_lock = threading.Lock()


def _log(msg):
    print(f"[Worker] {msg}", flush=True)


# ── GPU setup ─────────────────────────────────────────────────

def setup_gpu():
    global _compute_type
    try:
        prefs = bpy.context.preferences
        cycles_addon = prefs.addons.get("cycles")
        if cycles_addon:
            for device_type in ("OPTIX", "CUDA"):
                try:
                    cycles_addon.preferences.compute_device_type = device_type
                    cycles_addon.preferences.get_devices()
                    devices = list(cycles_addon.preferences.devices)
                    if devices:
                        for d in devices:
                            d.use = True
                            _log(f"Device: {d.name} ({device_type})")
                        _compute_type = device_type
                        _log(f"Compute: {device_type}")
                        break
                except Exception as e:
                    _log(f"{device_type} unavailable: {e}")
        bpy.context.scene.render.engine = "CYCLES"
        bpy.context.scene.cycles.device = "GPU"
    except Exception as e:
        _log(f"GPU setup warning: {e}")


def _enable_denoiser(scene):
    scene.cycles.use_denoising = True
    try:
        scene.cycles.denoiser = "OPTIX" if _compute_type == "OPTIX" else "OPENIMAGEDENOISE"
    except Exception:
        pass


# ── Camera helper ─────────────────────────────────────────────

def set_camera_from_matrix(view_matrix_flat):
    import mathutils
    cam_data = bpy.data.cameras.get("_remote_cam") or bpy.data.cameras.new("_remote_cam")
    cam_obj  = bpy.data.objects.get("_remote_cam_obj")
    if not cam_obj:
        cam_obj = bpy.data.objects.new("_remote_cam_obj", cam_data)
        bpy.context.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    m = mathutils.Matrix((
        view_matrix_flat[0:4],
        view_matrix_flat[4:8],
        view_matrix_flat[8:12],
        view_matrix_flat[12:16],
    ))
    cam_obj.matrix_world = m.inverted()


# ── Render ────────────────────────────────────────────────────

def render_frame(width, height, samples=1, quality=75):
    global _rendering

    scene = bpy.context.scene
    scene.render.resolution_x         = width
    scene.render.resolution_y         = height
    scene.render.resolution_percentage = 100
    scene.cycles.samples               = samples
    _enable_denoiser(scene)
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality     = quality

    output_dir  = tempfile.mkdtemp(prefix="wrkr_")
    output_path = os.path.join(output_dir, "frame.jpg")
    scene.render.filepath = output_path

    start     = time.time()
    _rendering = True
    try:
        bpy.ops.render.render(write_still=True)
        if os.path.isfile(output_path):
            with open(output_path, "rb") as f:
                jpg_data = f.read()
            elapsed_ms = int((time.time() - start) * 1000)
            result_b64 = base64.b64encode(jpg_data).decode("ascii")
            denoiser = getattr(scene.cycles, "denoiser", "?")
            _log(f"{width}x{height} @{samples}spp+{denoiser} {elapsed_ms}ms {len(jpg_data)//1024}KB")
            return result_b64
        _log("Render produced no output")
        return ""
    except Exception as e:
        _log(f"Render error: {e}")
        return ""
    finally:
        _rendering = False
        try:
            os.remove(output_path)
            os.rmdir(output_dir)
        except Exception:
            pass


# ── HTTP handler (daemon thread — NO bpy.ops here) ───────────

class WorkerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        global _pending_path   # CRITICAL — without this, assignment is local-only

        try:
            length   = int(self.headers.get("Content-Length", 0))
            body     = self.rfile.read(length)
            data     = json.loads(body.decode("utf-8"))
            msg_type = data.get("type", "")
            response = {}

            if msg_type == "ping":
                response = {
                    "type":          "pong",
                    "worker":        True,
                    "build":         "b34",
                    "scene_loaded":  _scene_loaded,
                    "scene_loading": _scene_loading,
                    "rendering":     _rendering,
                    "compute":       _compute_type,
                }

            elif msg_type == "load_scene_path":
                blend_path = data.get("path", "")
                if not blend_path or not os.path.isfile(blend_path):
                    response = {"type": "error", "message": f"File not found: {blend_path}"}
                else:
                    with _pending_lock:
                        _pending_path = blend_path
                    _log(f"Queued scene load: {blend_path}")
                    response = {"type": "scene_loading"}

            elif msg_type == "load_scene":
                # Legacy: base64 in body
                blend_b64 = data.get("blend_data", "")
                if not blend_b64:
                    response = {"type": "error", "message": "No blend_data"}
                else:
                    tmp = tempfile.NamedTemporaryFile(suffix=".blend", delete=False)
                    tmp.write(base64.b64decode(blend_b64))
                    tmp.close()
                    with _pending_lock:
                        _pending_path = tmp.name
                    _log(f"Queued scene load (from b64): {tmp.name}")
                    response = {"type": "scene_loading"}

            elif msg_type == "update_camera":
                view_matrix = data.get("view_matrix")
                if not _scene_loaded:
                    response = {"type": "error", "message": "No scene loaded"}
                elif view_matrix:
                    flat = ([x for row in view_matrix for x in row]
                            if isinstance(view_matrix[0], list) else view_matrix)
                    with _render_lock:
                        set_camera_from_matrix(flat)
                    response = {"type": "camera_updated"}
                else:
                    response = {"type": "error", "message": "No view_matrix"}

            elif msg_type == "render_frame":
                if not _scene_loaded:
                    response = {"type": "error", "message": "No scene loaded"}
                else:
                    width       = data.get("width", 640)
                    height      = data.get("height", 360)
                    samples     = data.get("samples", 1)
                    quality     = data.get("quality", 75)
                    view_matrix = data.get("view_matrix")
                    with _render_lock:
                        if view_matrix:
                            flat = ([x for row in view_matrix for x in row]
                                    if isinstance(view_matrix[0], list) else view_matrix)
                            set_camera_from_matrix(flat)
                        jpg_b64 = render_frame(width, height, samples, quality)
                    response = {
                        "type":    "frame_result",
                        "jpg_b64": jpg_b64,
                        "width":   width,
                        "height":  height,
                        "samples": samples,
                        "compute": _compute_type,
                    }

            elif msg_type == "render_final":
                if not _scene_loaded:
                    response = {"type": "error", "message": "No scene loaded"}
                else:
                    width   = data.get("width", 1920)
                    height  = data.get("height", 1080)
                    samples = data.get("samples", 128)
                    with _render_lock:
                        scene = bpy.context.scene
                        scene.render.resolution_x         = width
                        scene.render.resolution_y         = height
                        scene.render.resolution_percentage = 100
                        scene.cycles.samples               = samples
                        _enable_denoiser(scene)
                        scene.render.image_settings.file_format = "PNG"
                        output_dir  = tempfile.mkdtemp(prefix="final_")
                        output_path = os.path.join(output_dir, "final.png")
                        scene.render.filepath = output_path
                        start = time.time()
                        bpy.ops.render.render(write_still=True)
                        elapsed_ms = int((time.time() - start) * 1000)
                        if os.path.isfile(output_path):
                            with open(output_path, "rb") as f:
                                png_data = f.read()
                            _log(f"Final {width}x{height}@{samples}spp {elapsed_ms}ms {len(png_data)//1024}KB")
                            response = {
                                "type":       "render_result",
                                "png_b64":    base64.b64encode(png_data).decode("ascii"),
                                "width":      width,
                                "height":     height,
                                "elapsed_ms": elapsed_ms,
                            }
                            os.remove(output_path)
                        else:
                            response = {"type": "error", "message": "No output produced"}
                        os.rmdir(output_dir)
            else:
                response = {"type": "error", "message": f"Unknown: {msg_type}"}

            resp_bytes = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)

        except Exception as e:
            _log(f"Handler error: {e}")
            err = json.dumps({"type": "error", "message": str(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def log_message(self, format, *args):
        pass


# ── Main ──────────────────────────────────────────────────────

def main():
    global _pending_path, _scene_loaded, _scene_loading

    setup_gpu()

    _log("=" * 55)
    _log(f"Blender Render Worker b34 on port {WORKER_PORT}")
    _log(f"Blender {bpy.app.version_string}")
    _log(f"Compute: {_compute_type}")
    _log("=" * 55)

    server = HTTPServer(("0.0.0.0", WORKER_PORT), WorkerHandler)
    http_thread = threading.Thread(target=server.serve_forever, daemon=True)
    http_thread.start()
    _log(f"HTTP on port {WORKER_PORT} — ready")

    try:
        while True:
            # Check for pending scene load (set by HTTP handler thread)
            with _pending_lock:
                path = _pending_path
                _pending_path = None

            if path:
                _scene_loading = True
                _scene_loaded  = False
                _log(f"Main thread loading: {path}")
                try:
                    bpy.ops.wm.open_mainfile(filepath=path)
                    setup_gpu()
                    _scene_loaded = True
                    _log(f"Scene ready: {len(bpy.data.objects)} objects, compute={_compute_type}")
                except Exception as e:
                    _log(f"open_mainfile failed: {e}")
                finally:
                    _scene_loading = False

                # Restart HTTP if open_mainfile killed the thread
                if not http_thread.is_alive():
                    _log("HTTP thread died — restarting")
                    try:
                        server.shutdown()
                    except Exception:
                        pass
                    server = HTTPServer(("0.0.0.0", WORKER_PORT), WorkerHandler)
                    http_thread = threading.Thread(target=server.serve_forever, daemon=True)
                    http_thread.start()
                    _log("HTTP restarted")

            time.sleep(0.1)

    except KeyboardInterrupt:
        _log("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
