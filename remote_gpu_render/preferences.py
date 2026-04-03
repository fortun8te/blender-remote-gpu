"""UI for the Remote GPU addon — b25 dual-viewport architecture."""

import bpy
from bpy.props import StringProperty, IntProperty


class RemoteGPUPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

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

        box = layout.box()
        box.label(text="Remote GPU Server", icon="URL")
        row = box.row(align=True)
        row.prop(self, "server_ip", text="IP")
        row.prop(self, "server_port", text="Port")

        from . import engine
        conn = engine.RemoteRenderEngine._connection
        status_box = layout.box()

        if conn and conn.connected:
            status_box.label(text="CONNECTED", icon="CHECKMARK")
            if conn.gpu_name:
                status_box.label(text=f"GPU: {conn.gpu_name}")
            if conn.vram_free > 0:
                status_box.label(text=f"VRAM: {conn.vram_free} MB free")
            if conn.latency_ms > 0:
                status_box.label(text=f"Latency: {conn.latency_ms}ms via {conn.method}")
            status_box.operator("remotegpu.disconnect", text="Disconnect", icon="CANCEL")
        else:
            status_box.label(text="NOT CONNECTED", icon="ERROR")
            if conn and conn.error:
                status_box.label(text=f"Error: {conn.error}")
            col = status_box.column(align=True)
            col.operator("remotegpu.connect", text="Connect", icon="PLAY")
            col.operator("remotegpu.test_connection", text="Test Connection", icon="FILE_REFRESH")


class REMOTEGPU_PT_render_panel(bpy.types.Panel):
    """Panel in Render Properties when Remote GPU engine is active."""
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

        addon = context.preferences.addons.get(__package__)
        if not addon:
            layout.label(text="Enable addon in preferences", icon="ERROR")
            return
        prefs = addon.preferences

        box = layout.box()
        box.label(text="Server", icon="URL")
        row = box.row(align=True)
        row.prop(prefs, "server_ip", text="IP")
        row.prop(prefs, "server_port", text="Port")

        from . import engine
        conn = engine.RemoteRenderEngine._connection

        if conn and conn.connected:
            s = layout.box()
            s.label(text=f"CONNECTED — {conn.gpu_name}", icon="CHECKMARK")
            s.label(text=f"Latency: {conn.latency_ms}ms ({conn.method})")
            s.operator("remotegpu.disconnect", text="Disconnect", icon="CANCEL")
        else:
            s = layout.box()
            s.label(text="NOT CONNECTED", icon="ERROR")
            if conn and conn.error:
                s.label(text=conn.error)
            col = s.column(align=True)
            col.scale_y = 1.5
            col.operator("remotegpu.connect", text="Connect to Server", icon="URL")
            col.operator("remotegpu.test_connection", text="Test Connection", icon="FILE_REFRESH")


class REMOTEGPU_PT_sidebar(bpy.types.Panel):
    """N-panel sidebar in 3D Viewport — main control panel for live preview.

    HOW TO USE:
      1. Split viewport into left (workspace) and right (preview)
      2. In the right viewport, open N-panel → Remote GPU tab
      3. Connect, Upload Scene, Set as Preview Viewport, Start Live Preview
      4. Navigate in the LEFT viewport — right viewport shows rendered result
    """
    bl_label = "Remote GPU"
    bl_idname = "REMOTEGPU_PT_sidebar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Remote GPU"

    def draw(self, context):
        layout = self.layout
        from . import engine
        from . import live_preview as lp

        conn = engine.RemoteRenderEngine._connection
        scene_uploaded = engine.RemoteRenderEngine._scene_uploaded

        # ── Connection ────────────────────────────────────────
        box = layout.box()
        box.label(text="Connection", icon="URL")

        if conn and conn.connected:
            row = box.row()
            row.label(text=conn.gpu_name, icon="CHECKMARK")
            box.label(text=f"{conn.latency_ms}ms  {conn.method}")
            box.operator("remotegpu.disconnect", text="Disconnect", icon="X")
        else:
            if conn and conn.error:
                box.label(text=conn.error, icon="ERROR")
            col = box.column(align=True)
            col.scale_y = 1.3
            col.operator("remotegpu.connect", text="Connect", icon="PLAY")
            col.operator("remotegpu.test_connection", text="Test", icon="FILE_REFRESH")

        # ── Scene ─────────────────────────────────────────────
        box = layout.box()
        box.label(text="Scene", icon="SCENE_DATA")

        if scene_uploaded:
            box.label(text="Scene in GPU memory", icon="CHECKMARK")
        else:
            box.label(text="Not uploaded", icon="ERROR")

        row = box.row()
        row.scale_y = 1.2
        row.enabled = (conn is not None and conn.connected)
        row.operator("remotegpu.upload_scene",
                     text="Upload Scene" if not scene_uploaded else "Re-upload Scene",
                     icon="EXPORT")

        # ── Live Preview ──────────────────────────────────────
        box = layout.box()
        box.label(text="Live Preview", icon="RENDER_ANIMATION")

        # Preview viewport status
        if lp._preview_area_ptr is not None:
            # Check if the stored pointer matches this viewport
            current_area_ptr = context.area.as_pointer() if context.area else None
            if current_area_ptr == lp._preview_area_ptr:
                box.label(text="This is the Preview Viewport", icon="VIEW3D")
            else:
                box.label(text="Preview viewport is set", icon="CHECKMARK")
        else:
            box.label(text="No preview viewport set", icon="INFO")

        # Set preview viewport button
        row = box.row()
        row.operator("remotegpu.set_preview_viewport",
                     text="Set This as Preview Viewport",
                     icon="PINNED")

        # Start / Stop
        row = box.row()
        row.scale_y = 1.5

        if lp.is_active():
            row.operator("remotegpu.stop_live_preview",
                         text="Stop Live Preview", icon="PAUSE")
            # Status
            status = lp.get_status()
            if status:
                box.label(text=status)
        else:
            row.enabled = scene_uploaded and (conn is not None and conn.connected)
            row.operator("remotegpu.start_live_preview",
                         text="Start Live Preview", icon="PLAY")

        # ── Setup guide ───────────────────────────────────────
        if not lp.is_active():
            box2 = layout.box()
            box2.label(text="Quick setup:", icon="QUESTION")
            col = box2.column(align=False)
            col.scale_y = 0.8
            col.label(text="1. Connect to server")
            col.label(text="2. Upload Scene")
            col.label(text="3. Split viewport (drag edge)")
            col.label(text="4. Hover RIGHT viewport")
            col.label(text="5. Set as Preview Viewport")
            col.label(text="6. Start Live Preview")
            col.label(text="7. Navigate in LEFT viewport")

        # ── F12 hint ──────────────────────────────────────────
        layout.separator()
        row = layout.row()
        row.label(text="F12 renders on remote GPU too", icon="INFO")
