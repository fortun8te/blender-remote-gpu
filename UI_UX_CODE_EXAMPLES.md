# Blender Remote GPU — UI/UX Code Examples & Implementation Guide

This document provides copy-paste-ready code for each improvement. Each section is self-contained and can be implemented independently.

---

## 1. CONNECTION TRACKING — `addon/connection.py`

Add these fields to `__init__` and update the connection logic:

```python
import time
import json

class Connection:
    def __init__(self, url):
        self.url = url
        self.connected = False
        self.gpu_name = ""
        self.vram_free = 0
        self.error = ""

        # NEW: Connection metadata
        self.connected_at = None       # Unix timestamp when connected
        self.latency_ms = 0            # PING → PONG round-trip time
        self.server_version = ""       # From PONG: "1.0.4"
        self.server_build = ""         # From PONG: "b4"

        self._send_queue = queue.Queue()
        self._recv_queue = queue.Queue()
        self._binary_queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._ping_sent_time = None    # Track PING timing

    def _worker(self):
        """Background thread: connect, send/receive, auto-reconnect."""
        connect_fn = _get_ws()

        while not self._stop.is_set():
            try:
                print(f"[Connection] Connecting to {self.url}...")
                ws = connect_fn(
                    self.url,
                    open_timeout=10,
                    close_timeout=5,
                    max_size=500 * 1024 * 1024,
                )
            except Exception as e:
                self.error = f"Connection failed: {e}"
                print(f"[Connection] {self.error}")
                self.connected = False
                if not self._stop.wait(2.0):
                    continue
                return

            try:
                # Send ping to verify connection
                self._ping_sent_time = time.time()  # NEW: Record PING time
                ws.send(json.dumps({"type": "ping"}))
                pong_raw = ws.recv(timeout=5.0)
                pong = json.loads(pong_raw)

                if pong.get("type") == "pong":
                    # NEW: Calculate latency
                    self.latency_ms = int((time.time() - self._ping_sent_time) * 1000)

                    self.gpu_name = pong.get("gpu", "Unknown")
                    self.vram_free = pong.get("vram_free", 0)

                    # NEW: Extract server version/build (graceful fallback)
                    self.server_version = pong.get("version", "")
                    self.server_build = pong.get("build", "")

                    # NEW: Record connection timestamp
                    self.connected_at = time.time()

                    self.connected = True
                    self.error = ""
                    print(f"[Connection] Connected! GPU: {self.gpu_name}, Latency: {self.latency_ms}ms")
                else:
                    self.error = "Unexpected server response"
                    ws.close()
                    continue

                # Main send/receive loop
                while not self._stop.is_set() and self.connected:
                    # Send queued messages
                    while not self._send_queue.empty():
                        try:
                            msg_type, data = self._send_queue.get_nowait()
                            if msg_type == "json":
                                ws.send(data)
                            else:
                                ws.send(data)
                        except queue.Empty:
                            break
                        except Exception as e:
                            print(f"[Connection] Send error: {e}")
                            self.connected = False
                            break

                    # Receive messages (non-blocking with short timeout)
                    try:
                        msg = ws.recv(timeout=0.05)
                        if isinstance(msg, bytes):
                            self._binary_queue.put(msg)
                        else:
                            self._recv_queue.put(json.loads(msg))
                    except TimeoutError:
                        pass
                    except Exception as e:
                        print(f"[Connection] Recv error: {e}")
                        self.connected = False
                        break

            except Exception as e:
                print(f"[Connection] Error: {e}")
                self.connected = False
            finally:
                try:
                    ws.close()
                except Exception:
                    pass

            # If we got here without stop, try reconnecting
            if not self._stop.is_set():
                print("[Connection] Reconnecting in 2s...")
                self._stop.wait(2.0)

        self.connected = False
        print("[Connection] Worker stopped.")
```

---

## 2. SERVER SIDE — `server/server.py`

Update the PONG response to include version info:

```python
# Near the top of the file, add version constants
import json
from shared.protocol import (
    PING, PONG, SCENE_UPLOAD, SCENE_ACK, FRAME, PROGRESS, ERROR,
    RENDER_START, RENDER_CANCEL, VIEWPORT_START, VIEWPORT_STOP,
    DEFAULT_HOST, DEFAULT_PORT,
)

# NEW: Version constants (sync with addon __init__.py)
SERVER_VERSION = "1.0.4"
SERVER_BUILD = "b4"

# Then in the _handle_json method:
async def _handle_json(self, ws, raw):
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await self._send_error(ws, "Invalid JSON")
        return

    msg_type = msg.get("type", "")
    print(f"[Server] Received: {msg_type}")

    if msg_type == PING:
        # NEW: Include version and build in PONG
        await ws.send(json.dumps({
            "type": PONG,
            "gpu": GPU_NAME,
            "vram_free": GPU_VRAM_FREE,
            "timestamp": time.time(),
            "version": SERVER_VERSION,  # NEW
            "build": SERVER_BUILD,      # NEW
        }))
```

---

## 3. FRIENDLY ERROR MAPPING — `addon/error_messages.py` (NEW FILE)

Create this new file for error recovery guidance:

```python
"""User-friendly error messages and recovery steps."""

# Map error keywords to helpful information
ERROR_RECOVERY = {
    "Connection refused": {
        "title": "Server rejected connection",
        "icon": "ERROR",
        "steps": [
            "1. Check IP address is correct",
            "2. Check port number is correct (default: 9876)",
            "3. Verify server is running: python server/server.py",
            "4. Try pinging the server first to verify network",
        ],
    },
    "Connection timed out": {
        "title": "Server not responding",
        "icon": "ERROR",
        "steps": [
            "1. Is the server running? Check the terminal",
            "2. Is Tailscale active? (if using remote IP like 100.x.x.x)",
            "3. Try a different network (WiFi vs Ethernet)",
            "4. Check firewall isn't blocking port 9876",
        ],
    },
    "Unexpected server response": {
        "title": "Server sent invalid data",
        "icon": "ERROR",
        "steps": [
            "1. Server and addon versions might be incompatible",
            "2. Try restarting the server",
            "3. Check server console for errors",
        ],
    },
    "Connection reset by peer": {
        "title": "Server disconnected unexpectedly",
        "icon": "ERROR",
        "steps": [
            "1. Check server console for crash or errors",
            "2. Verify network stability",
            "3. Try reconnecting",
        ],
    },
}


def get_error_help(error_str):
    """
    Match error string to helpful recovery info.

    Args:
        error_str: Raw error message (e.g., "Connection failed: [Errno 61] Connection refused")

    Returns:
        dict with "title", "icon", "steps" or None if no match
    """
    if not error_str:
        return None

    lower_error = error_str.lower()

    # Try exact match first
    for key, help_info in ERROR_RECOVERY.items():
        if key.lower() in lower_error:
            return help_info

    # Try partial match on keywords
    keyword_map = {
        "refused": ERROR_RECOVERY["Connection refused"],
        "timeout": ERROR_RECOVERY["Connection timed out"],
        "invalid": ERROR_RECOVERY["Unexpected server response"],
        "reset": ERROR_RECOVERY["Connection reset by peer"],
    }

    for keyword, help_info in keyword_map.items():
        if keyword in lower_error:
            return help_info

    return None
```

---

## 4. ENHANCED PREFERENCES PANEL — `addon/preferences.py` (UPDATED)

Complete replacement with validation and better layout:

```python
"""UI panel for the Remote GPU addon. Shows connection status, IP/Port settings."""

import bpy
from bpy.props import StringProperty, IntProperty, BoolProperty
import time
import re


class RemoteGPUPreferences(bpy.types.AddonPreferences):
    bl_idname = "addon"

    server_ip: StringProperty(
        name="Server IP",
        description="IP address of the remote GPU server (Tailscale IP like 100.x.x.x or hostname)",
        default="100.74.135.83",
        maxlen=255,
    )

    server_port: IntProperty(
        name="Port",
        description="WebSocket port (default 9876)",
        default=9876,
        min=1024,
        max=65535,
    )

    # NEW: Remember last good connection
    last_good_ip: StringProperty(
        name="Last good IP",
        default="",
    )

    last_good_port: IntProperty(
        name="Last good port",
        default=9876,
    )

    # NEW: Auto-reconnect option
    auto_reconnect: BoolProperty(
        name="Auto-reconnect on disconnect",
        description="Automatically try to reconnect if connection drops",
        default=True,
    )

    def draw(self, context):
        layout = self.layout

        # Header info
        layout.label(text="Configure your remote GPU server connection", icon="INFO")
        layout.separator()

        # SERVER ADDRESS section
        box = layout.box()
        box.label(text="SERVER ADDRESS", icon="URL")

        col = box.column(align=True)
        col.prop(self, "server_ip", text="IP Address")
        col.prop(self, "server_port", text="Port")

        # Validation indicator
        if self.server_ip and not self._is_valid_ip(self.server_ip):
            row = col.row()
            row.label(text="⚠️ Invalid IP/hostname format", icon="ERROR")

        # Help text (small labels)
        col.separator(factor=0.5)
        sub = col.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="Examples:", icon="INFO")
        sub.label(text="  • Tailscale: 100.x.x.x", icon="BLANK1")
        sub.label(text="  • Hostname: myserver.local", icon="BLANK1")
        sub.label(text="  • Local: 192.168.x.x", icon="BLANK1")

        layout.separator()

        # CONNECTION OPTIONS section
        box = layout.box()
        box.label(text="CONNECTION OPTIONS", icon="PREFERENCES")
        box.prop(self, "auto_reconnect")

        # Restore last good connection
        if self.last_good_ip and self.last_good_ip != self.server_ip:
            col = box.column(align=True)
            col.label(text=f"Last working: {self.last_good_ip}:{self.last_good_port}", icon="FILE_BACKUP")
            col.operator("remotegpu.restore_last_good", text="Restore Last Good Connection", icon="BACK")

        layout.separator()

        # Connection testing in render panel hint
        box = layout.box()
        box.label(text="TESTING", icon="ZOOM_SELECTED")
        col = box.column(align=True)
        col.scale_y = 0.9
        col.label(text="Quick connection test available in:", icon="INFO")
        col.label(text="Render Properties > Remote GPU > Quick Test", icon="BLANK1")

    @staticmethod
    def _is_valid_ip(ip_str):
        """Validate IPv4 address or hostname."""
        if not ip_str:
            return False

        # IPv4 validation
        ipv4_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        if re.match(ipv4_pattern, ip_str):
            parts = ip_str.split('.')
            try:
                return all(0 <= int(p) <= 255 for p in parts)
            except ValueError:
                return False

        # Hostname validation (e.g., myserver.local, gpu.company.com)
        hostname_pattern = r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$'
        return re.match(hostname_pattern, ip_str) is not None


class REMOTEGPU_PT_panel(bpy.types.Panel):
    bl_label = "Remote GPU"
    bl_idname = "REMOTEGPU_PT_panel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "render"

    @classmethod
    def poll(cls, context):
        return context.engine == "REMOTE_GPU"

    def draw(self, context):
        layout = self.layout

        # Version info (compact)
        try:
            from . import BUILD, BUILD_DATE
            row = layout.row()
            row.label(text=f"Remote GPU {BUILD} ({BUILD_DATE})", icon="INFO")
        except ImportError:
            pass

        # Get addon preferences
        try:
            addon = context.preferences.addons.get("addon")
            if not addon or not addon.preferences:
                layout.label(text="Enable addon in preferences first", icon="ERROR")
                return
            prefs = addon.preferences
        except Exception as e:
            layout.label(text=f"Preferences error: {e}", icon="ERROR")
            return

        from . import engine
        conn = engine.RemoteRenderEngine._connection

        # CONNECTION STATUS PANEL (main focus)
        status_box = layout.box()
        status_box.label(text="CONNECTION STATUS", icon="URL")

        if conn and conn.connected:
            # ✅ Connected state
            col = status_box.column(align=True)

            # Status line with elapsed time
            elapsed = time.time() - conn.connected_at if conn.connected_at else 0
            elapsed_str = self._format_elapsed(elapsed)
            col.label(text=f"🟢 CONNECTED ({elapsed_str} elapsed)", icon="CHECKMARK")

            # Connection details
            col.label(text=f"Server: {conn.url}")
            if conn.latency_ms:
                col.label(text=f"Latency: {conn.latency_ms}ms")
            col.label(text=f"GPU: {conn.gpu_name}")
            col.label(text=f"VRAM Free: {conn.vram_free:,} MB")
            if conn.server_version:
                col.label(text=f"Server: v{conn.server_version} ({conn.server_build})")

            # Action buttons
            col.separator(factor=0.5)
            row = col.row(align=True)
            row.operator("remotegpu.reconnect", text="Reconnect", icon="FILE_REFRESH")
            row.operator("remotegpu.copy_address", text="Copy URL", icon="COPYDOWN")

        elif conn and conn.error:
            # ❌ Error state
            col = status_box.column(align=True)
            col.label(text="🔴 CONNECTION FAILED", icon="ERROR")

            # Friendly error message
            friendly_error = self._format_error(conn.error)
            col.label(text=f"Error: {friendly_error}")

            # Recovery steps
            col.separator(factor=0.5)
            col.label(text="Recovery steps:", icon="CHECKMARK")
            steps = self._get_recovery_steps(conn.error)
            for step in steps:
                col.label(text=step, icon="BLANK1")

            # Retry button
            col.separator(factor=0.5)
            col.scale_y = 1.3
            col.operator("remotegpu.connect", text="Try Again", icon="PLAY")

        else:
            # ⚫ Not connected, no error (initial state)
            col = status_box.column(align=True)
            col.label(text="⚫ NOT CONNECTED", icon="BLANK1")

            col.separator(factor=0.5)
            col.scale_y = 1.3
            col.operator("remotegpu.connect", text="Connect to Server", icon="PLAY")

        # SERVER SETTINGS PANEL
        layout.separator()
        settings_box = layout.box()
        settings_box.label(text="SERVER SETTINGS", icon="PREFERENCES")

        row = settings_box.row(align=True)
        row.prop(prefs, "server_ip", text="IP")
        row.prop(prefs, "server_port", text="Port")

        # Quick test button
        col = settings_box.column()
        col.operator("remotegpu.quick_test", text="Quick Test Connection", icon="ZOOM_SELECTED")

        # Settings help
        col.separator(factor=0.5)
        sub = col.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="IP: Server IP or hostname", icon="INFO")
        sub.label(text="Port: WebSocket port (default 9876)", icon="INFO")

    @staticmethod
    def _format_elapsed(seconds):
        """Convert seconds to '3m 42s', '1h 5m', etc."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            m = int(seconds // 60)
            s = int(seconds % 60)
            return f"{m}m {s}s"
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h}h {m}m"

    @staticmethod
    def _format_error(error_str):
        """Convert technical error to user-friendly message."""
        error_map = {
            "refused": "Server rejected connection (check IP/port)",
            "timeout": "Server not responding (check if running)",
            "connection failed": "Could not reach server",
            "unexpected server response": "Server sent invalid data",
            "connection reset": "Server disconnected unexpectedly",
        }

        lower_error = error_str.lower()
        for key, friendly in error_map.items():
            if key in lower_error:
                return friendly
        return error_str  # Fallback to original

    @staticmethod
    def _get_recovery_steps(error_str):
        """Return list of recovery steps based on error."""
        lower_error = error_str.lower()

        if "refused" in lower_error:
            return [
                "1. Check IP and port are correct",
                "2. Verify server is running",
                "3. Check firewall settings",
            ]
        elif "timeout" in lower_error:
            return [
                "1. Is server running? Check terminal",
                "2. Is Tailscale active? (for 100.x.x.x IPs)",
                "3. Try a different network if available",
            ]
        elif "unexpected" in lower_error:
            return [
                "1. Verify server and addon versions match",
                "2. Restart the server",
                "3. Check server console for errors",
            ]
        else:
            return [
                "1. Check network connection",
                "2. Verify server address and port",
                "3. Try reconnecting",
            ]
```

