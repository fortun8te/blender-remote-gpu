"""Remote GPU Render — offload Cycles rendering to a remote GPU over HTTP.

b29: Added importlib.reload() pattern so re-installing without a Blender
     restart works correctly (fixes stale sys.modules cache errors).
     Panel classes stay in __init__.py (b28 fix for Blender 5.0 metaclass).
"""

__version__ = "1.0.37"
BUILD = "b37"
BUILD_DATE = "2026-04-03"

bl_info = {
    "name": "Remote GPU Render",
    "author": "Michael Knaap",
    "version": (1, 0, 29),
    "blender": (4, 0, 0),
    "location": "3D Viewport > N-Panel > Remote GPU",
    "description": "Live rendered preview on a remote RTX GPU — dual viewport architecture",
    "category": "Render",
}

import bpy

# ── Reload support ────────────────────────────────────────────────────────────
# When an addon is re-installed or F8 is pressed without restarting Blender,
# Python keeps stale module objects in sys.modules.  The reload() calls below
# force every submodule to be re-executed from the new files on disk.
# The guard `if "preferences" in dir()` means: only reload when this module
# was already loaded in a previous session (i.e. the names are already bound).
if "preferences" in dir():
    import importlib
    importlib.reload(preferences)   # type: ignore[name-defined]
    importlib.reload(engine)        # type: ignore[name-defined]
    importlib.reload(operators)     # type: ignore[name-defined]
    from . import connection        # type: ignore[no-redef]
    importlib.reload(connection)    # type: ignore[name-defined]
    if "live_preview" in dir() and live_preview is not None:  # type: ignore[name-defined]
        try:
            importlib.reload(live_preview)  # type: ignore[name-defined]
        except Exception as _e:
            print(f"[RemoteGPU] live_preview reload failed: {_e}")

# ── Submodule imports — each isolated so one failure doesn't kill the addon ──

from . import engine
from . import preferences
from . import operators

try:
    from . import live_preview
    _HAS_LIVE_PREVIEW = True
except Exception as e:
    print(f"[RemoteGPU] live_preview unavailable: {e}")
    import traceback; traceback.print_exc()
    live_preview = None
    _HAS_LIVE_PREVIEW = False


# ── Panel: Render Properties ─────────────────────────────────────────────────

class REMOTEGPU_PT_render_panel(bpy.types.Panel):
    """Panel in Render Properties when Remote GPU engine is active."""
    bl_label = "Remote GPU"
    bl_idname = "REMOTEGPU_PT_render_panel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "render"
    COMPAT_ENGINES = {"REMOTE_GPU"}

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


# ── Panel: N-Panel Sidebar ────────────────────────────────────────────────────

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
            col.scale_y = 1.5
            col.operator("remotegpu.auto_discover",
                         text="Auto-discover (TB4 / LAN)", icon="SNAP_ON")
            col.separator()
            col.scale_y = 1.0
            col.operator("remotegpu.connect", text="Connect manually", icon="PLAY")
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

        lp = live_preview  # may be None if import failed

        if lp is None:
            box.label(text="Live preview unavailable", icon="ERROR")
            box.label(text="Check System Console for details")
        else:
            # Preview viewport status
            current_area_ptr = context.area.as_pointer() if context.area else None
            is_this_the_preview = (lp._preview_area_ptr is not None and
                                   current_area_ptr == lp._preview_area_ptr)
            preview_set_elsewhere = (lp._preview_area_ptr is not None and
                                     not is_this_the_preview)

            if is_this_the_preview:
                # This IS the preview viewport
                row = box.row()
                row.alert = True
                row.label(text="PREVIEW VIEWPORT", icon="RESTRICT_VIEW_OFF")
                box.operator("remotegpu.clear_preview_viewport",
                             text="Release", icon="X")
            elif preview_set_elsewhere:
                # A different viewport is the preview
                box.label(text="Preview active in another viewport", icon="INFO")
                box.operator("remotegpu.set_preview_viewport",
                             text="Move Preview Here", icon="PINNED")
            else:
                # No preview viewport set yet
                box.label(text="No preview viewport set", icon="RADIOBUT_OFF")
                box.operator("remotegpu.set_preview_viewport",
                             text="Set This as Preview Viewport", icon="PINNED")

            row = box.row()
            row.scale_y = 1.5

            if lp.is_active():
                row.operator("remotegpu.stop_live_preview",
                             text="Stop Live Preview", icon="PAUSE")
                status = lp.get_status()
                if status:
                    box.label(text=status)
            else:
                row.enabled = scene_uploaded and (conn is not None and conn.connected)
                row.operator("remotegpu.start_live_preview",
                             text="Start Live Preview", icon="PLAY")

        # ── Setup guide ───────────────────────────────────────
        if lp is None or not lp.is_active():
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
        layout.label(text="F12 renders on remote GPU too", icon="INFO")


# ── Class registry ────────────────────────────────────────────────────────────

def _build_classes():
    """Build the full class list.  Called both by register() and unregister()."""
    cls = [
        preferences.RemoteGPUPreferences,
        engine.RemoteRenderEngine,
        REMOTEGPU_PT_render_panel,
        REMOTEGPU_PT_sidebar,
        operators.REMOTEGPU_OT_auto_discover,
        operators.REMOTEGPU_OT_connect,
        operators.REMOTEGPU_OT_disconnect,
        operators.REMOTEGPU_OT_test_connection,
    ]

    if _HAS_LIVE_PREVIEW and live_preview is not None:
        cls += [
            live_preview.REMOTEGPU_OT_upload_scene,
            live_preview.REMOTEGPU_OT_set_preview_viewport,
            live_preview.REMOTEGPU_OT_clear_preview_viewport,
            live_preview.REMOTEGPU_OT_start_live_preview,
            live_preview.REMOTEGPU_OT_stop_live_preview,
        ]

    return cls


def _get_compatible_panels():
    """Return all standard Blender panels that advertise BLENDER_RENDER support."""
    exclude = {"VIEWLAYER_PT_filter", "VIEWLAYER_PT_layer_passes"}
    panels = []
    for panel in bpy.types.Panel.__subclasses__():
        if hasattr(panel, "COMPAT_ENGINES") and "BLENDER_RENDER" in panel.COMPAT_ENGINES:
            if panel.__name__ not in exclude:
                panels.append(panel)
    return panels


def register():
    classes = _build_classes()

    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            print(f"[RemoteGPU] Failed to register {cls.__name__}: {e}")

    for panel in _get_compatible_panels():
        panel.COMPAT_ENGINES.add("REMOTE_GPU")

    print(f"[RemoteGPU] Registered v{__version__} {BUILD}"
          f"{' (live preview active)' if _HAS_LIVE_PREVIEW else ' (live preview unavailable)'}")


def unregister():
    if _HAS_LIVE_PREVIEW and live_preview and live_preview.is_active():
        live_preview.stop_preview()

    if engine.RemoteRenderEngine._connection:
        engine.RemoteRenderEngine._connection.close()
        engine.RemoteRenderEngine._connection = None

    for panel in _get_compatible_panels():
        panel.COMPAT_ENGINES.discard("REMOTE_GPU")

    for cls in reversed(_build_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    print("[RemoteGPU] Unregistered")


if __name__ == "__main__":
    register()
