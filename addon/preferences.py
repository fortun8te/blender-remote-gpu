"""UI panel for the Remote GPU addon. Shows connection status, IP/Port settings."""

import bpy
from bpy.props import StringProperty, IntProperty


class RemoteGPUPreferences(bpy.types.AddonPreferences):
    bl_idname = "addon"

    server_ip: StringProperty(
        name="Server IP",
        description="IP address of the remote GPU server (Tailscale or LAN)",
        default="100.74.135.83",
    )
    server_port: IntProperty(
        name="Port",
        description="WebSocket port",
        default=9876,
        min=1024,
        max=65535,
    )
    connection_status: StringProperty(
        name="Status",
        default="Not connected",
    )

    def draw(self, context):
        layout = self.layout

        # Header
        layout.label(text="Remote GPU Server Configuration", icon="URL")

        # Server config box
        box = layout.box()
        box.label(text="Server Address")
        row = box.row(align=True)
        row.prop(self, "server_ip", text="IP")
        row.prop(self, "server_port", text="Port")

        # Connection status box
        status_box = layout.box()
        status_box.label(text="Connection Status")

        # Get current connection state
        from . import engine
        conn = engine.RemoteRenderEngine._connection
        is_connected = conn and conn.connected if conn else False

        # Status display
        row = status_box.row(align=True)
        if is_connected:
            row.label(text="✓ CONNECTED", icon="CHECKMARK")
            if hasattr(conn, 'gpu_name') and conn.gpu_name:
                status_box.label(text=f"GPU: {conn.gpu_name}")
            if hasattr(conn, 'vram_free') and conn.vram_free and conn.vram_free > 0:
                status_box.label(text=f"VRAM: {conn.vram_free} MB available")
        else:
            row.label(text="✗ NOT CONNECTED", icon="ERROR")
            if conn and hasattr(conn, 'error') and conn.error:
                status_box.label(text=f"Error: {conn.error}", icon="ERROR")

        # Action buttons
        col = status_box.column(align=True)
        if is_connected:
            col.operator("remotegpu.disconnect", text="Disconnect", icon="CANCEL")
        else:
            col.operator("remotegpu.connect", text="Connect to Server", icon="PLAY")
            col.operator("remotegpu.test_connection", text="Test Connection", icon="FILE_REFRESH")


class REMOTEGPU_PT_panel(bpy.types.Panel):
    bl_label = "Remote GPU"
    bl_idname = "REMOTEGPU_PT_panel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "render"

    @classmethod
    def poll(cls, context):
        return context.engine == "REMOTE_GPU"

    def draw(self, context):
        layout = self.layout

        try:
            addon = context.preferences.addons.get("addon")
            if not addon or not addon.preferences:
                layout.label(text="Enable addon in preferences first", icon="ERROR")
                return
            prefs = addon.preferences
        except Exception:
            layout.label(text="Preferences error", icon="ERROR")
            return

        # Server settings
        box = layout.box()
        box.label(text="Server", icon="URL")
        row = box.row(align=True)
        row.prop(prefs, "server_ip", text="IP")
        row.prop(prefs, "server_port", text="Port")

        # Connection status + buttons
        from . import engine
        conn = engine.RemoteRenderEngine._connection

        if conn and conn.connected:
            row = box.row()
            row.operator("remotegpu.disconnect", text="Disconnect", icon="CANCEL")

            # Status
            status_box = layout.box()
            status_box.label(text="Connected", icon="CHECKMARK")
            col = status_box.column(align=True)
            col.label(text=f"GPU: {conn.gpu_name}")
            if conn.vram_free > 0:
                col.label(text=f"VRAM Free: {conn.vram_free} MB")
        else:
            col = box.column()
            col.scale_y = 1.5
            col.operator("remotegpu.connect", text="Connect to Server", icon="URL")

            if conn and conn.error:
                row = layout.row()
                row.label(text=f"Error: {conn.error}", icon="ERROR")
            else:
                row = layout.row()
                row.label(text="Not connected", icon="INFO")
