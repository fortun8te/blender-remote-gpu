#!/usr/bin/env python3
"""
Persistent Blender Render Worker b24 — runs INSIDE Blender, keeps scene in GPU memory.

Launch: blender --background --python render_worker.py

Upgrades in b24:
- OptiX AI denoiser (Tensor Cores): clean frames at 1 sample
- Falls back to CUDA → CPU if OptiX unavailable
- update_camera command: set camera without re-rendering
- Default viewport samples = 1 (denoiser handles quality)
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

WORKER_PORT = int(os.environ.get("WORKER_PORT", "9880"))

_scene_loaded = False
_scene_path = ""
_last_render_b64 = ""
_rendering = False
_render_lock = threading.Lock()

# Which compute type is active ("OPTIX", "CUDA", "NONE")
_compute_type = "NONE"


def setup_gpu():
    """Enable GPU rendering — try OptiX first (RTX Tensor Core denoiser), fall back to CUDA."""
    global _compute_type

    try:
        prefs = bpy.context.preferences
        cycles_addon = prefs.addons.get("cycles")
        if cycles_addon:
            # OptiX = faster denoising on RTX cards via Tensor Cores
            for device_type in ("OPTIX", "CUDA"):
                try:
                    cycles_addon.preferences.compute_device_type = device_type
                    cycles_addon.preferences.get_devices()
                    devices = list(cycles_addon.preferences.devices)
                    if devices:
                        for d in devices:
                            d.use = True
                            print(f"[Worker] Device: {d.name} ({device_type}, use={d.use})")
                        _compute_type = device_type
                        print(f"[Worker] Compute backend: {device_type}")
                        break
                except Exception as e:
                    print(f"[Worker] {device_type} unavailable: {e}")
                    continue

        bpy.context.scene.render.engine = "CYCLES"
        bpy.context.scene.cycles.device = "GPU"
        print(f"[Worker] GPU rendering enabled ({_compute_type})")
    except Exception as e:
        print(f"[Worker] GPU setup warning: {e}")


def _enable_denoiser(scene):
    """Enable OptiX AI denoiser (falls back to OpenImageDenoise if OptiX unavailable)."""
    scene.cycles.use_denoising = True
    try:
        if _compute_type == "OPTIX":
            scene.cycles.denoiser = "OPTIX"
        else:
            scene.cycles.denoiser = "OPENIMAGEDENOISE"
    except Exception:
        pass  # Older Blender versions don't have denoiser property


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


def set_camera_from_matrix(view_matrix_flat):
    """Set the scene camera to match the given 4x4 view matrix (flat list of 16 floats)."""
    import mathutils

    cam_data = bpy.data.cameras.get("_remote_cam")
    if not cam_data:
        cam_data = bpy.data.cameras.new("_remote_cam")

    cam_obj = bpy.data.objects.get("_remote_cam_obj")
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


def render_frame(width, height, samples=1, quality=75):
    """Render current scene with AI denoising and return JPEG as base64.

    b24: default samples=1 — OptiX AI denoiser gives clean results at 1 sample.
    quality=75 is a good balance of file size vs visual quality for viewport.
    """
    global _last_render_b64, _rendering

    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.cycles.samples = samples

    # AI denoiser — main quality upgrade in b24
    _enable_denoiser(scene)

    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality = quality

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
            denoiser = getattr(scene.cycles, "denoiser", "?")
            print(f"[Worker] Frame {width}x{height} @ {samples}spp+{denoiser} in {elapsed_ms}ms ({len(jpg_data)//1024}KB)")
            return _last_render_b64
        else:
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
                    "build": "b24",
                    "scene_loaded": _scene_loaded,
                    "rendering": _rendering,
                    "compute": _compute_type,
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
                        "compute": _compute_type,
                    }

            elif msg_type == "update_camera":
                # Fast path: just set camera, no render — b24 addition
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
                    width = data.get("width", 640)
                    height = data.get("height", 360)
                    samples = data.get("samples", 1)   # b24: default 1 (denoiser handles quality)
                    quality = data.get("quality", 75)  # b24: bumped from 70 to 75
                    view_matrix = data.get("view_matrix")

                    with _render_lock:
                        if view_matrix:
                            flat = ([x for row in view_matrix for x in row]
                                    if isinstance(view_matrix[0], list) else view_matrix)
                            set_camera_from_matrix(flat)

                        jpg_b64 = render_frame(width, height, samples, quality)

                    response = {
                        "type": "frame_result",
                        "jpg_b64": jpg_b64,
                        "width": width,
                        "height": height,
                        "samples": samples,
                        "compute": _compute_type,
                    }

            elif msg_type == "render_final":
                if not _scene_loaded:
                    response = {"type": "error", "message": "No scene loaded"}
                else:
                    width = data.get("width", 1920)
                    height = data.get("height", 1080)
                    samples = data.get("samples", 128)

                    with _render_lock:
                        scene = bpy.context.scene
                        scene.render.resolution_x = width
                        scene.render.resolution_y = height
                        scene.render.resolution_percentage = 100
                        scene.cycles.samples = samples

                        # Enable denoiser for final renders too
                        _enable_denoiser(scene)

                        scene.render.image_settings.file_format = "PNG"

                        output_dir = tempfile.mkdtemp(prefix="final_")
                        output_path = os.path.join(output_dir, "final.png")
                        scene.render.filepath = output_path

                        start = time.time()
                        bpy.ops.render.render(write_still=True)
                        elapsed_ms = int((time.time() - start) * 1000)

                        if os.path.isfile(output_path):
                            with open(output_path, "rb") as f:
                                png_data = f.read()
                            print(f"[Worker] Final {width}x{height} @ {samples}spp in {elapsed_ms}ms ({len(png_data)//1024}KB)")
                            response = {
                                "type": "render_result",
                                "png_b64": base64.b64encode(png_data).decode("ascii"),
                                "width": width,
                                "height": height,
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
    print(f"[Worker] Blender Render Worker b24 on port {WORKER_PORT}")
    print(f"[Worker] Blender {bpy.app.version_string}")
    print(f"[Worker] Compute: {_compute_type} | Denoiser: OptiX/OIDN")
    print(f"[Worker] 1 sample + AI denoiser = fast clean viewport")
    print("=" * 55)

    server = HTTPServer(("0.0.0.0", WORKER_PORT), WorkerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[Worker] HTTP listener on port {WORKER_PORT}")
    print("[Worker] Ready for commands")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[Worker] Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
