"""Remote GPU Render -- offload Cycles rendering to a remote GPU over HTTP."""

# Version scheme: (major, minor, patch) where patch indicates build
# b1 = 1.0.1, b2 = 1.0.2, b3 = 1.0.3, b4 = 1.0.4, etc.
__version__ = "1.0.14"
BUILD = "b14"
BUILD_DATE = "2026-04-03"

bl_info = {
    "name": "Remote GPU Render",
    "author": "Michael Knaap",
    "version": (1, 0, 4),
    "blender": (4, 0, 0),
    "location": "Render Properties > Render Engine > Remote GPU",
    "description": "Render on a remote GPU server over your network",
    "category": "Render",
    "wiki_url": "https://github.com/fortun8te/blender-remote-gpu",
    "tracker_url": "https://github.com/fortun8te/blender-remote-gpu/issues",
}

import bpy

# Import addon modules (no external dependencies needed)
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
    """Register addon classes."""
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            print(f"[RemoteGPU] Failed to register {cls.__name__}: {e}")


def unregister():
    """Unregister addon classes."""
    # Disconnect on unregister
    if engine.RemoteRenderEngine._connection:
        engine.RemoteRenderEngine._connection.close()
        engine.RemoteRenderEngine._connection = None

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
