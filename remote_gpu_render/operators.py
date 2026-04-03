"""Operators for connecting/disconnecting to the render server."""

import bpy
import threading
import json
from . import engine

BEACON_PORT = 9875  # Must match server.py


def _get_prefs(context):
    """Get addon preferences using __package__ (not hardcoded 'addon')."""
    addon = context.preferences.addons.get(__package__)
    if addon:
        return addon.preferences
    # Fallback: try the parent package name
    parent = __package__.rsplit(".", 1)[0] if "." in __package__ else __package__
    addon = context.preferences.addons.get(parent)
    if addon:
        return addon.preferences
    return None


class REMOTEGPU_OT_auto_discover(bpy.types.Operator):
    """Listen for the Windows server beacon — plug in TB4 cable and click this."""
    bl_idname = "remotegpu.auto_discover"
    bl_label = "Auto-discover"
    bl_description = "Find the Windows render server automatically (Thunderbolt or LAN)"

    _timer = None
    _thread = None
    _result = None   # ("ip", "gpu_name") or None

    def execute(self, context):
        REMOTEGPU_OT_auto_discover._result = None

        def _listen():
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("", BEACON_PORT))
                sock.settimeout(6.0)   # wait up to 6 seconds for a beacon
                data, addr = sock.recvfrom(1024)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "remote_gpu_beacon":
                    REMOTEGPU_OT_auto_discover._result = (addr[0], msg.get("gpu", "GPU"))
            except Exception:
                pass
            finally:
                sock.close()

        REMOTEGPU_OT_auto_discover._thread = threading.Thread(target=_listen, daemon=True)
        REMOTEGPU_OT_auto_discover._thread.start()

        self.report({"INFO"}, "Searching... (make sure server.py is running on Windows)")
        self._timer = context.window_manager.event_timer_add(0.2, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if REMOTEGPU_OT_auto_discover._thread and REMOTEGPU_OT_auto_discover._thread.is_alive():
            return {"PASS_THROUGH"}

        context.window_manager.event_timer_remove(self._timer)
        result = REMOTEGPU_OT_auto_discover._result

        if result:
            ip, gpu = result
            prefs = _get_prefs(context)
            if prefs:
                prefs.server_ip = ip
            self.report({"INFO"}, f"Found: {gpu} at {ip} — click Connect")
        else:
            self.report({"WARNING"},
                "Nothing found — is server.py running on Windows? TB4 cable plugged in?")

        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


class REMOTEGPU_OT_connect(bpy.types.Operator):
    bl_idname = "remotegpu.connect"
    bl_label = "Connect"
    bl_description = "Connect to the remote GPU render server"

    _timer = None
    _thread = None
    _result = None

    def execute(self, context):
        prefs = _get_prefs(context)
        if not prefs:
            self.report({"ERROR"}, "Addon preferences not found — check addon is enabled")
            print(f"[RemoteGPU] ERROR: preferences not found. __package__={__package__}")
            return {"CANCELLED"}

        # Close existing
        if engine.RemoteRenderEngine._connection:
            engine.RemoteRenderEngine._connection.close()
            engine.RemoteRenderEngine._connection = None

        ip = prefs.server_ip
        port = prefs.server_port
        self.report({"INFO"}, f"Connecting to {ip}:{port}...")

        # Run connection in background thread
        from .connection import Connection
        conn = Connection(ip, port)

        def _connect():
            conn.connect()
            REMOTEGPU_OT_connect._result = conn

        REMOTEGPU_OT_connect._result = None
        REMOTEGPU_OT_connect._thread = threading.Thread(target=_connect, daemon=True)
        REMOTEGPU_OT_connect._thread.start()

        # Register timer to check when done
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        thread = REMOTEGPU_OT_connect._thread
        if thread and thread.is_alive():
            return {"PASS_THROUGH"}  # Still connecting

        # Done
        context.window_manager.event_timer_remove(self._timer)

        conn = REMOTEGPU_OT_connect._result
        if conn and conn.connected:
            engine.RemoteRenderEngine._connection = conn
            self.report({"INFO"}, f"Connected — {conn.gpu_name} via {conn.method}")
        else:
            error = conn.error if conn else "Unknown error"
            self.report({"ERROR"}, f"Connection failed: {error}")

        # Redraw UI
        for area in context.screen.areas:
            area.tag_redraw()

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

    _timer = None
    _thread = None
    _result = None

    def execute(self, context):
        prefs = _get_prefs(context)
        if not prefs:
            self.report({"ERROR"}, "Addon preferences not found — check addon is enabled")
            print(f"[RemoteGPU] ERROR: preferences not found. __package__={__package__}")
            return {"CANCELLED"}

        ip = prefs.server_ip
        port = prefs.server_port
        self.report({"INFO"}, f"Testing {ip}:{port}...")

        from .connection import Connection
        conn = Connection(ip, port)

        def _test():
            conn.connect()
            REMOTEGPU_OT_test_connection._result = conn

        REMOTEGPU_OT_test_connection._result = None
        REMOTEGPU_OT_test_connection._thread = threading.Thread(target=_test, daemon=True)
        REMOTEGPU_OT_test_connection._thread.start()

        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        thread = REMOTEGPU_OT_test_connection._thread
        if thread and thread.is_alive():
            return {"PASS_THROUGH"}

        context.window_manager.event_timer_remove(self._timer)

        conn = REMOTEGPU_OT_test_connection._result
        if conn and conn.connected:
            self.report({"INFO"},
                f"Server reachable — {conn.gpu_name}, {conn.vram_free}MB VRAM, "
                f"{conn.latency_ms}ms via {conn.method}")
        else:
            error = conn.error if conn else "Unknown error"
            self.report({"ERROR"}, f"Cannot reach server: {error}")

        if conn:
            conn.close()

        for area in context.screen.areas:
            area.tag_redraw()

        return {"FINISHED"}
