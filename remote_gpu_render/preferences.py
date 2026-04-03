"""AddonPreferences for Remote GPU — Agent R3 dispatcher integration.

Panel classes have been moved to __init__.py to avoid Blender 5.0
metaclass registration issues.
"""

import bpy
from bpy.props import StringProperty, IntProperty


class RemoteGPUPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    server_ip: StringProperty(
        name="Dispatcher IP",
        description="IP address of the job dispatcher (Tailscale or LAN)",
        default="100.74.135.83",
    )
    server_port: IntProperty(
        name="Port",
        description="Dispatcher port",
        default=9876,
        min=1024,
        max=65535,
    )

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Job Dispatcher", icon="URL")
        row = box.row(align=True)
        row.prop(self, "server_ip", text="IP")
        row.prop(self, "server_port", text="Port")

        from . import engine
        dispatcher = engine.RemoteRenderEngine._dispatcher
        status_box = layout.box()

        if dispatcher is not None:
            status_box.label(text="CONNECTED", icon="CHECKMARK")
            if dispatcher.latency_ms > 0:
                status_box.label(text=f"Latency: {dispatcher.latency_ms}ms")
            status_box.operator("remotegpu.disconnect", text="Disconnect", icon="CANCEL")
        else:
            status_box.label(text="NOT CONNECTED", icon="ERROR")
            col = status_box.column(align=True)
            col.operator("remotegpu.connect", text="Connect", icon="PLAY")
            col.operator("remotegpu.test_connection", text="Test Dispatcher", icon="FILE_REFRESH")
