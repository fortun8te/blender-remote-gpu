"""
Remote GPU Live Preview — b25 dual-viewport architecture.

How it works:
  LEFT viewport  → user navigates normally (Solid mode, 60fps, Mac GPU)
  RIGHT viewport → shows rendered result from RTX 5080 as a draw overlay

Flow:
  1. Camera watch timer fires every 100ms on main thread
  2. Detects camera movement across any 3D viewport
  3. Debounce: waits 150ms after camera stops
  4. Fires background render thread → remote renders 1spp + OptiX denoise
  5. Render result stored in _pending_frame (bytes)
  6. Next timer tick decodes bytes → GPUTexture (must be main thread)
  7. SpaceView3D draw handler blits texture over the preview viewport
  8. Progressive refinement: 1spp shown immediately, then 4spp follows

Usage:
  1. Connect to server
  2. Click "Upload Scene" (one-time, re-upload when scene changes)
  3. Split viewport: left = your workspace, right = preview
  4. Hover over RIGHT viewport, click "Set as Preview Viewport" in N-panel
  5. Click "Start Live Preview"
  6. Navigate in LEFT viewport — right viewport updates automatically
"""

import bpy
import threading
import base64
import time
import os
import tempfile

try:
    import gpu
    from gpu_extras.presets import draw_texture_2d
    HAS_GPU = True
except ImportError:
    HAS_GPU = False

# ── Global state ───────────────────────────────────────────────

_preview_active = False
_preview_area_ptr = None     # int: area.as_pointer() of the preview viewport
_draw_handler = None         # handle from SpaceView3D.draw_handler_add
_preview_gpu_texture = None  # gpu.types.GPUTexture (main thread only)

_pending_frame = None        # bytes: written by render thread, read by main thread
_pending_frame_lock = threading.Lock()

_last_view_matrices = {}     # {area_ptr: matrix} for change detection
_last_camera_move_time = 0.0
_render_in_flight = False
_vp_version = 0              # increments each camera move — threads check this

DEBOUNCE_SECS = 0.15         # wait 150ms after camera stops before rendering
TIMER_INTERVAL = 0.1         # camera check frequency (10fps)

# Status string shown in UI
_status = ""
_last_frame_ms = 0
_last_samples = 0


def _log(msg):
    print(f"[RemoteGPU Live] {msg}")


# ── Camera watching ────────────────────────────────────────────

def _scan_viewports():
    """Scan all 3D viewports. Return (matrix, area_ptr) if any camera moved."""
    global _last_view_matrices

    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                for space in area.spaces:
                    if space.type != 'VIEW_3D':
                        continue
                    rv3d = space.region_3d
                    if not rv3d:
                        continue
                    mat = tuple(tuple(row) for row in rv3d.view_matrix)
                    key = area.as_pointer()
                    if _last_view_matrices.get(key) != mat:
                        _last_view_matrices[key] = mat
                        # Don't render from the preview viewport itself
                        if key != _preview_area_ptr:
                            return [list(row) for row in rv3d.view_matrix], key
    except Exception:
        pass
    return None, None


def _camera_watch_timer():
    """Main thread timer — watches cameras, processes pending frames."""
    global _last_camera_move_time, _vp_version, _render_in_flight, _status

    if not _preview_active:
        return None  # Unregister timer

    # 1. Process any decoded frame waiting from render thread
    _process_pending_frame()

    # 2. Check if any viewport camera moved
    moved_matrix, _moved_area = _scan_viewports()

    if moved_matrix is not None:
        # Camera moved — reset debounce
        _last_camera_move_time = time.time()
        _vp_version += 1

    elif (not _render_in_flight
          and _last_camera_move_time > 0
          and (time.time() - _last_camera_move_time) >= DEBOUNCE_SECS):
        # Camera has been stable long enough — find current matrix and render
        _last_camera_move_time = 0  # Reset so we don't re-trigger until next move

        # Get current matrix from any non-preview 3D view
        current_matrix = _get_current_view_matrix()
        if current_matrix:
            _fire_render(current_matrix)

    return TIMER_INTERVAL


