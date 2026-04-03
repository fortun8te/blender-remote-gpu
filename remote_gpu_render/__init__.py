"""Remote GPU Render — offload Cycles rendering to a remote GPU over HTTP."""

__version__ = "1.0.24"
BUILD = "b24"
BUILD_DATE = "2026-04-03"

bl_info = {
    "name": "Remote GPU Render",
    "author": "Michael Knaap",
    "version": (1, 0, 24),
    "blender": (4, 0, 0),
    "location": "Render Properties > Render Engine > Remote GPU",
    "description": "Render on a remote GPU server over your network",
    "category": "Render",
}

import bpy

from . import engine
from . import preferences
from . import operators

classes = [
    preferences.RemoteGPUPreferences,
    operators.REMOTEGPU_OT_connect,
    operators.REMOTEGPU_OT_disconnect,
    operators.REMOTEGPU_OT_test_connection,
    engine.RemoteRenderEngine,
    preferences.REMOTEGPU_PT_render_panel,
]


def _get_compatible_panels():
    """Get all standard Blender panels that should show for our engine."""
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


def unregister():
    if engine.RemoteRenderEngine._connection:
        engine.RemoteRenderEngine._connection.close()
        engine.RemoteRenderEngine._connection = None

    # Remove our engine from standard panels
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
