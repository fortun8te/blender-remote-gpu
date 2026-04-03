"""Remote GPU render engine — F12 render + viewport rendered mode."""

import bpy
import array
import os
import time
import base64
import tempfile
import threading

try:
    import gpu
    from gpu_extras.presets import draw_texture_2d
    HAS_GPU = True
except ImportError:
    HAS_GPU = False


class ViewportDrawData:
    """Holds a GPUTexture for viewport rendering. Recreated on each update."""

    def __init__(self, width, height, pixels=None):
        self.dimensions = (width, height)
        if pixels is not None:
            buf = gpu.types.Buffer('FLOAT', width * height * 4, pixels)
        else:
            # Black with alpha
            flat = array.array('f', [0.0, 0.0, 0.0, 1.0]) * (width * height)
            buf = gpu.types.Buffer('FLOAT', width * height * 4, flat)
        self.texture = gpu.types.GPUTexture((width, height), format='RGBA16F', data=buf)

    def update(self, width, height, pixels):
        """Replace texture with new pixel data."""
        del self.texture
        self.dimensions = (width, height)
        buf = gpu.types.Buffer('FLOAT', width * height * 4, pixels)
        self.texture = gpu.types.GPUTexture((width, height), format='RGBA16F', data=buf)

    def draw(self):
        draw_texture_2d(self.texture, (0, 0), self.texture.width, self.texture.height)

    def __del__(self):
        try:
            del self.texture
        except Exception:
            pass


class RemoteRenderEngine(bpy.types.RenderEngine):
    bl_idname = "REMOTE_GPU"
    bl_label = "Remote GPU"
    bl_use_preview = False
    bl_use_gpu_context = True        # Need GPU context for view_draw
    bl_use_eevee_viewport = True     # Use Eevee for Material/Solid modes

    _connection = None  # Shared across all engine instances

    def __init__(self):
        self.draw_data = None
        self._viewport_thread = None

    def __del__(self):
        pass

    # ── F12 FINAL RENDER ──────────────────────────────────────

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
        samples = 128
        if hasattr(scene, "cycles"):
            samples = getattr(scene.cycles, "samples", 128)

        self.update_stats("", "Saving scene...")
        self.update_progress(0.0)

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

        size_kb = len(blend_data) // 1024
        self.update_stats("", f"Uploading ({size_kb} KB)...")
        self.update_progress(0.1)

        if self.test_break():
            return

        # Submit render job
        job_id = conn.submit_render(blend_b64, width, height, samples)
        if not job_id:
            self.report({"ERROR"}, "Failed to submit render job")
            return

        self.update_stats("", f"Rendering on {conn.gpu_name}...")
        self.update_progress(0.2)

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
                self.update_progress(0.9)
                break
            elif job_status == "error":
                error = status.get("error", "Unknown render error")
                self.report({"ERROR"}, f"Render failed: {error}")
                return
            elif job_status in ("queued", "rendering"):
                progress = min(0.2 + (elapsed / 300.0) * 0.7, 0.85)
                self.update_progress(progress)
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                self.update_stats("", f"Rendering on {conn.gpu_name}... {mins}m {secs}s")
                time.sleep(1.0)
            else:
                self.report({"ERROR"}, f"Unknown job status: {job_status}")
                return

            if elapsed > 600:
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
        self._display_png(png_bytes, width, height, start_time)

    def _display_png(self, png_bytes, width, height, start_time):
        """Decode PNG and write pixels to render result."""
        try:
            # Save PNG to temp, load with Blender (most reliable method)
            tmp_png = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_png.write(png_bytes)
            tmp_png.close()

            try:
                bpy_img = bpy.data.images.load(tmp_png.name)
                pixel_data = list(bpy_img.pixels[:])
                bpy.data.images.remove(bpy_img)
            finally:
                os.unlink(tmp_png.name)

            # Write to render result
            result = self.begin_result(0, 0, width, height)
            layer = result.layers[0].passes["Combined"]
            layer.rect = [pixel_data[i:i+4] for i in range(0, len(pixel_data), 4)]
            self.end_result(result)

            self.update_progress(1.0)
            elapsed = time.time() - start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            self.update_stats("", f"Done ({mins}m {secs}s)")

        except Exception as e:
            self.report({"ERROR"}, f"Failed to decode result: {e}")

    # ── VIEWPORT RENDERED MODE ────────────────────────────────

    def view_update(self, context, depsgraph):
        """Called when scene changes in viewport Rendered mode."""
        conn = RemoteRenderEngine._connection
        if not conn or not conn.connected:
            return

        region = context.region
        if not region:
            return

        w, h = region.width, region.height
        if w < 1 or h < 1:
            return

        # Extract view/projection matrices from 3D viewport
        rv3d = context.region_data
        if not rv3d:
            return

        view_matrix = [list(row) for row in rv3d.view_matrix]
        proj_matrix = [list(row) for row in rv3d.window_matrix]

        # Don't start another render if one is in progress
        if self._viewport_thread and self._viewport_thread.is_alive():
            return

        # Render in background thread
        def _do_viewport_render():
            try:
                result = conn.send({
                    "type": "viewport_render",
                    "width": min(w, 960),   # Cap viewport resolution
                    "height": min(h, 540),
                    "view_matrix": view_matrix,
                    "proj_matrix": proj_matrix,
                })

                if result and result.get("type") == "viewport_result":
                    png_b64 = result.get("png_b64", "")
                    if png_b64:
                        # Decode and store for view_draw
                        png_bytes = base64.b64decode(png_b64)
                        self._update_viewport_pixels(w, h, png_bytes)
                        self.tag_redraw()
            except Exception:
                pass

        self._viewport_thread = threading.Thread(target=_do_viewport_render, daemon=True)
        self._viewport_thread.start()

    def _update_viewport_pixels(self, width, height, png_bytes):
        """Decode PNG to float pixels for viewport display."""
        try:
            # Quick decode via temp file
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.write(png_bytes)
            tmp.close()

            import numpy as np
            from PIL import Image
            img = Image.open(tmp.name)
            img = img.convert("RGBA")
            img = img.resize((width, height))
            pixels = np.array(img, dtype=np.float32) / 255.0
            pixels = np.flipud(pixels)  # Bottom-up for Blender

            flat = pixels.flatten().tolist()
            if self.draw_data:
                self.draw_data.update(width, height, flat)
            else:
                self.draw_data = ViewportDrawData(width, height, flat)

            os.unlink(tmp.name)
        except Exception:
            pass

    def view_draw(self, context, depsgraph):
        """Called every viewport redraw — just blit the texture."""
        if not HAS_GPU or not self.draw_data:
            return

        scene = depsgraph.scene

        gpu.state.blend_set('ALPHA_PREMULT')
        self.bind_display_space_shader(scene)

        self.draw_data.draw()

        self.unbind_display_space_shader()
        gpu.state.blend_set('NONE')