def _get_current_view_matrix():
    """Get view matrix from first non-preview 3D viewport."""
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                if area.as_pointer() == _preview_area_ptr:
                    continue  # Skip the preview viewport
                for space in area.spaces:
                    if space.type == 'VIEW_3D' and space.region_3d:
                        return [list(row) for row in space.region_3d.view_matrix]
    except Exception:
        pass
    return None


# ── Rendering ─────────────────────────────────────────────────

def _fire_render(view_matrix):
    """Launch background render thread. Progressive: 1spp → 4spp."""
    global _render_in_flight, _status

    from .engine import RemoteRenderEngine
    conn = RemoteRenderEngine._connection
    if not conn or not conn.connected:
        return
    scene_id = RemoteRenderEngine._scene_id
    if not scene_id:
        _status = "Upload scene first"
        return

    _render_in_flight = True
    my_version = _vp_version
    _status = "Rendering..."

    def _do_render():
        global _render_in_flight, _pending_frame, _status, _last_frame_ms, _last_samples

        try:
            for samples in [1, 4]:
                if _vp_version != my_version:
                    return  # New camera position arrived — abort

                t_start = time.time()
                result = conn.viewport_render(
                    scene_id, 960, 540,
                    view_matrix, None,
                    samples=samples
                )

                if _vp_version != my_version:
                    return  # Stale result — discard

                if not result:
                    _status = "No response from server"
                    return

                img_b64 = result.get("jpg_b64") or result.get("png_b64", "")
                if not img_b64:
                    _status = "Empty render result"
                    return

                elapsed = int((time.time() - t_start) * 1000)
                _last_frame_ms = elapsed
                _last_samples = samples
                _status = f"{samples}spp — {elapsed}ms"

                with _pending_frame_lock:
                    _pending_frame = base64.b64decode(img_b64)

        except Exception as e:
            _status = f"Error: {e}"
            _log(f"Render error: {e}")
        finally:
            _render_in_flight = False

    threading.Thread(target=_do_render, daemon=True).start()


# ── Frame processing (main thread) ────────────────────────────

def _process_pending_frame():
    """Decode pending frame bytes into GPUTexture. Must run on main thread."""
    global _pending_frame, _preview_gpu_texture

    with _pending_frame_lock:
        frame_bytes = _pending_frame
        _pending_frame = None

    if frame_bytes is None or not HAS_GPU:
        return

    # Try PIL first (fast, no disk I/O)
    try:
        from PIL import Image
        import numpy as np
        from io import BytesIO

        img = Image.open(BytesIO(frame_bytes)).convert("RGBA")
        img_w, img_h = img.size
        pixels = np.flipud(np.array(img, dtype=np.float32) / 255.0).flatten().tolist()
        _upload_texture(img_w, img_h, pixels)
        return

    except ImportError:
        pass
    except Exception as e:
        _log(f"PIL decode error: {e}")

    # Fallback: write temp file, load via bpy
    try:
        suffix = ".jpg" if frame_bytes[:2] == b'\xff\xd8' else ".png"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(frame_bytes)
        tmp.close()

        bpy_img = bpy.data.images.load(tmp.name, check_existing=False)
        img_w, img_h = bpy_img.size
        pix = list(bpy_img.pixels[:])
        bpy.data.images.remove(bpy_img)
        os.unlink(tmp.name)

        # Flip vertically (Blender pixels are bottom-up)
        stride = img_w * 4
        rows = [pix[i:i + stride] for i in range(0, len(pix), stride)]
        flat = [v for row in reversed(rows) for v in row]

        _upload_texture(img_w, img_h, flat)

    except Exception as e:
        _log(f"Bpy image decode error: {e}")


