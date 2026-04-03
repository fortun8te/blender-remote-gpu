"""Cycles renderer — manages headless Blender rendering on the GPU server.

Two modes:
1. Subprocess mode: launches `blender -b` for each render (simpler, more reliable)
2. BPY module mode: uses bpy directly (faster for viewport, requires bpy as Python module)

We start with subprocess mode for final renders and will add bpy mode for viewport.
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import time
import json

logger = logging.getLogger("remote-gpu.renderer")

# Animation render timeout: max 1 hour for full animation sequences
ANIMATION_RENDER_TIMEOUT = 3600


def _get_gpu_backend_manager():
    """Lazily import and instantiate GPU backend manager."""
    try:
        from shared.gpu_backend import GPUBackendManager
        return GPUBackendManager()
    except ImportError:
        logger.warning("GPU backend manager not available, using default device selection")
        return None


class SubprocessRenderer:
    """Renders using `blender -b` subprocess calls.

    Best for final renders — reliable, isolated, no state issues.
    Supports multi-GPU backend selection (OptiX, CUDA, HIP, Vulkan).
    """

    def __init__(self, blender_path: str = "blender"):
        self.blender_path = blender_path
        self.work_dir = os.path.join(tempfile.gettempdir(), "remote_gpu_renders")
        os.makedirs(self.work_dir, exist_ok=True)
        self._current_process: subprocess.Popen | None = None
        self.gpu_backend_mgr = _get_gpu_backend_manager()

    async def render_final(
        self,
        blend_path: str,
        resolution: tuple[int, int],
        samples: int,
        output_format: str = "PNG",
        on_progress: callable = None,
        denoiser_type: str = "OFF",
        denoiser_intensity: float = 0.8,
        auto_aov_creation: bool = True,
    ) -> bytes | None:
        """Render a final frame using Blender subprocess with optional OptiX/OIDN denoising.

        Args:
            blend_path: Path to .blend file
            resolution: (width, height) tuple
            samples: Number of render samples
            output_format: PNG, JPEG, or EXR
            on_progress: Optional callback for progress updates
            denoiser_type: "OFF", "OIDN", or "OPTIX"
            denoiser_intensity: 0.0-1.0 denoising strength
            auto_aov_creation: Automatically create Normal/Albedo AOVs for denoising

        Returns the rendered image as bytes, or None on failure.
        Has timeout protection.
        """
        # Validate inputs
        if not os.path.exists(blend_path):
            logger.error(f"Blend file not found: {blend_path}")
            return None

        if resolution[0] < 1 or resolution[1] < 1:
            logger.error(f"Invalid resolution: {resolution}")
            return None

        if samples < 1:
            logger.error(f"Invalid samples: {samples}")
            return None

        output_path = os.path.join(self.work_dir, f"render_{int(time.time())}")

        # Build Blender command
        script = self._build_render_script(
            resolution, samples, output_format,
            denoiser_type, denoiser_intensity, auto_aov_creation
        )
        script_path = os.path.join(self.work_dir, "render_script.py")
        with open(script_path, "w") as f:
            f.write(script)

        ext = {"PNG": ".png", "JPEG": ".jpg", "EXR": ".exr", "OPEN_EXR": ".exr"}.get(output_format, ".png")
        output_file = output_path + ext

        # Windows path handling: use forward slashes or raw strings
        cmd = [
            self.blender_path,
            "-b", blend_path.replace("\\", "/"),        # Background mode, forward slashes
            "--python", script_path.replace("\\", "/"),  # Run config script
            "-o", output_path.replace("\\", "/") + "####",  # Output path pattern
            "-f", "1",                                   # Render frame 1
        ]

        logger.info(f"Starting render: {' '.join(cmd)}")
        start_time = time.time()
        timeout = 300  # Issue #6: 5 minute timeout (reduced from 10 minutes)

        # Run asynchronously with timeout
        try:
            process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=timeout,
            )
            self._current_process = process

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
            elapsed = time.time() - start_time
            self._current_process = None

        except asyncio.TimeoutError:
            logger.error(f"Render subprocess timeout: exceeded {timeout}s limit")
            if self._current_process:
                self._current_process.kill()
                self._current_process = None
            return None
        except Exception as e:
            logger.error(f"Render subprocess error: {e}")
            return None

        if process.returncode != 0:
            logger.error(f"Render failed (exit {process.returncode}):\n{stderr.decode()}")
            return None

        logger.info(f"Render complete in {elapsed:.1f}s")

        # Find the output file (Blender replaces #### with frame number)
        output_file = output_path + "0001" + ext
        if not os.path.exists(output_file):
            # Try without frame number padding
            for candidate in [output_path + ext, output_file]:
                if os.path.exists(candidate):
                    output_file = candidate
                    break
            else:
                logger.error(f"Output file not found: {output_file}")
                return None

        try:
            with open(output_file, "rb") as f:
                data = f.read()

            # Cleanup
            try:
                os.unlink(output_file)
            except OSError:
                pass

            return data

        except Exception as e:
            logger.error(f"Failed to read output file: {e}")
            return None

    def cancel(self):
        """Cancel the current render."""
        if self._current_process is not None:
            self._current_process.terminate()
            self._current_process = None
            logger.info("Render cancelled")


    async def render_animation(
        self,
        blend_path: str,
        resolution: tuple[int, int],
        samples: int,
        frame_start: int,
        frame_end: int,
        frame_step: int = 1,
        output_format: str = "EXR",
        on_progress: callable = None,
    ) -> list[tuple[bytes, dict]]:
        """Render an animation sequence, returning list of frames with metadata.

        Each frame is rendered at the specified frame_start to frame_end range.
        Returns list of (jpeg_bytes, metadata) tuples for animation playback.

        Supports very large animation sequences with a 1-hour timeout total.
        """
        # Validate inputs
        if not os.path.exists(blend_path):
            logger.error(f"Blend file not found: {blend_path}")
            return []

        if resolution[0] < 1 or resolution[1] < 1:
            logger.error(f"Invalid resolution: {resolution}")
            return []

        if samples < 1:
            logger.error(f"Invalid samples: {samples}")
            return []

        if frame_start > frame_end:
            logger.error(f"Invalid frame range: {frame_start}-{frame_end}")
            return []

        frame_count = (frame_end - frame_start) // frame_step + 1
        logger.info(f"Rendering animation: frames {frame_start}-{frame_end} (step {frame_step}) = {frame_count} frames")

        output_dir = os.path.join(self.work_dir, f"anim_{int(time.time())}")
        os.makedirs(output_dir, exist_ok=True)

        # Build animation render script
        script = self._build_animation_script(
            resolution, samples, output_format, frame_start, frame_end, frame_step, output_dir
        )
        script_path = os.path.join(self.work_dir, "anim_script.py")
        with open(script_path, "w") as f:
            f.write(script)

        # Windows path handling: use forward slashes
        cmd = [
            self.blender_path,
            "-b", blend_path.replace("\\", "/"),
            "--python", script_path.replace("\\", "/"),
        ]

        logger.info(f"Starting animation render: {' '.join(cmd)}")
        start_time = time.time()

        try:
            process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=ANIMATION_RENDER_TIMEOUT,
            )
            self._current_process = process

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=ANIMATION_RENDER_TIMEOUT,
            )
            elapsed = time.time() - start_time
            self._current_process = None

        except asyncio.TimeoutError:
            logger.error(f"Animation render timeout (>{ANIMATION_RENDER_TIMEOUT}s)")
            if self._current_process:
                self._current_process.kill()
                self._current_process = None
            return []
        except Exception as e:
            logger.error(f"Animation render subprocess error: {e}")
            return []

        if process.returncode != 0:
            logger.error(f"Animation render failed (exit {process.returncode}):\n{stderr.decode()}")
            return []

        logger.info(f"Animation render complete in {elapsed:.1f}s")

        # Collect all rendered frames
        results = []
        ext = {"PNG": ".png", "JPEG": ".jpg", "EXR": ".exr", "OPEN_EXR": ".exr"}.get(output_format, ".exr")

        frame_number = frame_start
        output_index = 1
        while frame_number <= frame_end:
            # Blender uses 4-digit frame padding
            frame_file = os.path.join(output_dir, f"frame_{output_index:04d}{ext}")

            if not os.path.exists(frame_file):
                logger.warning(f"Frame file not found: {frame_file}")
                frame_number += frame_step
                output_index += 1
                continue

            try:
                with open(frame_file, "rb") as f:
                    frame_data = f.read()

                metadata = {
                    "frame": frame_number,
                    "frame_start": frame_start,
                    "frame_end": frame_end,
                    "total_frames": frame_count,
                    "format": output_format,
                }

                results.append((frame_data, metadata))

                if on_progress:
                    on_progress(len(results), frame_count)

                # Cleanup individual frame
                try:
                    os.unlink(frame_file)
                except OSError:
                    pass

            except Exception as e:
                logger.error(f"Failed to read frame file {frame_file}: {e}")

            frame_number += frame_step
            output_index += 1

        # Cleanup output directory
        try:
            os.rmdir(output_dir)
        except OSError:
            pass

        logger.info(f"Animation render collected {len(results)} frames")
        return results

    def _build_animation_script(
        self,
        resolution: tuple[int, int],
        samples: int,
        output_format: str,
        frame_start: int,
        frame_end: int,
        frame_step: int,
        output_dir: str,
    ) -> str:
        """Generate a Python script that renders an animation sequence.

        Script will render each frame from frame_start to frame_end with specified step.
        """
        res_x = int(resolution[0])
        res_y = int(resolution[1])
        samples = int(samples)
        output_fmt = str(output_format).upper()
        frame_start_int = int(frame_start)
        frame_end_int = int(frame_end)
        frame_step_int = int(frame_step)
        output_dir_escaped = output_dir.replace("\\", "/")

        script = f"""
