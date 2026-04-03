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

        # Save current .blend to temp
        blend_path = os.path.join(tempfile.gettempdir(), "remote_render_scene.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend_path, copy=True)

        # Upload scene
        with open(blend_path, "rb") as f:
            blend_data = f.read()

        conn.send_json({
            "type": "scene_upload",
            "filename": "scene.blend",
            "size": len(blend_data),
        })
        conn.send_binary(blend_data)

        # Wait for scene_ack
        ack = self._wait_for_message("scene_ack", timeout=30.0)
        if not ack:
            self.report({"ERROR"}, "Server did not acknowledge scene upload")
            return

        # Start render
        conn.send_json({
            "type": "render_start",
            "width": width,
            "height": height,
            "samples": samples,
        })

        # Wait for frame result
        self.update_stats("", "Rendering on remote GPU...")

        frame_meta = self._wait_for_message("frame", timeout=300.0)
        if not frame_meta:
            self.report({"ERROR"}, "Render timed out or failed")
            return

        # Get the binary image data
        image_data = self._wait_for_binary(timeout=30.0)
        if not image_data:
            self.report({"ERROR"}, "Did not receive render result")
            return

        # Decode the image and write to Blender's render result
        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(image_data))
            img = img.convert("RGBA")
            pixels = np.array(img, dtype=np.float32) / 255.0
            pixels = np.flipud(pixels)  # Blender expects bottom-up

            result = self.begin_result(0, 0, width, height)
            layer = result.layers[0].passes["Combined"]
            layer.rect = pixels.reshape(-1, 4).tolist()
            self.end_result(result)

            self.update_stats("", "Render complete")
        except Exception as e:
            self.report({"ERROR"}, f"Failed to decode render: {e}")

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
                self.report({"ERROR"}, f"Server error: {msg.get('message', 'Unknown')}")
                return None
            if msg.get("type") == "progress":
                done = msg.get("samples_done", 0)
                total = msg.get("samples_total", 0)
                self.update_stats("", f"Rendering: {done}/{total} samples")
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
