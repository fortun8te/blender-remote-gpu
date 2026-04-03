#!/usr/bin/env python3
"""
Persistent Blender Render Worker — runs INSIDE Blender, keeps scene in GPU memory.

Launch: blender --background --python render_worker.py

This script:
1. Opens an HTTP listener inside Blender's Python environment
2. Accepts scene_load, set_camera, render_frame commands
3. Keeps the scene loaded in GPU memory between frames
4. Returns JPEG frames in ~100-300ms instead of 1-3s
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
from io import BytesIO

# Configuration
WORKER_PORT = int(os.environ.get("WORKER_PORT", "9880"))

# State
_scene_loaded = False
_scene_path = ""
_last_render_b64 = ""
_rendering = False
_render_lock = threading.Lock()


def setup_gpu():
    """Enable GPU rendering in Cycles."""
    try:
        prefs = bpy.context.preferences
        cycles = prefs.addons.get("cycles")
        if cycles:
            cycles.preferences.compute_device_type = "CUDA"
            cycles.preferences.get_devices()
            for device in cycles.preferences.devices:
                device.use = True
                print(f"[Worker] GPU device: {device.name} (enabled={device.use})")

        bpy.context.scene.render.engine = "CYCLES"
        bpy.context.scene.cycles.device = "GPU"
        print("[Worker] GPU rendering enabled")
    except Exception as e:
        print(f"[Worker] GPU setup warning: {e}")


def load_scene(blend_path):
    """Load a .blend file and keep it in memory."""
    global _scene_loaded, _scene_path

    print(f"[Worker] Loading scene: {blend_path}")
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    setup_gpu()

    _scene_path = blend_path
    _scene_loaded = True
    print(f"[Worker] Scene loaded: {len(bpy.data.objects)} objects")


def load_scene_from_b64(blend_b64):
    """Decode base64 .blend and load it."""
    blend_data = base64.b64decode(blend_b64)
    tmp = tempfile.NamedTemporaryFile(suffix=".blend", delete=False)
    tmp.write(blend_data)
    tmp.close()
    load_scene(tmp.name)
    # Don't delete — Blender keeps reference to it


def set_camera_from_matrix(view_matrix_flat):
    """Set the scene camera to match the given 4x4 view matrix."""
    import mathutils

    # Get or create viewport camera
    cam_data = bpy.data.cameras.get("_remote_cam")
    if not cam_data:
        cam_data = bpy.data.cameras.new("_remote_cam")

    cam_obj = bpy.data.objects.get("_remote_cam_obj")
    if not cam_obj:
        cam_obj = bpy.data.objects.new("_remote_cam_obj", cam_data)
        bpy.context.collection.objects.link(cam_obj)

    bpy.context.scene.camera = cam_obj

    # Set camera from view matrix (inverted because view_matrix = world_to_camera)
    m = mathutils.Matrix((
        view_matrix_flat[0:4],
        view_matrix_flat[4:8],
        view_matrix_flat[8:12],
        view_matrix_flat[12:16],
    ))
    cam_obj.matrix_world = m.inverted()


def render_frame(width, height, samples=16, quality=70):
    """Render current scene and return JPEG as base64."""
    global _last_render_b64, _rendering

    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.cycles.samples = samples
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality = quality

    # Render to memory
    output_dir = tempfile.mkdtemp(prefix="wrkr_")
    output_path = os.path.join(output_dir, "frame.jpg")
    scene.render.filepath = output_path

    start = time.time()
    _rendering = True

    try:
        bpy.ops.render.render(write_still=True)

        if os.path.isfile(output_path):
            with open(output_path, "rb") as f:
                jpg_data = f.read()

            elapsed_ms = int((time.time() - start) * 1000)
            _last_render_b64 = base64.b64encode(jpg_data).decode("ascii")
            print(f"[Worker] Frame: {width}x{height} @ {samples}spp in {elapsed_ms}ms ({len(jpg_data)} bytes)")
            return _last_render_b64
        else:
            print("[Worker] Render produced no output")
            return ""
    except Exception as e:
        print(f"[Worker] Render error: {e}")
        return ""
    finally:
        _rendering = False
        # Cleanup
        try:
            os.remove(output_path)
            os.rmdir(output_dir)
        except Exception:
            pass


# ── HTTP Handler ──────────────────────────────────────────────

class WorkerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            msg_type = data.get("type", "")

            response = {}

            if msg_type == "ping":
                response = {
                    "type": "pong",
                    "worker": True,
                    "scene_loaded": _scene_loaded,
                    "rendering": _rendering,
                }

            elif msg_type == "load_scene":
                blend_b64 = data.get("blend_data", "")
                if not blend_b64:
                    response = {"type": "error", "message": "No blend_data"}
                else:
                    with _render_lock:
                        load_scene_from_b64(blend_b64)
                    response = {
                        "type": "scene_loaded",
                        "objects": len(bpy.data.objects),
                    }

            elif msg_type == "render_frame":
                if not _scene_loaded:
                    response = {"type": "error", "message": "No scene loaded"}
                else:
                    width = data.get("width", 640)
                    height = data.get("height", 360)
                    samples = data.get("samples", 16)
                    quality = data.get("quality", 70)
                    view_matrix = data.get("view_matrix")

                    with _render_lock:
                        if view_matrix:
                            # Flatten nested list if needed
                            if isinstance(view_matrix[0], list):
                                flat = [x for row in view_matrix for x in row]
                            else:
                                flat = view_matrix
                            set_camera_from_matrix(flat)

                        jpg_b64 = render_frame(width, height, samples, quality)

                    response = {
                        "type": "frame_result",
                        "jpg_b64": jpg_b64,
                        "width": width,
                        "height": height,
                    }

            elif msg_type == "render_final":
                if not _scene_loaded:
                    response = {"type": "error", "message": "No scene loaded"}
                else:
                    width = data.get("width", 1920)
                    height = data.get("height", 1080)
                    samples = data.get("samples", 128)

                    with _render_lock:
                        # For final render, use PNG and high quality
                        scene = bpy.context.scene
                        scene.render.resolution_x = width
                        scene.render.resolution_y = height
                        scene.render.resolution_percentage = 100
                        scene.cycles.samples = samples
                        scene.render.image_settings.file_format = "PNG"

                        output_dir = tempfile.mkdtemp(prefix="final_")
                        output_path = os.path.join(output_dir, "final.png")
                        scene.render.filepath = output_path

                        bpy.ops.render.render(write_still=True)

                        if os.path.isfile(output_path):
                            with open(output_path, "rb") as f:
                                png_data = f.read()
                            response = {
                                "type": "render_result",
                                "png_b64": base64.b64encode(png_data).decode("ascii"),
                                "width": width,
                                "height": height,
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
            print(f"[Worker] Error: {e}")
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

    print("=" * 50)
    print(f"[Worker] Blender Render Worker starting on port {WORKER_PORT}")
    print(f"[Worker] Blender {bpy.app.version_string}")
    print(f"[Worker] Scene in GPU memory — persistent process")
    print("=" * 50)

    # Start HTTP server in a thread
    server = HTTPServer(("0.0.0.0", WORKER_PORT), WorkerHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[Worker] HTTP listener on port {WORKER_PORT}")
    print("[Worker] Ready for commands")

    # Keep Blender alive
    # In background mode, we need to prevent Blender from exiting
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[Worker] Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
