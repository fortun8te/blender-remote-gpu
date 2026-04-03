"""Remote GPU Render — offload Cycles rendering to a remote GPU over HTTP.

b27: robust import handling, classes built inside register()
"""

__version__ = "1.0.27"
BUILD = "b27"
BUILD_DATE = "2026-04-03"

bl_info = {
    "name": "Remote GPU Render",
    "author": "Michael Knaap",
    "version": (1, 0, 27),
    "blender": (4, 0, 0),
    "location": "3D Viewport > N-Panel > Remote GPU",
    "description": "Live rendered preview on a remote RTX GPU — dual viewport architecture",
    "category": "Render",
}

import bpy

# Import submodules — each wrapped so one failure doesn't kill the whole addon
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


def _build_classes():
    """Build class list at register-time, not import-time.
    This avoids crashes if any optional submodule failed to load.
    """
    cls = [
        preferences.RemoteGPUPreferences,
        engine.RemoteRenderEngine,
        preferences.REMOTEGPU_PT_render_panel,
        preferences.REMOTEGPU_PT_sidebar,
        operators.REMOTEGPU_OT_auto_discover,
        operators.REMOTEGPU_OT_connect,
        operators.REMOTEGPU_OT_disconnect,
        operators.REMOTEGPU_OT_test_connection,
    ]

    if _HAS_LIVE_PREVIEW and live_preview is not None:
        cls += [
            live_preview.REMOTEGPU_OT_upload_scene,
            live_preview.REMOTEGPU_OT_set_preview_viewport,
            live_preview.REMOTEGPU_OT_start_live_preview,
            live_preview.REMOTEGPU_OT_stop_live_preview,
        ]

    return cls


def _get_compatible_panels():
    exclude = {'VIEWLAYER_PT_filter', 'VIEWLAYER_PT_layer_passes'}
    panels = []
    for panel in bpy.types.Panel.__subclasses__():
        if hasattr(panel, 'COMPAT_ENGINES') and 'BLENDER_RENDER' in panel.COMPAT_ENGINES:
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
        panel.COMPAT_ENGINES.add('REMOTE_GPU')

    print(f"[RemoteGPU] Registered v{__version__} {BUILD}"
          f"{' (live preview active)' if _HAS_LIVE_PREVIEW else ' (live preview unavailable)'}")


def unregister():
    if _HAS_LIVE_PREVIEW and live_preview and live_preview.is_active():
        live_preview.stop_preview()

    if engine.RemoteRenderEngine._connection:
        engine.RemoteRenderEngine._connection.close()
        engine.RemoteRenderEngine._connection = None

    for panel in _get_compatible_panels():
        panel.COMPAT_ENGINES.discard('REMOTE_GPU')

    for cls in reversed(_build_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    print("[RemoteGPU] Unregistered")


if __name__ == "__main__":
    register()