---

## 5. NEW OPERATORS — `addon/operators.py` (ADDITIONS)

Add these operators to the existing file:

```python
"""Operators for connecting/disconnecting to the render server."""

import bpy
from . import engine
import time


# EXISTING operators (REMOTEGPU_OT_connect, REMOTEGPU_OT_disconnect) go here...
# Then add these new ones:

class REMOTEGPU_OT_reconnect(bpy.types.Operator):
    """Reconnect using current IP/port without disconnecting first"""
    bl_idname = "remotegpu.reconnect"
    bl_label = "Reconnect"
    bl_description = "Reconnect to the server using current settings"

    def execute(self, context):
        # First disconnect
        conn = engine.RemoteRenderEngine._connection
        if conn:
            conn.close()
            engine.RemoteRenderEngine._connection = None

        # Wait a moment
        time.sleep(0.5)

        # Then reconnect using same IP/port
        bpy.ops.remotegpu.connect()
        return {"FINISHED"}


class REMOTEGPU_OT_copy_address(bpy.types.Operator):
    """Copy server URL to clipboard"""
    bl_idname = "remotegpu.copy_address"
    bl_label = "Copy Address"
    bl_description = "Copy server URL to clipboard (IP:Port)"

    def execute(self, context):
        conn = engine.RemoteRenderEngine._connection
        if not conn:
            self.report({"WARNING"}, "Not connected")
            return {"CANCELLED"}

        try:
            # Copy to clipboard (platform-independent using Blender's internal)
            import subprocess
            import sys

            url = conn.url
            if sys.platform == "darwin":  # macOS
                subprocess.run(['pbcopy'], input=url.encode(), check=True)
            elif sys.platform == "linux":  # Linux
                subprocess.run(['xclip', '-selection', 'clipboard'], input=url.encode(), check=True)
            elif sys.platform == "win32":  # Windows
                subprocess.run(['clip'], input=url.encode(), check=True)

            self.report({"INFO"}, f"Copied: {url}")
        except Exception as e:
            self.report({"ERROR"}, f"Copy failed: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}


class REMOTEGPU_OT_quick_test(bpy.types.Operator):
    """Test IP/port without affecting current connection"""
    bl_idname = "remotegpu.quick_test"
    bl_label = "Quick Test"
    bl_description = "Test server connection without changing current connection"

    def execute(self, context):
        from .connection import Connection

        try:
            addon = context.preferences.addons.get("addon")
            if not addon:
                self.report({"ERROR"}, "Addon preferences not found")
                return {"CANCELLED"}
            prefs = addon.preferences
        except Exception as e:
            self.report({"ERROR"}, f"Preferences error: {e}")
            return {"CANCELLED"}

        # Create temporary test connection
        url = f"ws://{prefs.server_ip}:{prefs.server_port}"
        test_conn = Connection(url)

        self.report({"INFO"}, f"Testing {url}...")
        test_conn.connect()

        # Check result
        if test_conn.connected:
            self.report({"INFO"}, f"✓ Success: {test_conn.gpu_name} ({test_conn.vram_free} MB VRAM)")
            test_conn.close()
            return {"FINISHED"}
        else:
            error = test_conn.error or "Unknown error"
            self.report({"ERROR"}, f"✗ Test failed: {error}")
            test_conn.close()
            return {"CANCELLED"}


class REMOTEGPU_OT_restore_last_good(bpy.types.Operator):
    """Restore last known good server address"""
    bl_idname = "remotegpu.restore_last_good"
    bl_label = "Restore Last Good Connection"
    bl_description = "Restore the last server address that worked"

    def execute(self, context):
        try:
            addon = context.preferences.addons.get("addon")
            if not addon:
                self.report({"ERROR"}, "Addon preferences not found")
                return {"CANCELLED"}
            prefs = addon.preferences
        except Exception as e:
            self.report({"ERROR"}, f"Preferences error: {e}")
            return {"CANCELLED"}

        if not prefs.last_good_ip:
            self.report({"WARNING"}, "No previous good connection saved")
            return {"CANCELLED"}

        # Restore settings
        prefs.server_ip = prefs.last_good_ip
        prefs.server_port = prefs.last_good_port

        self.report({"INFO"}, f"Restored: {prefs.last_good_ip}:{prefs.last_good_port}")
        return {"FINISHED"}


# Update REMOTEGPU_OT_connect to save last good connection:
class REMOTEGPU_OT_connect(bpy.types.Operator):
    bl_idname = "remotegpu.connect"
    bl_label = "Connect"
    bl_description = "Connect to the remote GPU render server"

    def execute(self, context):
        try:
            prefs = context.preferences.addons.get("addon")
            if not prefs:
                self.report({"ERROR"}, "Addon preferences not found")
                return {"CANCELLED"}
            prefs = prefs.preferences
        except Exception as e:
            self.report({"ERROR"}, f"Preferences error: {e}")
            return {"CANCELLED"}

        # Close any existing connection
        if engine.RemoteRenderEngine._connection is not None:
            engine.RemoteRenderEngine._connection.close()
            engine.RemoteRenderEngine._connection = None

        # Connect
        from .connection import Connection
        url = f"ws://{prefs.server_ip}:{prefs.server_port}"
        conn = Connection(url)
        conn.connect()

        if conn.connected:
            engine.RemoteRenderEngine._connection = conn

            # NEW: Save as last good connection
            prefs.last_good_ip = prefs.server_ip
            prefs.last_good_port = prefs.server_port

            self.report({"INFO"}, f"Connected — {conn.gpu_name}")
        else:
            error = conn.error or "Unknown error"
            conn.close()
            self.report({"ERROR"}, f"Connection failed: {error}")

        return {"FINISHED"}
```

