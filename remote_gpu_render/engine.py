"""Remote GPU render engine — F12 render + live viewport streaming."""

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
    """Holds a GPUTexture for viewport rendering."""

    def __init__(self, width, height, pixels=None):
        self.dimensions = (width, height)
        if pixels is not None:
            buf = gpu.types.Buffer('FLOAT', width * height * 4, pixels)
        else:
            flat = array.array('f', [0.0, 0.0, 0.0, 1.0]) * (width * height)
            buf = gpu.types.Buffer('FLOAT', width * height * 4, flat)
        self.texture = gpu.types.GPUTexture((width, height), format='RGBA16F', data=buf)

    def update(self, width, height, pixels):
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
    bl_use_gpu_context = True
    bl_use_eevee_viewport = True

    _connection = None       # Shared connection
    _scene_id = None         # Cached scene ID on server
    _scene_uploaded = False  # Whether scene has been uploaded this session

    def __init__(self):
        self.draw_data = None
        self._viewport_thread = None
        self._last_view_matrix = None

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

        # Save and encode scene
        blend_b64 = self._save_scene_b64()
        if not blend_b64:
            self.report({"ERROR"}, "Failed to save scene")
            return

        if self.test_break():
            return

        size_kb = len(blend_b64) * 3 // 4 // 1024  # Approximate decoded size
        self.update_stats("", f"Uploading scene ({size_kb} KB)...")
        self.update_progress(0.1)

        # Upload scene to cache, then render from cache
        scene_id = conn.upload_scene(blend_b64)
        if not scene_id:
            # Fallback: inline render
            job_id = conn.submit_render(blend_b64, width, height, samples)
        else:
            RemoteRenderEngine._scene_id = scene_id
            RemoteRenderEngine._scene_uploaded = True
            job_id = conn.submit_render_cached(scene_id, width, height, samples)

        if not job_id:
            self.report({"ERROR"}, "Failed to submit render")
            return

        self.update_stats("", f"Rendering on {conn.gpu_name}...")
        self.update_progress(0.2)

        # Poll for completion
        start_time = time.time()
        while True:
            if self.test_break():
                return

            status = conn.poll_status(job_id)
            if not status:
                self.report({"ERROR"}, "Lost connection")
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
                self.report({"ERROR"}, f"Unknown status: {job_status}")
                return

            if elapsed > 600:
                self.report({"ERROR"}, "Timed out (10 min)")
                return

        # Fetch and display result
        result_data = conn.get_result(job_id)
        if not result_data or not result_data.get("png_b64"):
            self.report({"ERROR"}, "Empty result")
            return

        png_bytes = base64.b64decode(result_data["png_b64"])
        self._display_png(png_bytes, width, height, start_time)

    def _save_scene_b64(self):
        """Save current scene to temp .blend and return base64."""
        tmp = tempfile.NamedTemporaryFile(suffix=".blend", delete=False)
        tmp.close()
        try:
            bpy.ops.wm.save_as_mainfile(filepath=tmp.name, copy=True)
            with open(tmp.name, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            print(f"[RemoteGPU] Save failed: {e}")
            return None
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    def _display_png(self, png_bytes, width, height, start_time):
        """Decode PNG and write pixels to render result."""
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
            layer.rect = [pixel_data[i:i+4] for i in range(0, len(pixel_data), 4)]
            self.end_result(result)

            self.update_progress(1.0)
            m, s = divmod(int(time.time() - start_time), 60)
            self.update_stats("", f"Done ({m}m {s}s)")

        except Exception as e:
            self.report({"ERROR"}, f"Failed to decode: {e}")

    # ── VIEWPORT LIVE RENDERING ───────────────────────────────

    def view_update(self, context, depsgraph):
        """Called when scene changes in viewport Rendered mode.
        Upload scene once, then stream camera updates."""
        conn = RemoteRenderEngine._connection
        if not conn or not conn.connected:
            return

        region = context.region
        rv3d = context.region_data
        if not region or not rv3d:
            return

        w, h = region.width, region.height
        if w < 1 or h < 1:
            return

        # Upload scene on first enter or when scene changes
        scene_changed = False
        for update in depsgraph.updates:
            if update.is_updated_geometry or update.is_updated_transform:
                scene_changed = True
                break

        if not RemoteRenderEngine._scene_uploaded or scene_changed:
            # Upload scene in background (don't block UI)
            def _upload():
                blend_b64 = self._save_scene_b64()
                if blend_b64:
                    sid = conn.upload_scene(blend_b64)
                    if sid:
                        RemoteRenderEngine._scene_id = sid
                        RemoteRenderEngine._scene_uploaded = True
                        print(f"[RemoteGPU] Scene cached: {sid}")
                        self.tag_redraw()

            if not (self._viewport_thread and self._viewport_thread.is_alive()):
                self._viewport_thread = threading.Thread(target=_upload, daemon=True)
                self._viewport_thread.start()
            return

        # Scene is cached — send viewport render request
        if self._viewport_thread and self._viewport_thread.is_alive():
            return  # Still working on previous frame

        view_matrix = [list(row) for row in rv3d.view_matrix]
        proj_matrix = [list(row) for row in rv3d.window_matrix]

        # Skip if camera hasn't moved
        if view_matrix == self._last_view_matrix:
            return
        self._last_view_matrix = view_matrix

        scene_id = RemoteRenderEngine._scene_id
        if not scene_id:
            return

        # Cap resolution for speed
        vp_w = min(w, 960)
        vp_h = min(h, 540)

        def _render_viewport():
            try:
                result = conn.viewport_render(scene_id, vp_w, vp_h, view_matrix, proj_matrix)
                if result and result.get("png_b64"):
                    png_bytes = base64.b64decode(result["png_b64"])
                    self._update_viewport_pixels(w, h, png_bytes)
                    self.tag_redraw()
                elif result and result.get("status") == "queued":
                    # Frame is being rendered, poll for it
                    time.sleep(0.5)
                    poll = conn.viewport_poll(scene_id)
                    if poll and poll.get("png_b64"):
                        png_bytes = base64.b64decode(poll["png_b64"])
                        self._update_viewport_pixels(w, h, png_bytes)
                        self.tag_redraw()
            except Exception as e:
                print(f"[RemoteGPU] Viewport error: {e}")

        self._viewport_thread = threading.Thread(target=_render_viewport, daemon=True)
        self._viewport_thread.start()

    def _update_viewport_pixels(self, width, height, png_bytes):
        """Decode PNG to float pixels for viewport texture."""
        try:
            import numpy as np
            from PIL import Image
            from io import BytesIO

            img = Image.open(BytesIO(png_bytes))
            img = img.convert("RGBA")
            img = img.resize((width, height))
            pixels = np.array(img, dtype=np.float32) / 255.0
            pixels = np.flipud(pixels)
            flat = pixels.flatten().tolist()

            if self.draw_data:
                self.draw_data.update(width, height, flat)
            else:
                self.draw_data = ViewportDrawData(width, height, flat)
        except ImportError:
            # No PIL — write to temp file and load with Blender
            # (can't call bpy from thread, so skip viewport in this case)
            pass
        except Exception as e:
            print(f"[RemoteGPU] Pixel decode error: {e}")

    def view_draw(self, context, depsgraph):
        """Called every viewport redraw — blit the latest texture."""
        if not HAS_GPU or not self.draw_data:
            return

        scene = depsgraph.scene
        gpu.state.blend_set('ALPHA_PREMULT')
        self.bind_display_space_shader(scene)
        self.draw_data.draw()
        self.unbind_display_space_shader()
        gpu.state.blend_set('NONE')