import bpy
import time

try:
    scene = bpy.context.scene
    render = scene.render
    cycles = scene.cycles

    # Resolution
    render.resolution_x = {res_x}
    render.resolution_y = {res_y}
    render.resolution_percentage = 100

    # Output format
    render.image_settings.file_format = {repr(output_fmt)}
    if {repr(output_fmt)} == 'PNG':
        render.image_settings.color_mode = 'RGBA'
    elif {repr(output_fmt)} in ('JPEG', 'JPG'):
        render.image_settings.quality = 90
    elif {repr(output_fmt)} in ('OPEN_EXR', 'OPEN_EXR_MULTILAYER'):
        # Enable multilayer EXR with all render passes
        render.image_settings.file_format = 'OPEN_EXR_MULTILAYER'
        render.image_settings.use_zbuffer = True
        render.image_settings.exr_codec = 'ZIP'  # DWA compression for bandwidth

    # Cycles settings
    cycles.device = 'GPU'
    cycles.samples = {samples}

    # Enable GPU devices
    prefs = bpy.context.preferences.addons.get('cycles')
    if prefs:
        cprefs = prefs.preferences
        for device_type in ('OPTIX', 'CUDA'):
            try:
                cprefs.compute_device_type = device_type
                cprefs.get_devices()
                enabled = 0
                for device in cprefs.devices:
                    device.use = True
                    enabled += 1
                print(f"Using {{device_type}} with {{enabled}} devices")
                break
            except Exception as e:
                print(f"GPU setup failed for {{device_type}}: {{e}}")
                continue

    # Render animation
    frame_num = {frame_start_int}
    output_index = 1
    while frame_num <= {frame_end_int}:
        scene.frame_set(frame_num)

        # Output with frame-padded filename
        output_path = f"{output_dir_escaped}/frame_{{output_index:04d}}"
        render.filepath = output_path

        print(f"Rendering frame {{frame_num}} (output {{output_index}})...")
        bpy.ops.render.render(write_still=True)

        frame_num += {frame_step_int}
        output_index += 1

    print("Animation render complete")