---

## 6. RENDER PROGRESS PANEL — `addon/ui_progress.py` (NEW FILE)

Create this new file for render progress tracking:

```python
"""Render progress display during remote GPU rendering."""

import bpy
import time


class REMOTEGPU_PT_render_progress(bpy.types.Panel):
    """Display render progress during remote rendering."""
    bl_label = "Render Progress"
    bl_idname = "REMOTEGPU_PT_render_progress"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "render"

    @classmethod
    def poll(cls, context):
        """Only show when rendering on remote GPU."""
        from . import engine
        conn = engine.RemoteRenderEngine._connection
        # Show if connected and render_state exists (gets set during render)
        return (context.engine == "REMOTE_GPU" and
                conn and conn.connected and
                hasattr(conn, 'render_state') and
                conn.render_state != "idle")

    def draw(self, context):
        from . import engine
        conn = engine.RemoteRenderEngine._connection

        if not conn or not hasattr(conn, 'render_state'):
            return

        layout = self.layout

        # Title
        layout.label(text="RENDER IN PROGRESS", icon="RENDER_RESULT")

        # Status text
        status_map = {
            "uploading": "Uploading scene",
            "waiting": "Waiting for server",
            "rendering": "Rendering samples",
            "denoising": "Denoising",
            "receiving": "Receiving result",
            "processing": "Processing",
        }
        status_text = status_map.get(conn.render_state, conn.render_state)
        layout.label(text=f"Status: {status_text}")

        # Overall progress bar (if applicable)
        if hasattr(conn, 'render_progress') and conn.render_progress > 0:
            col = layout.column(align=True)
            col.label(text=f"Progress: {int(conn.render_progress * 100)}%")
            # Draw a simple progress indicator with label
            row = col.row(align=True)
            row.label(text="[" + "█" * int(conn.render_progress * 20) +
                          "░" * (20 - int(conn.render_progress * 20)) + "]")

        # Samples info (for rendering state)
        if (conn.render_state == "rendering" and
            hasattr(conn, 'samples_done') and
            hasattr(conn, 'samples_total') and
            conn.samples_total > 0):

            col = layout.column(align=True)
            col.separator()
            col.label(text=f"Samples: {conn.samples_done} / {conn.samples_total}")

            # Sample progress bar
            if conn.samples_total > 0:
                sample_pct = conn.samples_done / conn.samples_total
                bar_len = 20
                filled = int(sample_pct * bar_len)
                bar = "█" * filled + "░" * (bar_len - filled)
                col.label(text=f"[{bar}]")

        # Time tracking
        if hasattr(conn, 'start_time') and conn.start_time:
            layout.separator()
            col = layout.column(align=True)

            elapsed = time.time() - conn.start_time
            elapsed_str = self._format_time(elapsed)
            col.label(text=f"Elapsed: {elapsed_str}")

            # Time estimate (if we're rendering)
            if (conn.render_state == "rendering" and
                hasattr(conn, 'samples_done') and
                hasattr(conn, 'samples_total') and
                conn.samples_done > 0 and
                conn.samples_total > 0):

                rate = conn.samples_done / elapsed  # samples per second
                remaining = conn.samples_total - conn.samples_done
                if rate > 0:
                    est_seconds = remaining / rate
                    est_str = self._format_time(est_seconds)
                    col.label(text=f"Est. remaining: ~{est_str}")

        # Cancel button
        layout.separator()
        col = layout.column()
        col.scale_y = 1.3
        col.operator("remotegpu.cancel_render", text="Cancel Render", icon="CANCEL")

    @staticmethod
    def _format_time(seconds):
        """Format seconds as '3m 42s', '1h 5m', etc."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            m = int(seconds // 60)
            s = int(seconds % 60)
            return f"{m}m {s}s"
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h}h {m}m"
```

