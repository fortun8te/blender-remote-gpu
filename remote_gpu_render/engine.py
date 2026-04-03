"""Remote GPU render engine — renders via HTTP REST API."""

import bpy
import os
import time
import base64
import tempfile


class RemoteRenderEngine(bpy.types.RenderEngine):
    bl_idname = "REMOTE_GPU"
    bl_label = "Remote GPU"
    bl_use_preview = False

    _connection = None  # Shared across instances

    def render(self, depsgraph):
        """Called by F12 — submit render to remote server."""
        conn = RemoteRenderEngine._connection
        if not conn or not conn.connected:
            self.report({"ERROR"}, "Not connected to render server. Click Connect first.")
            return

        scene = depsgraph.scene
        scale = scene.render.resolution_percentage / 100.0
        width = int(scene.render.resolution_x * scale)
        height = int(scene.render.resolution_y * scale)
        samples = getattr(scene.cycles, "samples", 128) if hasattr(scene, "cycles") else 128

        self.update_stats("", "Saving scene...")

        # Save scene to temp .blend
        tmp = tempfile.NamedTemporaryFile(suffix=".blend", delete=False)
        tmp.close()
        try:
            bpy.ops.wm.save_as_mainfile(filepath=tmp.name, copy=True)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to save scene: {e}")
            return

        # Read and encode as base64
        try:
            with open(tmp.name, "rb") as f:
                blend_data = f.read()
            blend_b64 = base64.b64encode(blend_data).decode("ascii")
        finally:
            os.unlink(tmp.name)

        self.update_stats("", f"Uploading ({len(blend_data) // 1024} KB)...")

        # Submit render job
        job_id = conn.submit_render(blend_b64, width, height, samples)
        if not job_id:
            self.report({"ERROR"}, "Failed to submit render job")
            return

        self.update_stats("", f"Rendering on {conn.gpu_name}...")

        # Poll for completion
        start_time = time.time()
        while True:
            if self.test_break():
                self.update_stats("", "Cancelled")
                return

            status = conn.poll_status(job_id)
            if not status:
                self.report({"ERROR"}, "Lost connection to server")
                return

            job_status = status.get("status", "unknown")
            elapsed = time.time() - start_time

            if job_status == "complete":
                self.update_stats("", "Downloading result...")
                break
            elif job_status == "error":
                error = status.get("error", "Unknown render error")
                self.report({"ERROR"}, f"Render failed: {error}")
                return
            elif job_status in ("queued", "rendering"):
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                self.update_stats("", f"Rendering... {mins}m {secs}s")
                time.sleep(1.0)
            else:
                self.report({"ERROR"}, f"Unknown job status: {job_status}")
                return

            if elapsed > 600:  # 10 min timeout
                self.report({"ERROR"}, "Render timed out (10 min)")
                return

        # Fetch result
        result_data = conn.get_result(job_id)
        if not result_data or result_data.get("type") == "error":
            error = result_data.get("message", "Unknown") if result_data else "No response"
            self.report({"ERROR"}, f"Failed to get result: {error}")
            return

        png_b64 = result_data.get("png_b64", "")
        if not png_b64:
            self.report({"ERROR"}, "Empty render result")
            return

        png_bytes = base64.b64decode(png_b64)

        # Decode PNG to pixels
        try:
            import numpy as np
            from io import BytesIO

            # Try Pillow (ships with Blender)
            try:
                from PIL import Image
                img = Image.open(BytesIO(png_bytes))
                img = img.convert("RGBA")
                pixels = np.array(img, dtype=np.float32) / 255.0
                pixels = np.flipud(pixels)  # Blender expects bottom-up
            except ImportError:
                # Fallback: save PNG, load with Blender
                tmp_png = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp_png.write(png_bytes)
                tmp_png.close()
                bpy_img = bpy.data.images.load(tmp_png.name)
                pixels = np.array(bpy_img.pixels[:]).reshape(height, width, 4).astype(np.float32)
                bpy.data.images.remove(bpy_img)
                os.unlink(tmp_png.name)

            # Write to render result
            result = self.begin_result(0, 0, width, height)
            layer = result.layers[0].passes["Combined"]
            layer.rect = pixels.reshape(-1, 4).tolist()
            self.end_result(result)

            elapsed = time.time() - start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            self.update_stats("", f"Done ({mins}m {secs}s)")

        except Exception as e:
            self.report({"ERROR"}, f"Failed to decode result: {e}")
