"""Operators for connecting/disconnecting to the render server."""

import bpy
from . import engine


def _make_connection(ip, port):
    """Try HTTP first, fall back to raw socket."""
    # Try HTTP
    try:
        from .connection import Connection
        url = f"http://{ip}:{port}"
        print(f"[RemoteGPU] Trying HTTP: {url}")
        conn = Connection(url)
        conn.connect()
        if conn.connected:
            return conn
        print(f"[RemoteGPU] HTTP failed: {conn.error}")
    except Exception as e:
        print(f"[RemoteGPU] HTTP exception: {e}")

    # Fall back to raw TCP socket
    try:
        from .connection_socket import SocketConnection
        print(f"[RemoteGPU] Trying raw socket: {ip}:{port}")
        conn = SocketConnection(ip, port)
        conn.connect()
        if conn.connected:
            return conn
        print(f"[RemoteGPU] Socket failed: {conn.error}")
    except Exception as e:
        print(f"[RemoteGPU] Socket exception: {e}")

    return None


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

        # Connect (tries HTTP then socket)
        conn = _make_connection(prefs.server_ip, prefs.server_port)

        if conn and conn.connected:
            engine.RemoteRenderEngine._connection = conn
            self.report({"INFO"}, f"Connected — {conn.gpu_name}")
        else:
            self.report({"ERROR"}, "Cannot reach server — check IP/port and server is running")

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

        print(f"[RemoteGPU] Testing connection to {prefs.server_ip}:{prefs.server_port}...")

        conn = _make_connection(prefs.server_ip, prefs.server_port)

        if conn and conn.connected:
            gpu_info = f" — GPU: {conn.gpu_name}" if conn.gpu_name else ""
            self.report({"INFO"}, f"✓ Server reachable{gpu_info} ({conn.latency_ms}ms)")
            print(f"[RemoteGPU] ✓ Success{gpu_info}")
            conn.close()
        else:
            self.report({"ERROR"}, "✗ Cannot reach server — check IP/port")
            print(f"[RemoteGPU] ✗ Failed")

        return {"FINISHED"}
