"""RemoteRenderEngine — Custom Blender RenderEngine that delegates to a remote GPU.

This engine appears in Blender's render engine dropdown as "Remote Cycles".
All rendering happens on the remote server; this engine handles:
- Scene sync (full .blend transfer + incremental updates)
- Viewport rendering (progressive frames streamed from server)
- Final rendering (F12, full resolution)
"""

import bpy
import gpu
import numpy as np
import logging
import uuid
import time
import threading

# Issue #7: Graceful fallback for gpu_extras import
try:
    from gpu_extras.presets import draw_texture_2d
except ImportError:
    draw_texture_2d = None

from .connection import Connection
from .viewport_registry import get_viewport_registry
from .sync import (
    save_blend_to_bytes, get_view_matrices, get_render_settings, classify_depsgraph_updates,
    initialize_scene_tracker, compute_scene_delta, get_delta_metrics,
    get_current_frame, get_frame_range, get_playback_fps
)
from .frame import decode_jpeg, decode_exr
from .error_handler import show_error, show_warning, show_info, record_error
from shared.logging_config import get_logger, LogContext, PerformanceLogger
from shared.error_codes import ErrorCodes

logger = get_logger("engine")


class RemoteRenderEngine(bpy.types.RenderEngine):
    bl_idname = "REMOTE_CYCLES"
    bl_label = "Remote GPU"
    bl_use_preview = False  # No material preview for now
    bl_use_eevee_viewport = False
    bl_use_shading_nodes_custom = False

    # Shared connection across all engine instances (with thread safety)
    _connection: Connection | None = None
    _connection_lock = threading.Lock()  # Issue #5: Thread-safe lock for _connection access
    _scene_synced: bool = False
    _last_view_matrix: list | None = None
    _last_texture = None
    _last_texture_size = (0, 0)
    _last_frame_number: int | None = None
    _last_is_playing: bool | None = None
    # Phase 12 — Multi-Viewport Support
    _viewport_id: str | None = None
    _viewport_registry = None
    _textures: dict = {}  # viewport_id -> texture cache

    # --- Final Render (F12) ---

    def render(self, depsgraph):
        """Called when user hits F12 or renders an animation frame."""
        operation_id = str(uuid.uuid4())[:12]
        perf = PerformanceLogger(logger, f"final_render[{operation_id}]")

        with LogContext("final_render", logger, operation_id) as ctx:
            conn = RemoteRenderEngine._connection
            if conn is None or not conn.connected:
                ctx.log_error("Not connected to render server")
                show_error("CONN_003", "Open Render Properties > Remote GPU > Connect")
                record_error("CONN_003", "final_render", perf.mark("error"))
                return

            # Issue #15: Use depsgraph.scene instead of bpy.context.scene for render()
            scene = depsgraph.scene
            render = scene.render

            # Calculate actual resolution
            scale = render.resolution_percentage / 100.0
            res_x = int(render.resolution_x * scale)
            res_y = int(render.resolution_y * scale)

            perf.mark("resolution_calculated")

            if res_x < 1 or res_y < 1:
                ctx.log_error(f"Invalid render resolution: {res_x}x{res_y}")
                show_error("RENDER_005", f"Resolution must be at least 1x1, got {res_x}x{res_y}")
                record_error("RENDER_005", "final_render", perf.mark("error"))
                return

            self.update_stats("", "Syncing scene to server...")

            try:
                # Send full scene
                ctx.log_info(f"Syncing scene ({scene.name})...")
                blend_data = save_blend_to_bytes()
                scene_size_mb = len(blend_data) / 1024 / 1024
                perf.mark("scene_serialized")

                conn.send_scene(blend_data)
                perf.mark("scene_uploaded")
                ctx.log_info(f"Scene uploaded: {scene_size_mb:.1f} MB")

                # Wait for scene ack
                self.update_stats("", f"Uploading scene ({scene_size_mb:.1f} MB)...")

                # Get addon preferences for denoiser settings
                prefs = bpy.context.preferences.addons[__package__].preferences
                denoiser_type = prefs.denoiser_type
                denoiser_intensity = prefs.denoiser_intensity
                auto_aov = prefs.auto_aov_creation

                # Send render settings (denoiser config)
                if denoiser_type != "OFF":
                    ctx.log_info(f"Using denoiser: {denoiser_type} (intensity={denoiser_intensity})")
                    conn.send_render_settings(denoiser_type, denoiser_intensity, auto_aov)

                # Request final render
                settings = get_render_settings(bpy.context)
                samples = settings.get("samples", 128)
                ctx.log_info(f"Starting render: {res_x}x{res_y}, {samples} samples")

                conn.send_render_start(
                    resolution=(res_x, res_y),
                    samples=samples,
                    output_format="OPEN_EXR",
                )
                perf.mark("render_started")

                self.update_stats("", "Rendering on remote GPU...")

                # Wait for result with progress updates
                result_data = None
                timeout_count = 0
                max_timeout = 300  # 5 minutes
                while timeout_count < max_timeout:
                    if self.test_break():
                        ctx.log_info("Render cancelled by user")
                        conn.send_render_cancel()
                        show_info("Render cancelled")
                        return

                    frame_result = conn.get_final_frame(timeout=1.0)
                    if frame_result is not None:
                        result_data, meta = frame_result
                        break

                    timeout_count += 1
                    self.update_stats("", f"Rendering... ({timeout_count}s)")

                perf.mark("render_received")

                if result_data is None:
                    ctx.log_error(f"Render timeout after {timeout_count}s")
                    show_error("RENDER_003", f"No result after {max_timeout}s")
                    record_error("RENDER_003", "final_render", perf.mark("error"))
                    return

                # Decode result
                try:
                    pixels, width, height, pass_data = decode_exr(result_data)
                    perf.mark("frame_decoded")
                    pass_names = ", ".join(pass_data.keys()) if pass_data else "none"
                    ctx.log_info(f"Frame decoded: {width}x{height}, passes: {pass_names}")
                except Exception as e:
                    ctx.log_error(f"Frame decode failed: {e}")
                    show_error("ENCODE_001", str(e), e)
                    record_error("ENCODE_001", "final_render", perf.mark("error"))
                    return

                # Validate dimensions
                if width != res_x or height != res_y:
                    ctx.log_warning(f"Resolution mismatch: expected {res_x}x{res_y}, got {width}x{height}")
                    show_warning("RENDER_007", f"Got {width}x{height} instead of {res_x}x{res_y}")

                # Create render result
                result = self.begin_result(0, 0, res_x, res_y)
                layer = result.layers[0].passes["Combined"]

                # Pixels should be (height, width, 4) — reshape to (height*width, 4)
                pixel_count = height * width
                pixels_flat = pixels.reshape(pixel_count, 4)
                layer.rect = pixels_flat.tolist()

                self.end_result(result)
                perf.mark("result_complete")
                self.update_stats("", "Render complete")

                ctx.log_info("Render completed successfully")
                perf.log_summary()
                show_info(f"Render complete: {perf.mark('finish'):.0f}ms")

            except Exception as e:
                ctx.log_error(f"Render failed: {e}")
                show_error("RENDER_001", str(e), e)
                record_error("RENDER_001", "final_render", perf.mark("error"))
                logger.exception("Render exception")

    # --- Viewport Rendering ---

    def view_update(self, context, depsgraph):
        """Called when the viewport scene data changes (object moved, material edited, etc.).

        Uses delta synchronization to send only changed data (>99% bandwidth savings).
        Falls back to full scene sync if delta is too large or on first sync.
        """
        operation_id = str(uuid.uuid4())[:12]

        with LogContext("view_update", logger, operation_id) as ctx:
            conn = RemoteRenderEngine._connection
            if conn is None or not conn.connected:
                ctx.log_debug("Not connected, skipping view update")
                return

            try:
                if not RemoteRenderEngine._scene_synced:
                    # First sync: send full scene and initialize delta tracker
                    ctx.log_info("First sync: sending full scene...")
                    blend_data = save_blend_to_bytes()
                    conn.send_scene(blend_data)
                    RemoteRenderEngine._scene_synced = True

                    # Initialize state tracker for subsequent deltas
                    initialize_scene_tracker(context)
                    scene_size_mb = len(blend_data) / 1024 / 1024
                    ctx.log_info(f"Scene tracker initialized ({scene_size_mb:.1f} MB)")

                else:
                    # Subsequent syncs: try delta first, fallback to full if too large
                    delta = compute_scene_delta(context, max_delta_bytes=1000000)

                    if delta and not delta.full_resync_required:
                        # Send minimal delta
                        from shared.protocol import MsgType
                        delta_dict = delta.to_dict()

                        # Log bandwidth savings
                        delta_size = delta.size_estimate()
                        saved = delta.bandwidth_saved_bytes
                        if saved > 0:
                            ctx.log_info(
                                f"Delta sync: {delta_size} bytes "
                                f"(saved ~{saved / 1024 / 1024:.1f} MB)"
                            )

                        conn.send(MsgType.SCENE_DELTA, delta_dict)

                    else:
                        # Delta too large or no tracker: send full scene
                        ctx.log_warning("Full resync required (delta too large or error)")
                        blend_data = save_blend_to_bytes()
                        conn.send_scene(blend_data)
                        # Re-initialize tracker
                        initialize_scene_tracker(context)

                # Start viewport render with current camera and timeline frame
                view_mat, proj_mat, resolution = get_view_matrices(context)
                current_frame = get_current_frame(context)
                frame_start, frame_end, frame_step = get_frame_range(context)
                fps = get_playback_fps(context)
                is_playing = context.scene.is_playing

                # Phase 12 — Extract zoom level and viewport ID
                zoom_level = 100.0
                viewport_id = None
                if RemoteRenderEngine._viewport_registry is not None:
                    registry = RemoteRenderEngine._viewport_registry
                    primary = registry.get_primary_viewport()
                    if primary:
                        viewport_id = primary.viewport_id
                        zoom_level = primary.zoom_level

                conn.send_viewport_start(
                    view_mat, proj_mat, resolution,
                    current_frame=current_frame,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    frame_step=frame_step,
                    fps=fps,
                    is_playing=is_playing,
                    viewport_id=viewport_id,
                    zoom_level=zoom_level
                )
                ctx.log_debug(f"Viewport render started at frame {current_frame} (range {frame_start}-{frame_end}), viewport_id={viewport_id}")

            except Exception as e:
                ctx.log_error(f"view_update error: {e}")
                logger.exception("view_update exception")

    def view_draw(self, context, depsgraph):
        """Called every frame to draw into the viewport.

        Checks for new frames from the server and draws them as a
        fullscreen textured quad. Phase 12 — supports multiple viewports.
        """
        try:
            conn = RemoteRenderEngine._connection
            region = context.region

            if region is None or region.width < 1 or region.height < 1:
                return

            if conn is None or not conn.connected:
                logger.debug("Not connected, showing placeholder")
                self._draw_placeholder(context, "Not connected to render server")
                self.update_stats("", "Disconnected")
                return

            # Phase 12 — Register this viewport
            if RemoteRenderEngine._viewport_registry is None:
                RemoteRenderEngine._viewport_registry = get_viewport_registry()

            registry = RemoteRenderEngine._viewport_registry

            # Extract zoom level from viewport shading (approximation)
            zoom_level = 100.0
            if hasattr(region.view2d, 'zoom'):
                zoom_level = region.view2d.zoom[0] * 100

            vp_state = registry.register_viewport(region, zoom_level=zoom_level)
            viewport_id = vp_state.viewport_id

            # Update viewport header with GPU status + latency
            latency_ms = conn.get_current_latency()
            if conn.server_status and "gpu_name" in conn.server_status:
                gpu_name = conn.server_status["gpu_name"]
                vp_info = f" [VP{vp_state.priority}]" if registry.get_viewport_count() > 1 else ""
                stats_text = f"{gpu_name} | Latency: {latency_ms:.0f}ms{vp_info}"
                self.update_stats("", stats_text)
            else:
                stats_text = f"Latency: {latency_ms:.0f}ms"
                self.update_stats("", stats_text)

            # Check if camera moved (send fast camera update)
            view_mat, proj_mat, resolution = get_view_matrices(context)
            if view_mat != vp_state.last_camera_matrix:
                vp_state.last_camera_matrix = view_mat
                conn.send_viewport_camera(view_mat, proj_mat, resolution, viewport_id=viewport_id)

            # Check if timeline frame or playback state changed
            current_frame = get_current_frame(context)
            is_playing = context.scene.is_playing
            if (current_frame != vp_state.last_frame_number or
                is_playing != vp_state.last_is_playing):
                vp_state.last_frame_number = current_frame
                vp_state.last_is_playing = is_playing
                conn.send_timeline_update(current_frame, is_playing)

            # Get latest frame for this viewport
            frame = conn.get_frame(viewport_id=viewport_id)
            if frame is not None:
                frame_data, meta = frame

                # Phase 11 — Check if frame is aged (stale frame detection)
                from shared.constants import FRAME_STALE_THRESHOLD_MS
                timestamp_sent = meta.get("timestamp_sent")
                frame_format = meta.get("format", "JPEG")  # Issue #6: Get format from metadata

                if timestamp_sent is not None:
                    latency_ms = (time.time() - timestamp_sent) * 1000
                    if latency_ms > FRAME_STALE_THRESHOLD_MS:
                        logger.debug(f"Skipping stale frame (latency {latency_ms:.0f}ms > {FRAME_STALE_THRESHOLD_MS}ms)")
                        # Draw cached frame instead
                        if viewport_id in RemoteRenderEngine._textures:
                            self._draw_cached_frame(context, viewport_id)
                        else:
                            self._draw_placeholder(context, "Waiting for render...")
                        # Issue #14: Check if connection is valid before tag_redraw
                        if conn and conn.connected:
                            self.tag_redraw()
                        return

                try:
                    # Issue #3, #5: Decode based on format metadata
                    if frame_format == "EXR":
                        pixels, width, height, _ = decode_exr(frame_data)
                    else:  # Default: JPEG
                        pixels, width, height = decode_jpeg(frame_data)
                    if pixels is not None and width > 0 and height > 0:
                        self._draw_frame(context, pixels, width, height, viewport_id)
                        # Issue #10: Log frame latency correctly using timestamp_sent
                        if timestamp_sent is not None and int(latency_ms * 10) % 60 == 0:
                            vp_queue = conn.get_viewport_queue(viewport_id)
                            metrics = vp_queue.get_metrics() if vp_queue else {}
                            logger.debug(f"Frame latency: {latency_ms:.1f}ms, viewport {viewport_id}: {metrics}")
                except Exception as e:
                    logger.error(f"Frame decode error ({frame_format}): {e}", exc_info=e)
                    show_warning("ENCODE_001", str(e))
            else:
                # No new frame yet — draw previous or placeholder
                if viewport_id in RemoteRenderEngine._textures:
                    self._draw_cached_frame(context, viewport_id)
                else:
                    self._draw_placeholder(context, "Waiting for render...")

            # Keep requesting redraws for continuous updates
            self.tag_redraw()

        except Exception as e:
            logger.error(f"view_draw error: {e}", exc_info=e)

    def _draw_frame(self, context, pixels, width, height, viewport_id: str | None = None):
        """Draw a rendered frame to the viewport using GPU module.

        pixels: numpy array shaped (height, width, 4) with float32 RGBA [0, 1]
        viewport_id: Phase 12 — for multi-viewport caching
        """
        region = context.region

        # Issue #3: Validate width and height
        if width <= 0 or height <= 0:
            logger.error(f"Invalid frame dimensions: {width}x{height}")
            return

        try:
            # Issue #4: Validate pixel reshape before GPU operations
            expected_pixel_count = height * width * 4
            actual_pixel_count = pixels.size
            if actual_pixel_count != expected_pixel_count:
                logger.error(f"Pixel array size mismatch: expected {expected_pixel_count}, got {actual_pixel_count} ({height}x{width}x4)")
                return

            # Flatten pixels for GPU buffer
            pixels_flat = pixels.astype("float32").flatten()
            pixel_buf = gpu.types.Buffer("FLOAT", len(pixels_flat), pixels_flat)
            texture = gpu.types.GPUTexture((width, height), format="RGBA32F", data=pixel_buf)

            # Cache for re-display (Phase 12 — per-viewport)
            if viewport_id:
                RemoteRenderEngine._textures[viewport_id] = texture
            else:
                self._last_texture = texture
                self._last_texture_size = (width, height)

            # Draw fullscreen quad
            gpu.state.blend_set("ALPHA")
            # Issue #7: Fallback if draw_texture_2d is not available
            if draw_texture_2d is not None:
                draw_texture_2d(texture, (0, 0), region.width, region.height)
            else:
                logger.warning("gpu_extras not available, skipping viewport texture draw")
            gpu.state.blend_set("NONE")
        except Exception as e:
            logger.error(f"GPU draw error: {e}", exc_info=e)
            show_warning("PERF_003", "Viewport rendering too slow")

    def _draw_cached_frame(self, context, viewport_id: str | None = None):
        """Re-draw the last received frame.

        Phase 12 — supports per-viewport caching.
        """
        region = context.region

        # Issue #2: Check if region is None and return early
        if region is None:
            return

        # Get texture for this viewport or fallback to legacy
        if viewport_id and viewport_id in RemoteRenderEngine._textures:
            texture = RemoteRenderEngine._textures[viewport_id]
        elif self._last_texture is not None:
            texture = self._last_texture
        else:
            return

        gpu.state.blend_set("ALPHA")
        # Issue #7: Fallback if draw_texture_2d is not available
        if draw_texture_2d is not None:
            draw_texture_2d(texture, (0, 0), region.width, region.height)
        else:
            logger.warning("gpu_extras not available, skipping cached frame draw")
        gpu.state.blend_set("NONE")

    def _draw_placeholder(self, context, message: str):
        """Draw a dark placeholder with a text message."""
        # Just draw a dark rectangle — text rendering in viewport requires more work
        # For now, the Blender header bar shows the engine name
        pass

    # --- Cleanup ---

    def __del__(self):
        """Clean up GPU resources."""
        self._last_texture = None
