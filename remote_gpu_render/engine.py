"""Remote GPU render engine b24 — F12 render + progressive live viewport.

b24 upgrades:
- Progressive viewport: 1 → 4 → 16 samples, each result shown immediately
- Version-based cancellation: new camera position cancels stale renders
- Reliable image decode: PIL/numpy fast path + bpy.app.timers fallback (no PIL needed)
- Camera-only change detection: skips upload when only view matrix changed
"""

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

    _connection = None
    _scene_id = None
    _scene_uploaded = False

    # b24: version counter for progressive render cancellation
    # Each new camera position increments this; running threads compare against it
    _vp_version = 0

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

        blend_b64 = self._save_scene_b64()
        if not blend_b64:
            self.report({"ERROR"}, "Failed to save scene")
            return

        if self.test_break():
            return

        size_kb = len(blend_b64) * 3 // 4 // 1024
        self.update_stats("", f"Uploading scene ({size_kb} KB)...")
        self.update_progress(0.1)

        scene_id = conn.upload_scene(blend_b64)
        if not scene_id:
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

        b24 upgrade: progressive sampling (1→4→16) with version-based
        cancellation so stale renders don't overwrite new camera positions.
        Camera-only changes skip scene re-upload.
        """
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

        # Detect what actually changed
        geo_changed = False
        for update in depsgraph.updates:
            if update.is_updated_geometry or update.is_updated_shading:
                geo_changed = True
                break

        # Current view matrix
        view_matrix = [list(row) for row in rv3d.view_matrix]
        proj_matrix = [list(row) for row in rv3d.window_matrix]

        # Skip if nothing changed
        camera_moved = (view_matrix != self._last_view_matrix)
        if not camera_moved and not geo_changed and RemoteRenderEngine._scene_uploaded:
            return
        self._last_view_matrix = view_matrix

        # Need to (re-)upload scene?
        if not RemoteRenderEngine._scene_uploaded or geo_changed:
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

        scene_id = RemoteRenderEngine._scene_id
        if not scene_id:
            return

        # Cap viewport resolution for speed
        vp_w = min(w, 960)
        vp_h = min(h, 540)

        # b24: bump version — any in-flight progressive render sees this and stops
        RemoteRenderEngine._vp_version += 1
        my_version = RemoteRenderEngine._vp_version

        def _progressive_render():
            """Render at 1 → 4 → 16 samples, showing each result immediately.
            Stops early if _vp_version has changed (newer camera position arrived).
            With OptiX denoiser on worker: 1spp already looks clean.
            """
            sample_levels = [1, 4, 16]

            for samples in sample_levels:
                # Check if superseded by a newer request
                if RemoteRenderEngine._vp_version != my_version:
                    return

                try:
                    result = conn.viewport_render(
                        scene_id, vp_w, vp_h, view_matrix, proj_matrix,
                        samples=samples
                    )
                    if not result:
                        return

                    # Check again — render took time, new camera may have arrived
                    if RemoteRenderEngine._vp_version != my_version:
                        return

                    img_b64 = result.get("jpg_b64") or result.get("png_b64", "")
                    if img_b64:
                        img_bytes = base64.b64decode(img_b64)
                        self._update_viewport_pixels(vp_w, vp_h, img_bytes)
                        self.tag_redraw()

                except Exception as e:
                    print(f"[RemoteGPU] Viewport error (spp={samples}): {e}")
                    return

        self._viewport_thread = threading.Thread(target=_progressive_render, daemon=True)
        self._viewport_thread.start()

    def _update_viewport_pixels(self, width, height, img_bytes):
        """Decode JPEG/PNG bytes to float RGBA pixels for GPUTexture.

        b24: PIL/numpy fast path + bpy.app.timers fallback so it works
        even without PIL installed in Blender's Python.
        """
        # Fast path: PIL + numpy (no main thread needed)
        try:
            import numpy as np
            from PIL import Image
            from io import BytesIO

            img = Image.open(BytesIO(img_bytes)).convert("RGBA")
            img = img.resize((width, height))
            pixels = np.flipud(np.array(img, dtype=np.float32) / 255.0).flatten().tolist()

            if self.draw_data:
                self.draw_data.update(width, height, pixels)
            else:
                self.draw_data = ViewportDrawData(width, height, pixels)
            return

        except ImportError:
            pass  # PIL not available — use timer fallback
        except Exception as e:
            print(f"[RemoteGPU] PIL decode error: {e}")

        # Fallback: write temp file, load via bpy on main thread using timer
        # bpy.data.images.load() must run on the main thread — timers fire there.
        suffix = ".jpg" if img_bytes[:2] == b'\xff\xd8' else ".png"
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp.write(img_bytes)
            tmp.close()
            tmp_path = tmp.name
        except Exception as e:
            print(f"[RemoteGPU] Temp file error: {e}")
            return

        engine_ref = self

        def _load_on_main_thread():
            """Runs on Blender main thread — safe to call bpy here."""
            try:
                bpy_img = bpy.data.images.load(tmp_path, check_existing=False)
                img_w, img_h = bpy_img.size
                pix = list(bpy_img.pixels[:])
                bpy.data.images.remove(bpy_img)

                # Flip vertically (Blender pixels are bottom-up, we need top-down)
                row_stride = img_w * 4
                rows = [pix[i:i + row_stride] for i in range(0, len(pix), row_stride)]
                flat = [v for row in reversed(rows) for v in row]

                if engine_ref.draw_data:
                    engine_ref.draw_data.update(img_w, img_h, flat)
                else:
                    engine_ref.draw_data = ViewportDrawData(img_w, img_h, flat)

                engine_ref.tag_redraw()

            except Exception as e:
                print(f"[RemoteGPU] Timer pixel load error: {e}")
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            return None  # Returning None removes the timer (don't repeat)

        bpy.app.timers.register(_load_on_main_thread, first_interval=0.0)

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
