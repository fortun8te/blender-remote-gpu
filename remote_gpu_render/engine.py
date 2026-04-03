"""Remote GPU render engine — F12 renders on the remote RTX GPU.

AGENT R3: Migrated to stateless job dispatcher API.
  Mac saves .blend → dispatcher queues job → RTX 5080 renders → PNG back

Previously: persistent worker connection (now replaced by dispatcher)
"""

import bpy
import os
import time
import tempfile


class RemoteRenderEngine(bpy.types.RenderEngine):
    bl_idname = "REMOTE_GPU"
    bl_label = "Remote GPU"
    bl_use_preview = False

    # Dispatcher client (stateless, created per-render)
    _dispatcher = None

    def __init__(self):
        pass

    def __del__(self):
        pass

    # ── F12 FINAL RENDER ──────────────────────────────────────

    def render(self, depsgraph):
        """Submit render job to dispatcher, poll status, fetch result."""
        dispatcher = RemoteRenderEngine._dispatcher
        if not dispatcher:
            self.report({"ERROR"}, "Not connected to dispatcher. Click Connect first.")
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

        blend_path = self._save_scene_temp()
        if not blend_path:
            self.report({"ERROR"}, "Failed to save scene")
            return

        if self.test_break():
            return

        # Build scene path for dispatcher (assumes network path or mounted filesystem)
        # For now, use local path — adjust if dispatcher runs on different machine
        scene_path = blend_path
        size_kb = os.path.getsize(blend_path) // 1024
        self.update_stats("", f"Submitting render ({size_kb} KB)...")
        self.update_progress(0.15)

        # Submit job to dispatcher
        job_result = dispatcher.submit_render_job(scene_path, width, height, samples)
        if not job_result or not job_result.get("job_id"):
            self.report({"ERROR"}, "Failed to submit render job to dispatcher")
            return

        job_id = job_result["job_id"]
        self.update_stats("", f"Job {job_id[:8]}... queued ({samples} samples)...")
        self.update_progress(0.2)

        # Poll until complete
        start_time = time.time()
        while True:
            if self.test_break():
                return

            status_result = dispatcher.get_job_status(job_id)
            if not status_result:
                self.report({"ERROR"}, "Lost connection to dispatcher")
                return

            job_status = status_result.get("status", "unknown")
            progress_value = status_result.get("progress", 0.0)
            elapsed = time.time() - start_time

            if job_status == "done":
                self.update_progress(0.9)
                break
            elif job_status == "error":
                error_msg = status_result.get("error", "Unknown error")
                self.report({"ERROR"}, f"Render failed: {error_msg}")
                return
            elif job_status in ("queued", "running"):
                # Map dispatcher progress (0.0-1.0) to Blender's UI range (0.2-0.85)
                ui_progress = 0.2 + (progress_value * 0.65)
                self.update_progress(ui_progress)
                m, s = divmod(int(elapsed), 60)
                msg = status_result.get("message", job_status)
                self.update_stats("", f"{msg}... {m}m {s}s")
                time.sleep(1.0)
            else:
                self.report({"ERROR"}, f"Unexpected status: {job_status}")
                return

            if elapsed > 600:
                self.report({"ERROR"}, "Timed out after 10 minutes")
                return

        # Fetch result from dispatcher
        result_data = dispatcher.get_job_result(job_id)
        if not result_data:
            self.report({"ERROR"}, "Failed to fetch render result")
            return

        if result_data.get("status") != "success":
            error_msg = result_data.get("error", "Unknown error")
            self.report({"ERROR"}, f"Render result error: {error_msg}")
            return

        # Dispatcher returns image_path on its machine — fetch PNG file
        result_image_path = result_data.get("image_path")
        if not result_image_path:
            self.report({"ERROR"}, "No image path in result")
            return

        # Read PNG from dispatcher's result
        try:
            png_bytes = self._fetch_result_png(result_image_path)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to fetch PNG: {e}")
            return

        if not png_bytes:
            self.report({"ERROR"}, "Empty render result")
            return

        self._display_png(png_bytes, width, height, start_time)

    def _save_scene_temp(self):
        """Save current Blender scene to temp .blend file. Returns path (or None on error)."""
        tmp = tempfile.NamedTemporaryFile(suffix=".blend", delete=False)
        tmp.close()
        try:
            bpy.ops.wm.save_as_mainfile(filepath=tmp.name, copy=True)
            return tmp.name
        except Exception as e:
            print(f"[RemoteGPU] Scene save failed: {e}")
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
            return None

    def _fetch_result_png(self, image_path):
        """Fetch PNG result from dispatcher's image_path.

        For now, assumes the path is accessible (local or mounted filesystem).
        In future, could support HTTP fetch or dispatcher streaming.
        """
        with open(image_path, "rb") as f:
            return f.read()

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