---

## 7. CANCEL RENDER OPERATOR — `addon/operators.py` (ADDITION)

Add this operator:

```python
class REMOTEGPU_OT_cancel_render(bpy.types.Operator):
    """Cancel ongoing remote render"""
    bl_idname = "remotegpu.cancel_render"
    bl_label = "Cancel Render"
    bl_description = "Stop the render on the remote GPU server"

    def execute(self, context):
        conn = engine.RemoteRenderEngine._connection

        if not conn or not conn.connected:
            self.report({"ERROR"}, "Not connected to server")
            return {"CANCELLED"}

        # Send cancel message
        conn.send_json({"type": "render_cancel"})

        # Reset state
        if hasattr(conn, 'render_state'):
            conn.render_state = "idle"

        self.report({"INFO"}, "Render cancelled")
        return {"FINISHED"}
```

---

## 8. UPDATE ENGINE TO TRACK RENDER STATE — `addon/engine.py`

Add state tracking to the `render()` method:

```python
def render(self, depsgraph):
    """Final render (F12). Send scene to server, wait for result."""
    conn = RemoteRenderEngine._connection
    if not conn or not conn.connected:
        self.report({"ERROR"}, "Not connected to render server")
        return

    # Initialize render state tracking
    if not hasattr(conn, 'render_state'):
        conn.render_state = "idle"
    if not hasattr(conn, 'start_time'):
        conn.start_time = None
    if not hasattr(conn, 'render_progress'):
        conn.render_progress = 0.0
    if not hasattr(conn, 'samples_done'):
        conn.samples_done = 0
    if not hasattr(conn, 'samples_total'):
        conn.samples_total = 0

    # Record start time
    conn.start_time = time.time()
    conn.render_state = "uploading"

    scene = depsgraph.scene
    width = int(scene.render.resolution_x * scene.render.resolution_percentage / 100)
    height = int(scene.render.resolution_y * scene.render.resolution_percentage / 100)
    samples = scene.cycles.samples if hasattr(scene, "cycles") else 128

    # Save current .blend to temp
    blend_path = os.path.join(tempfile.gettempdir(), "remote_render_scene.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path, copy=True)

    # Upload scene
    with open(blend_path, "rb") as f:
        blend_data = f.read()

    conn.send_json({
        "type": "scene_upload",
        "filename": "scene.blend",
        "size": len(blend_data),
    })
    conn.send_binary(blend_data)

    # Wait for scene_ack
    conn.render_state = "waiting"
    ack = self._wait_for_message("scene_ack", timeout=30.0)
    if not ack:
        self.report({"ERROR"}, "Server did not acknowledge scene upload")
        conn.render_state = "idle"
        return

    # Start render
    conn.render_state = "rendering"
    conn.samples_total = samples
    conn.samples_done = 0
    conn.send_json({
        "type": "render_start",
        "width": width,
        "height": height,
        "samples": samples,
    })

    # Wait for frame result
    self.update_stats("", "Rendering on remote GPU...")

    frame_meta = self._wait_for_message("frame", timeout=300.0)
    if not frame_meta:
        self.report({"ERROR"}, "Render timed out or failed")
        conn.render_state = "idle"
        return

    # Receive result
    conn.render_state = "receiving"
    image_data = self._wait_for_binary(timeout=30.0)
    if not image_data:
        self.report({"ERROR"}, "Did not receive render result")
        conn.render_state = "idle"
        return

    # Process result
    conn.render_state = "processing"
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(image_data))
        img = img.convert("RGBA")
        pixels = np.array(img, dtype=np.float32) / 255.0
        pixels = np.flipud(pixels)

        result = self.begin_result(0, 0, width, height)
        layer = result.layers[0].passes["Combined"]
        layer.rect = pixels.reshape(-1, 4).tolist()
        self.end_result(result)

        self.update_stats("", "Render complete")
    except Exception as e:
        self.report({"ERROR"}, f"Failed to decode render: {e}")
    finally:
        conn.render_state = "idle"
        conn.start_time = None
```