def _upload_texture(w, h, flat_pixels):
    """Create or replace GPUTexture and force preview viewport redraw."""
    global _preview_gpu_texture

    try:
        buf = gpu.types.Buffer('FLOAT', w * h * 4, flat_pixels)

        if _preview_gpu_texture is not None:
            try:
                del _preview_gpu_texture
            except Exception:
                pass

        _preview_gpu_texture = gpu.types.GPUTexture((w, h), format='RGBA16F', data=buf)

        # Force all 3D viewports to redraw (draw handler will filter to preview only)
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        except Exception:
            pass

    except Exception as e:
        _log(f"Texture upload error: {e}")


# ── Draw handler ───────────────────────────────────────────────

def _draw_preview_overlay():
    """SpaceView3D POST_PIXEL draw callback.

    Fires for EVERY 3D viewport on every redraw. We filter to only draw
    in the designated preview area using the stored area pointer.
    """
    if not HAS_GPU or _preview_gpu_texture is None:
        return
    if _preview_area_ptr is None:
        return

    try:
        area = bpy.context.area
        if area is None or area.as_pointer() != _preview_area_ptr:
            return

        region = bpy.context.region
        if region is None:
            return

        w, h = region.width, region.height

        # Draw the rendered texture over the full viewport
        gpu.state.blend_set('ALPHA_PREMULT')
        draw_texture_2d(_preview_gpu_texture, (0, 0), w, h)
        gpu.state.blend_set('NONE')

    except Exception:
        pass  # Silently skip — context can be invalid during redraws


# ── Registration ───────────────────────────────────────────────

def start_preview():
    """Start the camera watcher and draw handler."""
    global _preview_active, _draw_handler, _status, _last_camera_move_time

    if _preview_active:
        return

    _preview_active = True
    _last_camera_move_time = 0.0
    _status = "Running — navigate in other viewport"

    # Register draw handler for ALL 3D viewports (we filter inside the callback)
    if HAS_GPU and _draw_handler is None:
        globals()['_draw_handler'] = bpy.types.SpaceView3D.draw_handler_add(
            _draw_preview_overlay, (), 'WINDOW', 'POST_PIXEL'
        )

    # Start camera watch timer
    if not bpy.app.timers.is_registered(_camera_watch_timer):
        bpy.app.timers.register(_camera_watch_timer,
                                first_interval=TIMER_INTERVAL,
                                persistent=True)

    _log("Live preview started")


def stop_preview():
    """Stop the camera watcher and remove the draw overlay."""
    global _preview_active, _draw_handler, _preview_gpu_texture, _status

    _preview_active = False
    _status = "Stopped"

    if _draw_handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handler, 'WINDOW')
        except Exception:
            pass
        globals()['_draw_handler'] = None

    if _preview_gpu_texture is not None:
        try:
            del _preview_gpu_texture
        except Exception:
            pass
        globals()['_preview_gpu_texture'] = None

    # Timer self-unregisters when _preview_active is False (returns None)
    _log("Live preview stopped")


def set_preview_area(area):
    """Designate an area as the preview viewport."""
    global _preview_area_ptr
    _preview_area_ptr = area.as_pointer()
    _log(f"Preview viewport set (area {_preview_area_ptr})")


def is_active():
    return _preview_active


def get_status():
    return _status


# ── Operators ─────────────────────────────────────────────────

