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


class REMOTEGPU_PT_render_panel(bpy.types.Panel):
    """Panel in Render Properties when Remote GPU engine is selected."""
    bl_label = "Remote GPU"
    bl_idname = "REMOTEGPU_PT_render_panel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "render"
    COMPAT_ENGINES = {'REMOTE_GPU'}

    @classmethod
    def poll(cls, context):
        return context.engine in cls.COMPAT_ENGINES

    def draw(self, context):
        layout = self.layout

        # Get preferences
        addon = context.preferences.addons.get(__package__)
        if not addon:
            layout.label(text="Enable addon in preferences", icon="ERROR")
            return
        prefs = addon.preferences

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
            status_box = layout.box()
            row = status_box.row()
            row.label(text=f"CONNECTED — {conn.gpu_name}", icon="CHECKMARK")
            if conn.vram_free > 0:
                status_box.label(text=f"VRAM: {conn.vram_free} MB free")
            status_box.label(text=f"Latency: {conn.latency_ms}ms ({conn.method})")
            status_box.operator("remotegpu.disconnect", text="Disconnect", icon="CANCEL")
        else:
            status_box = layout.box()
            row = status_box.row()
            row.label(text="NOT CONNECTED", icon="ERROR")
            if conn and conn.error:
                status_box.label(text=f"Error: {conn.error}")
            col = status_box.column(align=True)
            col.scale_y = 1.5
            col.operator("remotegpu.connect", text="Connect to Server", icon="URL")
            col.operator("remotegpu.test_connection", text="Test Connection", icon="FILE_REFRESH")