---

## 9. REGISTER NEW CLASSES — `addon/__init__.py`

Update the classes list:

```python
from . import engine
from . import preferences
from . import operators
from . import ui_progress  # NEW

classes = [
    preferences.RemoteGPUPreferences,
    preferences.REMOTEGPU_PT_panel,
    ui_progress.REMOTEGPU_PT_render_progress,  # NEW
    operators.REMOTEGPU_OT_connect,
    operators.REMOTEGPU_OT_disconnect,
    operators.REMOTEGPU_OT_reconnect,           # NEW
    operators.REMOTEGPU_OT_copy_address,        # NEW
    operators.REMOTEGPU_OT_quick_test,          # NEW
    operators.REMOTEGPU_OT_restore_last_good,   # NEW
    operators.REMOTEGPU_OT_cancel_render,       # NEW
    engine.RemoteRenderEngine,
]
```

---

## Implementation Checklist

### Phase 1: Connection Status (2-3 hours)
- [ ] Add latency/version tracking to `connection.py`
- [ ] Update server PONG in `server.py`
- [ ] Redesign preferences panel in `preferences.py`
- [ ] Test connection state display
- [ ] Test elapsed time updates

### Phase 2: Error Messages & Testing (1-2 hours)
- [ ] Create `error_messages.py` with recovery mapping
- [ ] Add quick test operator
- [ ] Update connect operator with friendly errors
- [ ] Add copy address operator
- [ ] Test error states and recovery messages

