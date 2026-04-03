"""Operators for connecting/disconnecting to the render server."""

import bpy
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
        url = f"http://{prefs.server_ip}:{prefs.server_port}"
        conn = Connection(url)
        conn.connect()

        if conn.connected:
            engine.RemoteRenderEngine._connection = conn
            self.report({"INFO"}, f"Connected — {conn.gpu_name}")
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


class REMOTEGPU_OT_test_connection(bpy.types.Operator):
    bl_idname = "remotegpu.test_connection"
    bl_label = "Test Connection"
    bl_description = "Test if the server is reachable"

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

        # Test connection without storing it
        from .connection import Connection
        url = f"http://{prefs.server_ip}:{prefs.server_port}"
        test_conn = Connection(url)

        print(f"[RemoteGPU] Testing connection to {url}...")
        test_conn.connect()

        if test_conn.connected:
            gpu_info = f" — GPU: {test_conn.gpu_name}" if test_conn.gpu_name else ""
            self.report({"INFO"}, f"✓ Server is reachable{gpu_info}")
            print(f"[RemoteGPU] ✓ Connection successful{gpu_info}")
            test_conn.close()
        else:
            error = test_conn.error or "Unknown error — server may not be running"
            self.report({"ERROR"}, f"✗ Cannot reach server: {error}")
            print(f"[RemoteGPU] ✗ Connection failed: {error}")
            test_conn.close()

        return {"FINISHED"}