except Exception as e:
    import traceback
    print(f"ERROR in animation render script: {{e}}")
    traceback.print_exc()
    raise
"""
        return script


    def _build_render_script(
        self,
        resolution: tuple[int, int],
        samples: int,
        output_format: str,
        denoiser_type: str = "OFF",
        denoiser_intensity: float = 0.8,
        auto_aov_creation: bool = True,
    ) -> str:
        """Generate a Python script that configures render settings inside Blender.

        Supports OptiX/OIDN denoising with automatic AOV creation.
        Uses repr() to safely escape all values — prevents code injection.
        """
        # Validate and escape inputs
        res_x = int(resolution[0])
        res_y = int(resolution[1])
        samples = int(samples)
        output_fmt = str(output_format).upper()
        denoiser_type = str(denoiser_type).upper()
        denoiser_intensity = float(denoiser_intensity)
        auto_aov = bool(auto_aov_creation)

        # Build denoiser config string
        denoiser_config = ""
        if denoiser_type == "OPTIX":
            denoiser_config = f"""
    # OptiX Denoising (requires RTX GPU + AOVs)
    if auto_aov:
        # Create normal and albedo AOVs for OptiX denoising
        view_layer = scene.view_layers[0]
        try:
            if not view_layer.use_pass_normal:
                view_layer.use_pass_normal = True
            if not view_layer.use_pass_diffuse_color:
                view_layer.use_pass_diffuse_color = True
            print("OptiX AOVs created: normal, diffuse_color")
        except Exception as e:
            print(f"Warning: Could not create AOVs: {{e}}")

    # Apply OptiX denoiser
    render.use_denoising = True
    render.denoiser = 'OPTIX'
    render.denoise_use_gpu = True
    # Note: denoiser intensity is applied via compositor or per-layer in newer Blender versions
    print(f"OptiX denoising enabled (intensity={denoiser_intensity})")
