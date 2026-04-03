"""Remote GPU render engine — F12 renders on the remote RTX GPU.

b25: viewport rendering moved to live_preview.py (dual-viewport architecture).
This engine handles F12 (Render Image) only.

When you hit F12:
  Mac saves .blend → uploads to Windows → RTX 5080 renders with OptiX → PNG back
"""

import bpy
import os
import time
import base64
import tempfile


class RemoteRenderEngine(bpy.types.RenderEngine):
    bl_idname = "REMOTE_GPU"
    bl_label = "Remote GPU"
    bl_use_preview = False
    bl_use_gpu_context = False   # Not needed — no viewport rendering in engine

    # Shared connection state (used by operators + live_preview)
    _connection = None
    _scene_id = None
    _scene_uploaded = False

    def __init__(self):
        pass

    def __del__(self):
        pass

    # ── F12 FINAL RENDER ──────────────────────────────────────

    def render(self, depsgraph):
        conn = RemoteRenderEngine._connection
        if not conn or not conn.connected:
            self.report({"ERROR"}, "Not connected. Click Connect first.")
            return

        scene = depsgraph.scene
        scale = scene.render.resolution_percentage / 100.0
        width = int(scene.render.resolution_x * scale)
        height = int(scene.render.resolution_y * scale)
        samples = 128
        if hasattr(scene, "cycles"):
            samples = getattr(scene.cycles, "samples", 128)

        self.update_stats("", "Saving scene...")
        self.update_progress(0.0)

        blend_b64 = self._save_scene_b64()
        if not blend_b64:
            self.report({"ERROR"}, "Failed to save scene")
            return

        if self.test_break():
            return

        size_kb = len(blend_b64) * 3 // 4 // 1024
        self.update_stats("", f"Uploading scene ({size_kb} KB)...")
        self.update_progress(0.1)

        # Upload scene and cache it (reused for live preview too)
        scene_id = conn.upload_scene(blend_b64)
        if not scene_id:
            job_id = conn.submit_render(blend_b64, width, height, samples)
        else:
            RemoteRenderEngine._scene_id = scene_id
            RemoteRenderEngine._scene_uploaded = True
            job_id = conn.submit_render_cached(scene_id, width, height, samples)

        if not job_id:
            self.report({"ERROR"}, "Failed to submit render job")
            return

        self.update_stats("", f"Rendering on {conn.gpu_name} ({samples} samples)...")
        self.update_progress(0.2)

        # Poll until complete
        start_time = time.time()
        while True:
            if self.test_break():
                return

            status = conn.poll_status(job_id)
            if not status:
                self.report({"ERROR"}, "Lost connection to server")
                return

            job_status = status.get("status", "unknown")
            elapsed = time.time() - start_time

            if job_status == "complete":
                self.update_progress(0.9)
                break
            elif job_status == "error":
                self.report({"ERROR"}, f"Render failed: {status.get('error')}")
                return
            elif job_status in ("queued", "rendering"):
                progress = min(0.2 + (elapsed / 300.0) * 0.7, 0.85)
                self.update_progress(progress)
                m, s = divmod(int(elapsed), 60)
                self.update_stats("", f"Rendering on {conn.gpu_name}... {m}m {s}s")
                time.sleep(1.0)
            else:
                self.report({"ERROR"}, f"Unexpected status: {job_status}")
                return

            if elapsed > 600:
                self.report({"ERROR"}, "Timed out after 10 minutes")
                return

        # Fetch result and display in Blender's render window
        result_data = conn.get_result(job_id)
        if not result_data or not result_data.get("png_b64"):
            self.report({"ERROR"}, "Empty render result")
            return

        png_bytes = base64.b64decode(result_data["png_b64"])
        self._display_png(png_bytes, width, height, start_time)

    def _save_scene_b64(self):
        """Save current Blender scene to temp .blend and return as base64."""
        tmp = tempfile.NamedTemporaryFile(suffix=".blend", delete=False)
        tmp.close()
        try:
            bpy.ops.wm.save_as_mainfile(filepath=tmp.name, copy=True)
            with open(tmp.name, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            print(f"[RemoteGPU] Scene save failed: {e}")
            return None
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    def _display_png(self, png_bytes, width, height, start_time):
        """Decode PNG result and push into Blender's render result buffer."""
        try:
            tmp_png = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_png.write(png_bytes)
            tmp_png.close()

            try:
                bpy_img = bpy.data.images.load(tmp_png.name)
                pixel_data = list(bpy_img.pixels[:])
                bpy.data.images.remove(bpy_img)
            finally:
                os.unlink(tmp_png.name)

            result = self.begin_result(0, 0, width, height)
            layer = result.layers[0].passes["Combined"]
            layer.rect = [pixel_data[i:i + 4] for i in range(0, len(pixel_data), 4)]
            self.end_result(result)

            self.update_progress(1.0)
            m, s = divmod(int(time.time() - start_time), 60)
            self.update_stats("", f"Done ({m}m {s}s)")

        except Exception as e:
            self.report({"ERROR"}, f"Failed to decode render result: {e}")
