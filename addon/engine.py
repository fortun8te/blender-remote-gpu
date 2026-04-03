"""Minimal render engine that delegates rendering to the remote GPU server."""

import bpy
import numpy as np
import tempfile
import os
import time

# Try to import gpu_extras for viewport drawing
try:
    import gpu
    from gpu_extras.presets import draw_texture_2d
    HAS_GPU = True
except ImportError:
    HAS_GPU = False


class RemoteRenderEngine(bpy.types.RenderEngine):
    bl_idname = "REMOTE_GPU"
    bl_label = "Remote GPU"
    bl_use_preview = False

    # Shared connection (class-level, all instances share it)
    _connection = None

    # UI/UX Improvement #3: Render progress tracking
    _render_progress_data = None
    _render_start_time = None

    def render(self, depsgraph):
        """Final render (F12). Send scene to server, wait for result."""
        conn = RemoteRenderEngine._connection
        if not conn or not conn.connected:
            self.report({"ERROR"}, "Not connected to render server")
            return

        scene = depsgraph.scene
        width = int(scene.render.resolution_x * scene.render.resolution_percentage / 100)
        height = int(scene.render.resolution_y * scene.render.resolution_percentage / 100)
        samples = scene.cycles.samples if hasattr(scene, "cycles") else 128

        # Initialize render progress tracking (Improvement #3)
        RemoteRenderEngine._render_progress_data = {
            "active": True,
            "status": "Uploading scene...",
            "progress": 0,
        }
        RemoteRenderEngine._render_start_time = time.time()

        try:
            # Save current .blend to temp
            blend_path = os.path.join(tempfile.gettempdir(), "remote_render_scene.blend")
            bpy.ops.wm.save_as_mainfile(filepath=blend_path, copy=True)

            # Upload scene
            with open(blend_path, "rb") as f:
                blend_data = f.read()

            RemoteRenderEngine._render_progress_data["status"] = "Uploading scene..."
            self.update_stats("", "Uploading scene...")

            conn.send_json({
                "type": "scene_upload",
                "filename": "scene.blend",
                "size": len(blend_data),
            })
            conn.send_binary(blend_data)

            # Wait for scene_ack
            RemoteRenderEngine._render_progress_data["status"] = "Waiting for server acknowledgement..."
            self.update_stats("", "Waiting for server...")

            ack = self._wait_for_message("scene_ack", timeout=30.0)
            if not ack:
                self.report({"ERROR"}, "Server did not acknowledge scene upload")
                RemoteRenderEngine._render_progress_data["active"] = False
                return

            # Start render
            RemoteRenderEngine._render_progress_data["status"] = "Rendering on remote GPU..."
            RemoteRenderEngine._render_progress_data["samples_done"] = 0
            RemoteRenderEngine._render_progress_data["samples_total"] = samples

            conn.send_json({
                "type": "render_start",
                "width": width,
                "height": height,
                "samples": samples,
            })

            # Wait for frame result while updating progress
            self.update_stats("", "Rendering on remote GPU...")

            frame_meta = self._wait_for_message("frame", timeout=300.0)
            if not frame_meta:
                self.report({"ERROR"}, "Render timed out or failed")
                RemoteRenderEngine._render_progress_data["active"] = False
                return

            # Update progress: receiving result
            RemoteRenderEngine._render_progress_data["status"] = "Receiving render result..."
            RemoteRenderEngine._render_progress_data["progress"] = 90

            # Get the binary image data
            image_data = self._wait_for_binary(timeout=30.0)
            if not image_data:
                self.report({"ERROR"}, "Did not receive render result")
                RemoteRenderEngine._render_progress_data["active"] = False
                return

            # Decode the image and write to Blender's render result
            try:
                from PIL import Image
                import io

                RemoteRenderEngine._render_progress_data["status"] = "Processing image..."
                RemoteRenderEngine._render_progress_data["progress"] = 95

                img = Image.open(io.BytesIO(image_data))
                img = img.convert("RGBA")
                pixels = np.array(img, dtype=np.float32) / 255.0
                pixels = np.flipud(pixels)  # Blender expects bottom-up

                result = self.begin_result(0, 0, width, height)
                layer = result.layers[0].passes["Combined"]
                layer.rect = pixels.reshape(-1, 4).tolist()
                self.end_result(result)

                # Update final status
                elapsed = time.time() - RemoteRenderEngine._render_start_time
                elapsed_str = self._format_time(elapsed)
                RemoteRenderEngine._render_progress_data["status"] = f"Render complete ({elapsed_str})"
                RemoteRenderEngine._render_progress_data["progress"] = 100

                self.update_stats("", f"Render complete ({elapsed_str})")

            except Exception as e:
                self.report({"ERROR"}, f"Failed to decode render: {e}")
                RemoteRenderEngine._render_progress_data["active"] = False

        finally:
            # Mark render as complete after a delay (allow UI to show completion)
            # In a real implementation, this would be handled by a timer
            pass

    def view_update(self, context, depsgraph):
        """Called when the scene changes in viewport."""
        pass  # Phase 3: send scene updates

    def view_draw(self, context, depsgraph):
        """Called to draw the viewport. Phase 3: display streamed frames."""
        pass  # Phase 3: draw received frames

    def _wait_for_message(self, msg_type, timeout=30.0):
        """Block until a specific message type is received."""
        conn = RemoteRenderEngine._connection
        if not conn:
            return None

        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = conn.recv()
            if msg is None:
                time.sleep(0.05)
                continue
            if msg.get("type") == msg_type:
                return msg
            if msg.get("type") == "error":
                error_msg = msg.get("message", "Unknown error")
                self.report({"ERROR"}, f"Server error: {error_msg}")
                print(f"[Engine] Server reported error: {error_msg}")

                RemoteRenderEngine._render_progress_data["active"] = False
                RemoteRenderEngine._render_progress_data["status"] = f"Error: {error_msg}"
                return None
            if msg.get("type") == "progress":
                # UI/UX Improvement #3: Update progress display
                done = msg.get("samples_done", 0)
                total = msg.get("samples_total", 0)
                elapsed = time.time() - RemoteRenderEngine._render_start_time

                RemoteRenderEngine._render_progress_data["samples_done"] = done
                RemoteRenderEngine._render_progress_data["samples_total"] = total
                RemoteRenderEngine._render_progress_data["elapsed_time"] = self._format_time(elapsed)

                if total > 0:
                    progress_percent = int((done / total) * 80)  # 0-80% for rendering
                    RemoteRenderEngine._render_progress_data["progress"] = progress_percent

                status_msg = f"Rendering: {done}/{total} samples"
                RemoteRenderEngine._render_progress_data["status"] = status_msg
                self.update_stats("", status_msg)

        RemoteRenderEngine._render_progress_data["active"] = False
        return None

    def _wait_for_binary(self, timeout=30.0):
        """Block until binary data is received."""
        conn = RemoteRenderEngine._connection
        if not conn:
            return None

        deadline = time.time() + timeout
        while time.time() < deadline:
            data = conn.recv_binary()
            if data is not None:
                return data
            time.sleep(0.05)
        return None

    @staticmethod
    def _format_time(seconds):
        """Format seconds as human-readable time (e.g., '2m 34s')."""
        if seconds < 60:
            return f"{int(seconds)}s"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        if minutes < 60:
            return f"{minutes}m {secs}s"
        hours = int(minutes // 60)
        minutes = minutes % 60
        return f"{hours}h {minutes}m {secs}s"