"""
        elif denoiser_type == "OIDN":
            denoiser_config = f"""
    # OIDN Denoising (CPU-based, all GPUs)
    if auto_aov:
        # Create AOVs for OIDN
        view_layer = scene.view_layers[0]
        try:
            if not view_layer.use_pass_normal:
                view_layer.use_pass_normal = True
            print("OIDN AOVs created: normal")
        except Exception as e:
            print(f"Warning: Could not create AOVs: {{e}}")

    # Apply OIDN denoiser
    render.use_denoising = True
    try:
        render.denoiser = 'OPENIMAGEDENOISE'
    except:
        render.denoiser = 'OIDN'  # Fallback name
    print(f"OIDN denoising enabled (intensity={denoiser_intensity})")
"""

        # Safe string building
        script = f"""
import bpy
import time

try:
    scene = bpy.context.scene
    render = scene.render
    cycles = scene.cycles

    # AOV creation flag
    auto_aov = {auto_aov}

    # Resolution
    render.resolution_x = {res_x}
    render.resolution_y = {res_y}
    render.resolution_percentage = 100

    # Output format
    render.image_settings.file_format = {repr(output_fmt)}
    if {repr(output_fmt)} == 'PNG':
        render.image_settings.color_mode = 'RGBA'
    elif {repr(output_fmt)} in ('JPEG', 'JPG'):
        render.image_settings.quality = 90
    elif {repr(output_fmt)} in ('OPEN_EXR', 'OPEN_EXR_MULTILAYER'):
        # Enable multilayer EXR with all render passes
        render.image_settings.file_format = 'OPEN_EXR_MULTILAYER'
        render.image_settings.use_zbuffer = True
        render.image_settings.exr_codec = 'ZIP'  # DWA compression for bandwidth

    # Cycles settings
    cycles.device = 'GPU'
    cycles.samples = {samples}

    # Enable GPU devices
    prefs = bpy.context.preferences.addons.get('cycles')
    if prefs:
        cprefs = prefs.preferences
        # Try OptiX first (RTX), fall back to CUDA
        for device_type in ('OPTIX', 'CUDA'):
            try:
                cprefs.compute_device_type = device_type
                cprefs.get_devices()
                enabled = 0
                for device in cprefs.devices:
                    device.use = True
                    enabled += 1
                print(f"Using {{device_type}} with {{enabled}} devices")
                break
            except Exception as e:
                print(f"GPU setup failed for {{device_type}}: {{e}}")
                continue

    # Enable all render passes for EXR output
    view_layer = scene.view_layers[0]
    if {repr(output_fmt)} in ('OPEN_EXR', 'OPEN_EXR_MULTILAYER'):
        try:
            view_layer.use_pass_combined = True
            view_layer.use_pass_normal = True
            view_layer.use_pass_diffuse_color = True
            view_layer.use_pass_diffuse_direct = True
            view_layer.use_pass_diffuse_indirect = True
            view_layer.use_pass_glossy_direct = True
            view_layer.use_pass_glossy_indirect = True
            view_layer.use_pass_transmission_direct = True
            view_layer.use_pass_transmission_indirect = True
            view_layer.use_pass_emission = True
            view_layer.use_pass_ambient_occlusion = True
            view_layer.use_pass_z = True
            print("EXR render passes enabled: beauty, normal, depth, AO, diffuse, glossy, transmission, emission")
        except Exception as e:
            print(f"Warning: Could not enable all render passes: {{e}}")

    # Configure denoising
    {denoiser_config if denoiser_config else '    render.use_denoising = False  # Denoising disabled'}

    print(f"Render config: {{render.resolution_x}}x{{render.resolution_y}}, {{cycles.samples}} samples, {{cycles.device}}")

    # Start timer for denoising performance logging
    denoise_start = time.time()

