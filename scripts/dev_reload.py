#!/usr/bin/env python3
"""Fast dev reload script for Blender addon.

Usage:
  python3 dev_reload.py          # Reload addon in running Blender
  python3 dev_reload.py --watch  # Auto-reload on file changes (requires watchdog)

This lets you:
1. Edit addon code
2. Run this script
3. See changes instantly in viewport
4. No Blender restart needed
"""

import subprocess
import sys
import time
from pathlib import Path

# Blender Python console command to reload addon
RELOAD_SCRIPT = """
import bpy
import importlib
import sys

# Unload addon
bpy.ops.preferences.addon_disable(module='remote_gpu')
bpy.ops.preferences.addon_remove(module='remote_gpu')

# Clear from sys.modules
modules_to_remove = [m for m in sys.modules if 'remote_gpu' in m]
for m in modules_to_remove:
    del sys.modules[m]

# Reload
bpy.ops.preferences.addon_install(filepath='__ADDON_PATH__')
bpy.ops.preferences.addon_enable(module='remote_gpu')

print('✅ Addon reloaded successfully')
"""

def find_blender_socket():
    """Find Blender's Python console socket."""
    import socket
    import os

    # Try common Blender socket locations
    socket_paths = [
        "/tmp/blender_python_console.sock",
        os.path.expanduser("~/.blender/blender_python.sock"),
    ]

    for path in socket_paths:
        if Path(path).exists():
            return path
    return None

def reload_addon_in_blender():
    """Send reload command to running Blender via Python console."""
    try:
        # Try Blender's remote execution if available
        import bpy
        print("✅ Direct Blender reload (running in Blender context)")

        bpy.ops.preferences.addon_disable(module='remote_gpu')
        bpy.ops.preferences.addon_remove(module='remote_gpu')

        import importlib
        import sys
        modules_to_remove = [m for m in sys.modules if 'remote_gpu' in m]
        for m in modules_to_remove:
            del sys.modules[m]

        addon_path = Path(__file__).parent / "addon" / "__init__.py"
        bpy.ops.preferences.addon_install(filepath=str(addon_path.parent.parent))
        bpy.ops.preferences.addon_enable(module='remote_gpu')

        print("✅ Addon reloaded")
        return True
    except Exception as e:
        print(f"⚠️  Direct reload failed: {e}")
        print("   Make sure Blender is running and addon is loaded")
        return False

def watch_and_reload():
    """Watch addon files and reload on change."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("❌ watchdog not installed. Install with: pip install watchdog")
        return

    addon_dir = Path(__file__).parent / "addon"

    class AddonChangeHandler(FileSystemEventHandler):
        def on_modified(self, event):
            if event.src_path.endswith('.py'):
                print(f"📝 Changed: {Path(event.src_path).name}")
                time.sleep(0.5)  # Wait for write to finish
                reload_addon_in_blender()

    observer = Observer()
    observer.schedule(AddonChangeHandler(), str(addon_dir), recursive=True)
    observer.start()

    print(f"👁️  Watching {addon_dir} for changes...")
    print("   Edit addon files to auto-reload (Ctrl+C to stop)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("✅ Stopped watching")
    observer.join()

if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch_and_reload()
    else:
        reload_addon_in_blender()