### Phase 3: Render Progress (2-3 hours)
- [ ] Add render state tracking to `connection.py`
- [ ] Create `ui_progress.py` panel
- [ ] Update `engine.py` render method
- [ ] Add cancel operator
- [ ] Test progress updates during render

### Phase 4: Polish (1-2 hours)
- [ ] Add "restore last good" feature
- [ ] Test all error paths
- [ ] Performance testing
- [ ] UI layout refinement

---

## Testing Commands

```bash
# Test basic connection
# In Blender: Render Properties > Remote GPU > Quick Test Connection

# Test error handling
# Set IP to invalid address (e.g., "192.0.2.1") > Connect

# Test render progress
# Start a render (F12) with server running
# Watch progress panel update in real-time

# Test cancel
# Start render, then click Cancel Render button
```

---

## Common Issues & Solutions

**Progress panel doesn't appear:**
- Check `conn.render_state` is set in `engine.render()`
- Verify `poll()` condition is correct

**Error messages not showing:**
- Check `error_messages.py` keywords match actual errors
- Test with different connection failures

**Copy address fails:**
- Platform detection might be wrong (macOS/Linux/Windows)
- Try alternative clipboard methods if needed

**Elapsed time doesn't update:**
- Make sure `connected_at` is set when PONG received
- Check time.time() is imported

