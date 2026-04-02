"""Remote GPU Render — Blender addon that offloads Cycles rendering to a remote GPU."""

bl_info = {
    "name": "Remote GPU Render",
    "author": "Michael Knaap",
    "version": (0, 2, 0),
    "blender": (4, 0, 0),
    "location": "Render Engine Dropdown",
    "description": "Seamless Cycles rendering on a remote GPU — same workflow, faster results",
    "category": "Render",
}

import bpy
import sys
from pathlib import Path

from . import engine
from . import preferences

# Try to load dev config for fast iteration
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from shared import dev_config
    HAS_DEV_CONFIG = True
except (ImportError, AttributeError):
    HAS_DEV_CONFIG = False
    dev_config = None


classes = [
    preferences.RemoteGPUPreferences,
    preferences.REMOTEGPU_PT_connection_panel,
    preferences.REMOTEGPU_OT_connect,
    preferences.REMOTEGPU_OT_disconnect,
    preferences.REMOTEGPU_OT_select_device,  # Agent 4 — GPU Backend Selection
    engine.RemoteRenderEngine,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    # Auto-connect on startup if dev_config available and AUTO_CONNECT=True
    if HAS_DEV_CONFIG and dev_config and getattr(dev_config, 'AUTO_CONNECT', False):
        ip = dev_config.REMOTE_SERVER_IP
        port = dev_config.REMOTE_SERVER_PORT
        use_tls = getattr(dev_config, 'USE_TLS', False)
        api_key = getattr(dev_config, 'API_KEY', None)

        print(f"[RemoteGPU] Auto-connecting to {ip}:{port} (TLS={'on' if use_tls else 'off'})")
        try:
            # Set preferences from dev_config
            prefs = bpy.context.preferences.addons[__name__].preferences
            prefs.server_ip = ip
            prefs.server_port = port
            if hasattr(prefs, 'denoiser_type'):
                prefs.denoiser_type = dev_config.DEFAULT_DENOISER
            if hasattr(prefs, 'denoiser_intensity'):
                prefs.denoiser_intensity = dev_config.DEFAULT_DENOISER_INTENSITY
            if hasattr(prefs, 'viewport_quality'):
                prefs.viewport_quality = getattr(dev_config, 'VIEWPORT_QUALITY', 75)

            # Build URL with correct protocol
            protocol = "wss" if use_tls else "ws"
            url = f"{protocol}://{ip}:{port}"

            # Connect
            from . import connection
            conn = connection.Connection(url, api_key=api_key, use_tls=use_tls)
            conn.connect()
            engine.RemoteRenderEngine._connection = conn
            print(f"[RemoteGPU] Connected to {ip}:{port}")
        except Exception as e:
            print(f"[RemoteGPU] Auto-connect failed: {e}")


def unregister():
    # Disconnect any active connection
    if engine.RemoteRenderEngine._connection is not None:
        engine.RemoteRenderEngine._connection.close()
        engine.RemoteRenderEngine._connection = None

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
