"""Operators for connecting/disconnecting and managing the render server."""

import bpy
import time
from . import engine


class REMOTEGPU_OT_connect(bpy.types.Operator):
    bl_idname = "remotegpu.connect"
    bl_label = "Connect"
    bl_description = "Connect to the remote GPU render server"

    def execute(self, context):
        try:
            prefs = context.preferences.addons.get("addon")
            if not prefs:
                self.report({"ERROR"}, "Addon preferences not found")
                return {"CANCELLED"}
            prefs = prefs.preferences
        except Exception as e:
            self.report({"ERROR"}, f"Preferences error: {e}")
            return {"CANCELLED"}

        # Close any existing connection
        if engine.RemoteRenderEngine._connection is not None:
            engine.RemoteRenderEngine._connection.close()
            engine.RemoteRenderEngine._connection = None

        # Connect
        from .connection import Connection
        url = f"ws://{prefs.server_ip}:{prefs.server_port}"
        conn = Connection(url)
        conn.connect()

        if conn.connected:
            engine.RemoteRenderEngine._connection = conn

            # UI/UX Improvement #4: Remember last good connection
            prefs.last_good_ip = prefs.server_ip
            prefs.last_good_port = prefs.server_port
            prefs.last_test_status = "Last connection: OK"

            self.report({"INFO"}, f"Connected -- {conn.gpu_name} ({conn.latency_ms}ms)")
        else:
            error = conn.error or "Unknown error"
            conn.close()
            self.report({"ERROR"}, f"Connection failed: {error}")

        return {"FINISHED"}


class REMOTEGPU_OT_disconnect(bpy.types.Operator):
    bl_idname = "remotegpu.disconnect"
    bl_label = "Disconnect"
    bl_description = "Disconnect from the render server"

    def execute(self, context):
        conn = engine.RemoteRenderEngine._connection
        if conn:
            conn.close()
            engine.RemoteRenderEngine._connection = None
            self.report({"INFO"}, "Disconnected")
        return {"FINISHED"}


class REMOTEGPU_OT_quick_test(bpy.types.Operator):
    """UI/UX Improvement #4: Test connection with quick handshake."""
    bl_idname = "remotegpu.quick_test"
    bl_label = "Test Connection"
    bl_description = "Run a quick handshake test without fully connecting"

    def execute(self, context):
        try:
            prefs = context.preferences.addons.get("addon")
            if not prefs:
                self.report({"ERROR"}, "Addon preferences not found")
                return {"CANCELLED"}
            prefs = prefs.preferences
        except Exception as e:
            self.report({"ERROR"}, f"Preferences error: {e}")
            return {"CANCELLED"}

        # Create temporary connection just for test
        from .connection import Connection
        url = f"ws://{prefs.server_ip}:{prefs.server_port}"
        test_conn = Connection(url)
        test_conn.connect()

        if test_conn.connected:
            # Success
            prefs.last_test_status = f"✓ Connection OK ({test_conn.latency_ms}ms, {test_conn.gpu_name})"
            self.report({"INFO"}, f"Connection test passed - {test_conn.gpu_name}, {test_conn.latency_ms}ms latency")
            test_conn.close()
        else:
            # Failed
            error = test_conn.error or "Unknown error"
            prefs.last_test_status = f"✗ Connection failed: {error}"
            self.report({"ERROR"}, f"Connection test failed: {error}")
            test_conn.close()

        return {"FINISHED"}


class REMOTEGPU_OT_copy_address(bpy.types.Operator):
    """UI/UX Improvement #1: Copy connection address to clipboard for debugging."""
    bl_idname = "remotegpu.copy_address"
    bl_label = "Copy Address"
    bl_description = "Copy server address to clipboard for debugging"

    def execute(self, context):
        try:
            prefs = context.preferences.addons.get("addon")
            if not prefs:
                return {"CANCELLED"}
            prefs = prefs.preferences
            address = f"ws://{prefs.server_ip}:{prefs.server_port}"
            context.window_manager.clipboard = address
            self.report({"INFO"}, f"Copied to clipboard: {address}")
        except Exception as e:
            self.report({"ERROR"}, f"Failed to copy: {e}")
        return {"FINISHED"}


class REMOTEGPU_OT_cancel_render(bpy.types.Operator):
    """UI/UX Improvement #3: Cancel an in-progress render."""
    bl_idname = "remotegpu.cancel_render"
    bl_label = "Cancel Render"
    bl_description = "Cancel the current render on the remote server"

    def execute(self, context):
        conn = engine.RemoteRenderEngine._connection
        if conn and conn.connected:
            from .connection import Connection
            # Send cancel message
            conn.send_json({"type": "render_cancel"})
            self.report({"INFO"}, "Render cancel requested")

            # Clear progress data
            if hasattr(engine, '_render_progress_data'):
                engine._render_progress_data = None
        else:
            self.report({"ERROR"}, "Not connected to server")
        return {"FINISHED"}
