"""Remote GPU Render -- offload Cycles rendering to a remote GPU over WebSocket."""

# Version scheme: (major, minor, patch) where patch indicates build
# b1 = 1.0.1, b2 = 1.0.2, b3 = 1.0.3, b4 = 1.0.4, etc.
__version__ = "1.0.11"
BUILD = "b11"
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
import sys
import os
import subprocess

# Bootstrap: ensure websockets is available
ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_DIR = os.path.join(ADDON_DIR, "modules")


def _ensure_packages():
    """Install websockets to addon/modules/ if not already available."""
    # Add modules dir to path using BOTH methods for Blender compatibility
    if MODULES_DIR not in sys.path:
        sys.path.insert(0, MODULES_DIR)

    # Use site.addsitedir() for proper .pth support in Blender's Python
    import site
    site.addsitedir(MODULES_DIR)
    print(f"[RemoteGPU] Added to sys.path: {MODULES_DIR}")

    try:
        import websockets
        print(f"[RemoteGPU] ✓ websockets loaded successfully")
        return True
    except ImportError as e:
        print(f"[RemoteGPU] ✗ Failed to import websockets: {e}")
        print(f"[RemoteGPU] sys.path: {sys.path[:3]}")  # Show first 3 paths for debugging

    # Try to install
    print("[RemoteGPU] Attempting to install websockets package...")
    try:
        os.makedirs(MODULES_DIR, exist_ok=True)
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--target", MODULES_DIR, "--no-user", "--quiet",
            "websockets",
        ])
        print("[RemoteGPU] websockets installed successfully")
        return True
    except Exception as e:
        print(f"[RemoteGPU] Failed to install websockets: {e}")
        return False


# Import addon modules (these don't need websockets at import time)
from . import engine
from . import preferences
from . import operators

classes = [
    preferences.RemoteGPUPreferences,
    # preferences.REMOTEGPU_PT_panel,  # TODO: Debug panel registration issue
    operators.REMOTEGPU_OT_connect,
    operators.REMOTEGPU_OT_disconnect,
    operators.REMOTEGPU_OT_test_connection,
    engine.RemoteRenderEngine,
]


def register():
    # Ensure packages before registering
    _ensure_packages()

    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            print(f"[RemoteGPU] Failed to register {cls.__name__}: {e}")


def unregister():
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
