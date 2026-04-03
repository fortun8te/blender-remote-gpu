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

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "server_ip")
        layout.prop(self, "server_port")


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

        # Show version info
        try:
            from . import BUILD, BUILD_DATE
            version_box = layout.box()
            version_box.label(text=f"Remote GPU {BUILD} ({BUILD_DATE})", icon="INFO")
        except ImportError:
            pass

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
