"""Remote GPU Render — offload Cycles rendering to a remote GPU over HTTP.

b25: Dual-viewport architecture
  - Left viewport: normal Blender (Solid mode, 60fps, Mac GPU)
  - Right viewport: live rendered preview from remote RTX GPU
  - No more "watching a video" — navigate normally, rendered view updates alongside
"""

__version__ = "1.0.26"
BUILD = "b26"
BUILD_DATE = "2026-04-03"

bl_info = {
    "name": "Remote GPU Render",
    "author": "Michael Knaap",
    "version": (1, 0, 25),
    "blender": (4, 0, 0),
    "location": "3D Viewport > N-Panel > Remote GPU",
    "description": "Live rendered preview on a remote RTX GPU — dual viewport architecture",
    "category": "Render",
}

import bpy

from . import engine
from . import preferences
from . import operators
from . import live_preview

classes = [
    # Preferences
    preferences.RemoteGPUPreferences,

    # Render engine
    engine.RemoteRenderEngine,

    # Render properties panel
    preferences.REMOTEGPU_PT_render_panel,

    # 3D viewport N-panel
    preferences.REMOTEGPU_PT_sidebar,

    # Connection operators
    operators.REMOTEGPU_OT_auto_discover,
    operators.REMOTEGPU_OT_connect,
    operators.REMOTEGPU_OT_disconnect,
    operators.REMOTEGPU_OT_test_connection,

    # Live preview operators
    live_preview.REMOTEGPU_OT_upload_scene,
    live_preview.REMOTEGPU_OT_set_preview_viewport,
    live_preview.REMOTEGPU_OT_start_live_preview,
    live_preview.REMOTEGPU_OT_stop_live_preview,
]


def _get_compatible_panels():
    """Get standard Blender panels that should show for our render engine."""
    exclude = {
        'VIEWLAYER_PT_filter',
        'VIEWLAYER_PT_layer_passes',
    }
    panels = []
    for panel in bpy.types.Panel.__subclasses__():
        if hasattr(panel, 'COMPAT_ENGINES') and 'BLENDER_RENDER' in panel.COMPAT_ENGINES:
            if panel.__name__ not in exclude:
                panels.append(panel)
    return panels


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            print(f"[RemoteGPU] Failed to register {cls.__name__}: {e}")

    # Add our engine to all standard Blender panels
    for panel in _get_compatible_panels():
        panel.COMPAT_ENGINES.add('REMOTE_GPU')

    print(f"[RemoteGPU] Addon registered (v{__version__} {BUILD})")
    print(f"[RemoteGPU] Open 3D Viewport > N-Panel > Remote GPU tab to start")


def unregister():
    # Stop live preview if running
    if live_preview.is_active():
        live_preview.stop_preview()

    # Disconnect if connected
    if engine.RemoteRenderEngine._connection:
        engine.RemoteRenderEngine._connection.close()
        engine.RemoteRenderEngine._connection = None

    # Remove from standard panels
    for panel in _get_compatible_panels():
        if 'REMOTE_GPU' in panel.COMPAT_ENGINES:
            panel.COMPAT_ENGINES.discard('REMOTE_GPU')

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    print("[RemoteGPU] Addon unregistered")


if __name__ == "__main__":
    register()
