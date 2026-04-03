"""UI preferences for the Remote GPU addon."""

import bpy
from bpy.props import StringProperty, IntProperty


class RemoteGPUPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__  # CRITICAL: must match package name, not hardcoded "addon"

    server_ip: StringProperty(
        name="Server IP",
        description="IP address of the remote GPU server (Tailscale or LAN)",
        default="100.74.135.83",
    )
    server_port: IntProperty(
        name="Port",
        description="Server port",
        default=9876,
        min=1024,
        max=65535,
    )

    def draw(self, context):
        layout = self.layout

        # Server config
        box = layout.box()
        box.label(text="Remote GPU Server", icon="URL")
        row = box.row(align=True)
        row.prop(self, "server_ip", text="IP")
        row.prop(self, "server_port", text="Port")

        # Connection status
        status_box = layout.box()
        from . import engine
        conn = engine.RemoteRenderEngine._connection

        if conn and conn.connected:
            row = status_box.row()
            row.label(text="CONNECTED", icon="CHECKMARK")
            if conn.gpu_name:
                status_box.label(text=f"GPU: {conn.gpu_name}")
            if conn.vram_free > 0:
                status_box.label(text=f"VRAM: {conn.vram_free} MB free")
            if conn.latency_ms > 0:
                status_box.label(text=f"Latency: {conn.latency_ms}ms")
            if conn.method:
                status_box.label(text=f"Method: {conn.method}")

            status_box.operator("remotegpu.disconnect", text="Disconnect", icon="CANCEL")
        else:
            row = status_box.row()
            row.label(text="NOT CONNECTED", icon="ERROR")
            if conn and conn.error:
                status_box.label(text=f"Error: {conn.error}")

            col = status_box.column(align=True)
            col.operator("remotegpu.connect", text="Connect", icon="PLAY")
            col.operator("remotegpu.test_connection", text="Test Connection", icon="FILE_REFRESH")
