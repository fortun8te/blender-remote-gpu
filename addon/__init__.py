"""Remote GPU Render — offload Cycles rendering to a remote GPU over HTTP."""

__version__ = "1.0.17"
BUILD = "b17"
BUILD_DATE = "2026-04-03"

bl_info = {
    "name": "Remote GPU Render",
    "author": "Michael Knaap",
    "version": (1, 0, 16),
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
]


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            print(f"[RemoteGPU] Failed to register {cls.__name__}: {e}")
    print(f"[RemoteGPU] Addon registered (v{__version__} {BUILD})")


def unregister():
    if engine.RemoteRenderEngine._connection:
        engine.RemoteRenderEngine._connection.close()
        engine.RemoteRenderEngine._connection = None

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    print("[RemoteGPU] Addon unregistered")


if __name__ == "__main__":
    register()
