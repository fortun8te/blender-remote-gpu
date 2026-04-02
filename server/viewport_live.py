"""Live viewport rendering using bpy module (keeps Blender loaded in memory).

CRITICAL: This module ONLY works when the Windows server is run INSIDE Blender's Python.
It will NOT work if called from an external Python process.

If bpy import fails, the server will fall back to subprocess mode.
"""

import time
import logging

logger = logging.getLogger("remote-gpu.viewport-live")

try:
    import bpy
    HAS_BPY = True
except ImportError:
    HAS_BPY = False
    logger.warning("bpy not available — viewport rendering will use subprocess mode (slower)")


class LiveViewportRenderer:
    """Renders Cycles frames as fast as possible by keeping Blender loaded.

    Key optimization: Blender stays in memory, we just call render repeatedly.
    Latency: ~30-50ms per frame (vs 2000ms subprocess).
    """

    def __init__(self):
        self.scene = None
        self.cancelled = False

    def setup_scene(self, blend_path: str):
        """Load a .blend file into the running Blender instance."""
        if not HAS_BPY:
            raise RuntimeError("bpy not available — must run server inside Blender")

        logger.info(f"Loading scene: {blend_path}")
        try:
            bpy.ops.wm.open_mainfile(filepath=blend_path)
            self.scene = bpy.context.scene

            # Configure for GPU rendering
            self._setup_gpu()
            logger.info("Scene loaded and GPU configured")
        except Exception as e:
            logger.error(f"Failed to load scene: {e}")
            self.scene = None
            raise

    def _setup_gpu(self):
        """Configure Blender for RTX GPU rendering with OptiX."""
        if self.scene is None:
            return

        render = self.scene.render
        cycles = self.scene.cycles

        # Use GPU
        cycles.device = "GPU"

        # Enable all GPU devices (RTX 5090 = 1 device, but ready for multi-GPU)
        prefs = bpy.context.preferences.addons.get("cycles")
        if prefs:
            cprefs = prefs.preferences

            # Try OptiX first (best for RTX), fall back to CUDA
            for device_type in ("OPTIX", "CUDA"):
                try:
                    cprefs.compute_device_type = device_type
                    cprefs.get_devices()

                    enabled_count = 0
                    for device in cprefs.devices:
                        device.use = True
                        enabled_count += 1

                    if enabled_count > 0:
                        logger.info(f"GPU setup: {device_type} with {enabled_count} device(s)")
                        return
                except Exception as e:
                    logger.debug(f"GPU setup failed for {device_type}: {e}")
                    continue

            logger.warning("GPU setup failed — will use CPU (slow)")

    def set_camera_from_matrix(self, view_matrix: list, proj_matrix: list):
        """Update scene camera to match viewport."""
        import mathutils

        if self.scene is None:
            return

        # Reconstruct 4x4 view matrix
        mat = mathutils.Matrix([
            view_matrix[0:4],
            view_matrix[4:8],
            view_matrix[8:12],
            view_matrix[12:16],
        ])
        cam_matrix = mat.inverted()

        # Get or create render camera
        cam_name = "__remote_viewport_cam"
        if cam_name not in bpy.data.objects:
            cam_data = bpy.data.cameras.new(cam_name)
            cam_obj = bpy.data.objects.new(cam_name, cam_data)
            self.scene.collection.objects.link(cam_obj)
        else:
            cam_obj = bpy.data.objects[cam_name]

        cam_obj.matrix_world = cam_matrix
        self.scene.camera = cam_obj

    def set_current_frame(self, frame_current: int):
        """Set the current animation frame for rendering.

        This ensures viewport renders the correct frame from the timeline.
        """
        if self.scene is None:
            return
        self.scene.frame_set(frame_current)
        logger.debug(f"Frame set to {frame_current}")

    def set_resolution(self, resolution: tuple):
        """Set render resolution."""
        if self.scene is None:
            return
        self.scene.render.resolution_x = resolution[0]
        self.scene.render.resolution_y = resolution[1]
        self.scene.render.resolution_percentage = 100

    def render_frame(self, samples: int = 16) -> bytes | None:
        """Render a single frame and return as JPEG.

        Accumulates samples progressively.
        """
        if self.scene is None:
            logger.error("No scene loaded")
            return None

        start_time = time.time()

        try:
            # Configure samples
            self.scene.cycles.samples = samples

            # Render to buffer
            bpy.ops.render.render(write_still=False, use_viewport=False)

            # Get render result
            pixels = bpy.data.images["Render Result"].pixels[:]
            width = self.scene.render.resolution_x
            height = self.scene.render.resolution_y

            # Encode as JPEG
            from server.encoder import encode_jpeg
            import numpy as np

            pixels_np = np.array(pixels, dtype=np.float32)
            jpeg_data = encode_jpeg(pixels_np, width, height, quality=75)

            elapsed = time.time() - start_time
            logger.debug(f"Render {samples} samples: {elapsed:.2f}s -> {len(jpeg_data) / 1024:.0f} KB")

            return jpeg_data

        except Exception as e:
            logger.error(f"Render error: {e}", exc_info=True)
            return None

    def cancel(self):
        """Cancel current render (can't cancel in-flight, but set flag for next)."""
        self.cancelled = True
