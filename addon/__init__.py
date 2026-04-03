"""Remote GPU Render -- offload Cycles rendering to a remote GPU over WebSocket."""

bl_info = {
    "name": "Remote GPU Render",
    "author": "Michael Knaap",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "Render Properties > Render Engine > Remote GPU",
    "description": "Render on a remote GPU server over your network",
    "category": "Render",
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
    # Add modules dir to path
    if MODULES_DIR not in sys.path:
        sys.path.insert(0, MODULES_DIR)

    try:
        import websockets
        return True
    except ImportError:
        pass

    # Try to install
    print("[RemoteGPU] Installing websockets package...")
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
    preferences.REMOTEGPU_PT_panel,
    operators.REMOTEGPU_OT_connect,
    operators.REMOTEGPU_OT_disconnect,
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
