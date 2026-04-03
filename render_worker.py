#!/usr/bin/env python3
"""
Persistent Blender Render Worker b31 — runs INSIDE Blender, keeps scene in GPU memory.

Launch: blender --background --python render_worker.py

b31 fix: open_mainfile MUST run on Blender's main thread.
  Calling it from the HTTP handler thread kills the Python runtime → WinError 10054.
  Fix: HTTP handler saves blend to disk + queues a timer, returns "scene_loading"
       immediately. Main thread timer calls open_mainfile. server.py polls ping
       until scene_loaded=True before returning scene_cached to Mac.
"""

import bpy
import json
import base64
import os
import sys
import tempfile
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

WORKER_PORT = int(os.environ.get("WORKER_PORT", "9880"))

_scene_loaded   = False
_scene_path     = ""
_scene_loading  = False   # True while open_mainfile is in progress
_last_render_b64 = ""
_rendering      = False
_render_lock    = threading.Lock()

# Pending blend file path — set by HTTP thread, consumed by main-thread timer
_pending_load_path = None
_pending_load_lock = threading.Lock()

# Which compute type is active ("OPTIX", "CUDA", "NONE")
_compute_type = "NONE"


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
                            print(f"[Worker] Device: {d.name} ({device_type})")
                        _compute_type = device_type
                        print(f"[Worker] Compute: {device_type}")
                        break
                except Exception as e:
                    print(f"[Worker] {device_type} unavailable: {e}")
        bpy.context.scene.render.engine = "CYCLES"
        bpy.context.scene.cycles.device = "GPU"
    except Exception as e:
        print(f"[Worker] GPU setup warning: {e}")


def _enable_denoiser(scene):
    scene.cycles.use_denoising = True
    try:
        scene.cycles.denoiser = "OPTIX" if _compute_type == "OPTIX" else "OPENIMAGEDENOISE"
    except Exception:
        pass


# ── Main-thread scene loader (via bpy.app.timers) ─────────────

def _load_timer():
    """Runs on Blender's main thread. Safe to call open_mainfile here."""
    global _pending_load_path, _scene_loaded, _scene_path, _scene_loading

    with _pending_load_lock:
        path = _pending_load_path
        _pending_load_path = None

    if not path:
        return None  # Nothing queued — unregister

    _scene_loading = True
    _scene_loaded  = False
    print(f"[Worker] Main-thread open_mainfile: {path}")

    try:
        bpy.ops.wm.open_mainfile(filepath=path)
        setup_gpu()
        _scene_path   = path
        _scene_loaded = True
        print(f"[Worker] Scene ready: {len(bpy.data.objects)} objects, compute={_compute_type}")
    except Exception as e:
        print(f"[Worker] open_mainfile failed: {e}")
    finally:
        _scene_loading = False

    return None  # Unregister timer (one-shot)


def _queue_scene_load_path(blend_path):
    """Queue a blend file path for main-thread loading."""
    global _scene_loaded

    with _pending_load_lock:
        _pending_load_path = blend_path

    _scene_loaded = False

    # Schedule the load on the main thread
    bpy.app.timers.register(_load_timer, first_interval=0.05)
    print(f"[Worker] Scene load queued: {blend_path} → main thread")


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
    global _last_render_b64, _rendering

    scene = bpy.context.scene
    scene.render.resolution_x        = width
    scene.render.resolution_y        = height
    scene.render.resolution_percentage = 100
    scene.cycles.samples              = samples
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
            _last_render_b64 = base64.b64encode(jpg_data).decode("ascii")
            denoiser = getattr(scene.cycles, "denoiser", "?")
            print(f"[Worker] {width}x{height} @{samples}spp+{denoiser} {elapsed_ms}ms {len(jpg_data)//1024}KB")
            return _last_render_b64
        print("[Worker] Render produced no output")
        return ""
    except Exception as e:
        print(f"[Worker] Render error: {e}")
        return ""
    finally:
        _rendering = False
        try:
            os.remove(output_path)
            os.rmdir(output_dir)
        except Exception:
            pass


# ── HTTP handler ──────────────────────────────────────────────

class WorkerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length   = int(self.headers.get("Content-Length", 0))
            body     = self.rfile.read(length)
            data     = json.loads(body.decode("utf-8"))
            msg_type = data.get("type", "")
            response = {}

            if msg_type == "ping":
                response = {
                    "type":         "pong",
                    "worker":       True,
                    "build":        "b31",
                    "scene_loaded": _scene_loaded,
                    "scene_loading":_scene_loading,
                    "rendering":    _rendering,
                    "compute":      _compute_type,
                }

            elif msg_type in ("load_scene_path", "load_scene"):
                # load_scene_path = preferred (path only, no large data transfer)
                # load_scene      = legacy fallback (base64 in body — avoid for large files)
                if msg_type == "load_scene_path":
                    blend_path = data.get("path", "")
                    if not blend_path or not os.path.isfile(blend_path):
                        response = {"type": "error", "message": f"File not found: {blend_path}"}
                    else:
                        _queue_scene_load_path(blend_path)
                        response = {
                            "type":    "scene_loading",
                            "message": "Loading on main thread — poll ping for scene_loaded=true",
                        }
                else:
                    # Legacy: base64 in body — save to temp then queue
                    blend_b64 = data.get("blend_data", "")
                    if not blend_b64:
                        response = {"type": "error", "message": "No blend_data"}
                    else:
                        tmp = tempfile.NamedTemporaryFile(suffix=".blend", delete=False)
                        tmp.write(base64.b64decode(blend_b64))
                        tmp.close()
                        _queue_scene_load_path(tmp.name)
                        response = {
                            "type":    "scene_loading",
                            "message": "Loading on main thread — poll ping for scene_loaded=true",
                        }

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
                            print(f"[Worker] Final {width}x{height}@{samples}spp {elapsed_ms}ms {len(png_data)//1024}KB")
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
            print(f"[Worker] Handler error: {e}")
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
    setup_gpu()

    print("=" * 55)
    print(f"[Worker] Blender Render Worker b31 on port {WORKER_PORT}")
    print(f"[Worker] Blender {bpy.app.version_string}")
    print(f"[Worker] Compute: {_compute_type}")
    print(f"[Worker] open_mainfile runs on main thread via bpy.app.timers")
    print("=" * 55)

    server = HTTPServer(("0.0.0.0", WORKER_PORT), WorkerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[Worker] HTTP listener on port {WORKER_PORT}")
    print("[Worker] Ready for commands")

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("[Worker] Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