class REMOTEGPU_OT_upload_scene(bpy.types.Operator):
    """Upload current scene to the remote GPU worker (keeps it in VRAM)."""
    bl_idname = "remotegpu.upload_scene"
    bl_label = "Upload Scene"
    bl_description = "Save and upload current .blend to the remote GPU worker"

    _timer = None
    _thread = None
    _result = None

    def execute(self, context):
        from .engine import RemoteRenderEngine
        conn = RemoteRenderEngine._connection
        if not conn or not conn.connected:
            self.report({'ERROR'}, "Not connected — connect first")
            return {'CANCELLED'}

        # bpy.ops MUST run on the main thread — save scene here in execute(),
        # then pass base64 data to the background thread for the HTTP upload.
        import tempfile, os, base64 as b64

        tmp = tempfile.NamedTemporaryFile(suffix=".blend", delete=False)
        tmp.close()
        try:
            bpy.ops.wm.save_as_mainfile(filepath=tmp.name, copy=True)
            with open(tmp.name, "rb") as f:
                blend_b64 = b64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            self.report({'ERROR'}, f"Scene save failed: {e}")
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
            return {'CANCELLED'}
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

        size_kb = len(blend_b64) * 3 // 4 // 1024
        self.report({'INFO'}, f"Uploading scene ({size_kb} KB)...")
        REMOTEGPU_OT_upload_scene._result = None

        # Only the HTTP transfer runs in background
        def _upload(blend_data):
            from . import engine as eng
            scene_id = conn.upload_scene(blend_data)
            if scene_id:
                eng.RemoteRenderEngine._scene_id = scene_id
                eng.RemoteRenderEngine._scene_uploaded = True
                REMOTEGPU_OT_upload_scene._result = "ok"
            else:
                REMOTEGPU_OT_upload_scene._result = "Upload failed — check server logs"

        REMOTEGPU_OT_upload_scene._thread = threading.Thread(
            target=_upload, args=(blend_b64,), daemon=True
        )
        REMOTEGPU_OT_upload_scene._thread.start()

        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if REMOTEGPU_OT_upload_scene._thread and REMOTEGPU_OT_upload_scene._thread.is_alive():
            return {'PASS_THROUGH'}

        context.window_manager.event_timer_remove(self._timer)
        result = REMOTEGPU_OT_upload_scene._result or "Unknown error"

        if result == "ok":
            self.report({'INFO'}, "Scene uploaded — ready for live preview")
        else:
            self.report({'ERROR'}, result)

        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


class REMOTEGPU_OT_set_preview_viewport(bpy.types.Operator):
    """Designate the current 3D viewport as the Remote GPU preview target."""
    bl_idname = "remotegpu.set_preview_viewport"
    bl_label = "Set as Preview Viewport"
    bl_description = "This viewport will show the Remote GPU rendered result"

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D'

    def execute(self, context):
        set_preview_area(context.area)
        self.report({'INFO'}, "This viewport is now the Remote GPU preview")
        context.area.tag_redraw()
        return {'FINISHED'}


class REMOTEGPU_OT_start_live_preview(bpy.types.Operator):
    """Start Remote GPU live preview — renders update as you navigate."""
    bl_idname = "remotegpu.start_live_preview"
    bl_label = "Start Live Preview"
    bl_description = "Start streaming rendered frames from the remote GPU"

    def execute(self, context):
        from .engine import RemoteRenderEngine
        conn = RemoteRenderEngine._connection

        if not conn or not conn.connected:
            self.report({'ERROR'}, "Not connected — connect first")
            return {'CANCELLED'}

        if not RemoteRenderEngine._scene_uploaded:
            self.report({'ERROR'}, "Upload scene first")
            return {'CANCELLED'}

        if _preview_area_ptr is None:
            self.report({'WARNING'},
                "No preview viewport set — hover over the right viewport and click "
                "'Set as Preview Viewport' first")

        start_preview()
        self.report({'INFO'}, "Live preview running — navigate in another viewport")

        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


class REMOTEGPU_OT_stop_live_preview(bpy.types.Operator):
    """Stop the Remote GPU live preview."""
    bl_idname = "remotegpu.stop_live_preview"
    bl_label = "Stop Live Preview"
    bl_description = "Stop streaming rendered frames"

    def execute(self, context):
        stop_preview()
        self.report({'INFO'}, "Live preview stopped")
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


# All operator classes for registration
classes = [
    REMOTEGPU_OT_upload_scene,
    REMOTEGPU_OT_set_preview_viewport,
    REMOTEGPU_OT_start_live_preview,
    REMOTEGPU_OT_stop_live_preview,
]