except Exception as e:
    import traceback
    print(f"ERROR in render script: {{e}}")
    traceback.print_exc()
    raise
"""
        return script


class ViewportRenderer:
    """Renders progressive viewport frames using bpy module or subprocess.

    For viewport, we need fast iteration:
    1. Receive camera update
    2. Render 1 sample
    3. Encode & send JPEG
    4. Render more samples progressively
    5. On new camera: reset and restart
    """

    def __init__(self, blender_path: str = "blender"):
        self.blender_path = blender_path
        self.work_dir = os.path.join(tempfile.gettempdir(), "remote_gpu_viewport")
        os.makedirs(self.work_dir, exist_ok=True)
        self._cancelled = False
        self._current_process: subprocess.Popen | None = None

    async def render_viewport_frame(
        self,
        blend_path: str,
        view_matrix: list,
        proj_matrix: list,
        resolution: tuple[int, int],
        samples: int = 16,
        quality: int = 75,
    ) -> bytes | None:
        """Render a single viewport frame.

        For progressive rendering, call this repeatedly with increasing samples.
        Returns JPEG bytes or None on failure.
        """
        self._cancelled = False

        script = self._build_viewport_script(view_matrix, proj_matrix, resolution, samples, quality)
        script_path = os.path.join(self.work_dir, "viewport_script.py")
        with open(script_path, "w") as f:
            f.write(script)

        output_file = os.path.join(self.work_dir, "viewport.jpg")

        cmd = [
            self.blender_path,
            "-b", blend_path,
            "--python", script_path,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._current_process = process

        stdout, stderr = await process.communicate()
        self._current_process = None

        if self._cancelled or process.returncode != 0:
            return None

        if not os.path.exists(output_file):
            logger.error(f"Viewport output not found: {output_file}")
            return None

        with open(output_file, "rb") as f:
            data = f.read()

        return data

    def cancel(self):
        """Cancel current viewport render."""
        self._cancelled = True
        if self._current_process is not None:
            self._current_process.terminate()
            self._current_process = None

    def _build_viewport_script(
        self,
        view_matrix: list,
        proj_matrix: list,
        resolution: tuple[int, int],
        samples: int,
        quality: int,
    ) -> str:
        """Generate script for viewport rendering."""
        output_file = os.path.join(self.work_dir, "viewport.jpg").replace("\\", "/")
        return f"""
import bpy
import mathutils
import os

scene = bpy.context.scene
render = scene.render
cycles = scene.cycles

# Resolution
render.resolution_x = {resolution[0]}
render.resolution_y = {resolution[1]}
render.resolution_percentage = 100

# Low samples for fast preview
cycles.device = 'GPU'
cycles.samples = {samples}
cycles.use_preview_denoising = True

# Output as JPEG
render.image_settings.file_format = 'JPEG'
render.image_settings.quality = {quality}

# Set up camera from view matrix
view_mat = {view_matrix}
mat = mathutils.Matrix([
    view_mat[0:4],
    view_mat[4:8],
    view_mat[8:12],
    view_mat[12:16],
])
cam_matrix = mat.inverted()

cam_name = "__remote_viewport_cam"
if cam_name not in bpy.data.objects:
    cam_data = bpy.data.cameras.new(cam_name)
    cam_obj = bpy.data.objects.new(cam_name, cam_data)
    scene.collection.objects.link(cam_obj)
else:
    cam_obj = bpy.data.objects[cam_name]

cam_obj.matrix_world = cam_matrix
scene.camera = cam_obj

# Enable GPU
prefs = bpy.context.preferences.addons.get('cycles')
if prefs:
    cprefs = prefs.preferences
    for device_type in ('OPTIX', 'CUDA'):
        try:
            cprefs.compute_device_type = device_type
            cprefs.get_devices()
            for device in cprefs.devices:
                device.use = True
            break
        except Exception:
            continue

# Render
render.filepath = '{output_file}'
bpy.ops.render.render(write_still=True)
print("VIEWPORT_RENDER_DONE")
"""
