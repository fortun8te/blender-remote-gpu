# Blender Remote GPU Addon — UI/UX Review & Improvement Recommendations

**Date:** 2026-04-03
**Current Version:** 1.0.4 (b4)
**Review Focus:** Connection status, error messaging, render progress, preferences panel

---

## Executive Summary

The addon is functionally solid but UI/UX needs polish for production readiness. Key gaps:
1. **Connection status** is minimal (just "Connected" label)
2. **Error messages** are cryptic and don't guide users to solutions
3. **Render progress** is invisible (no feedback during long renders)
4. **Preferences panel** lacks validation and helpful context
5. **Visual hierarchy** is weak (all elements same importance)

**Estimated effort:** 2-3 days of development for all improvements
**Priority ranking:** 1 must-have improvement + 3 high-priority + 2 nice-to-have

---

## 1. CONNECTION STATUS PANEL — MUST-HAVE

### Current State
```
┌─ Remote GPU ─────────────────────┐
│ Remote GPU b4 (2026-04-03)        │ ← Version box only
├─────────────────────────────────────
│ Server                            │
│  IP: [100.74.135.83     Port: 9876]
│                [Disconnect]       │
│ ═════════════════════════════════│
│ Connected         ← CHECKMARK     │
│ GPU: RTX 4090                     │
│ VRAM Free: 23456 MB               │
└─────────────────────────────────────
```

**Issues:**
- No connection state machine (Connecting → Connected transitions)
- No latency/response time indicator
- No server version/build info (only in ping response)
- "Connected" label has no context (connected how long?)
- No way to see connection details without inspector
- Error state shows generic text with no actionable steps

### Desired State
```
┌─ Remote GPU ─────────────────────────┐
│ Remote GPU b4 (2026-04-03)          │
├───────────────────────────────────────
│ ⚙️ CONNECTION STATUS                │
│ ┌─────────────────────────────────┐ │
│ │ 🟢 CONNECTED (3m 42s elapsed)   │ │  ← Color-coded + elapsed time
│ │ Server: ws://100.74.135.83:9876 │ │  ← URL for verification
│ │ Latency: 12ms                   │ │  ← Response time
│ │ GPU: RTX 4090                   │ │  ← GPU name (via PONG)
│ │ VRAM: 23,456 MB free            │ │  ← Formatted with commas
│ │ Server v1.0.4 (b4)              │ │  ← Version from PONG
│ └─────────────────────────────────┘ │
│ [Reconnect]  [Copy Address]  [ⓘ]   │  ← Actions + help icon
├───────────────────────────────────────
│ ⚙️ SERVER SETTINGS                  │
│ IP: [100.74.135.83     Port: 9876] │
│     [Test Connection ▶]             │  ← Validation button
├───────────────────────────────────────
│                    [Disconnect]     │
└───────────────────────────────────────
```

### Implementation Details

**File:** `addon/connection.py` — Add connection metadata tracking
```python
class Connection:
    def __init__(self, url):
        # ... existing code ...
        self.connected_at = None       # Timestamp when PONG received
        self.latency_ms = 0            # Measured from PING to PONG
        self.server_version = ""       # From PONG: "1.0.4"
        self.server_build = ""         # From PONG: "b4"

    def _worker(self):
        # ... existing code ...
        # After receiving PONG:
        pong = json.loads(pong_raw)
        if pong.get("type") == "pong":
            self.gpu_name = pong.get("gpu", "Unknown")
            self.vram_free = pong.get("vram_free", 0)
            self.server_version = pong.get("version", "")  # NEW
            self.server_build = pong.get("build", "")      # NEW
            self.connected_at = time.time()                # NEW
            self.connected = True
```

**File:** `server/server.py` — Include version in PONG
```python
async def _handle_json(self, ws, raw):
    # ... existing code ...
    if msg_type == PING:
        await ws.send(json.dumps({
            "type": PONG,
            "gpu": GPU_NAME,
            "vram_free": GPU_VRAM_FREE,
            "timestamp": time.time(),
            "version": "1.0.4",     # NEW
            "build": "b4",          # NEW
        }))
```

**File:** `addon/preferences.py` — Redesigned status panel
```python
class REMOTEGPU_PT_panel(bpy.types.Panel):
    # ... existing code ...

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
        except Exception:
            layout.label(text="Preferences error", icon="ERROR")
            return

        from . import engine
        conn = engine.RemoteRenderEngine._connection

        # STATUS PANEL (always visible, color-coded)
        status_box = layout.box()
        status_box.label(text="CONNECTION STATUS", icon="URL")

        if conn and conn.connected:
            # Connected state
            col = status_box.column(align=True)

            # Status line with elapsed time
            elapsed = time.time() - conn.connected_at if conn.connected_at else 0
            elapsed_str = self._format_elapsed(elapsed)
            col.label(text=f"🟢 CONNECTED ({elapsed_str} elapsed)",
                     icon="CHECKMARK")  # Will show as green highlight

            # Connection details (sub-row, indented)
            col.label(text=f"Server: {conn.url}")
            col.label(text=f"Latency: {conn.latency_ms}ms")
            col.label(text=f"GPU: {conn.gpu_name}")
            col.label(text=f"VRAM Free: {conn.vram_free:,} MB")
            if conn.server_version:
                col.label(text=f"Server: v{conn.server_version} ({conn.server_build})")

            # Action buttons
            row = status_box.row(align=True)
            row.operator("remotegpu.reconnect", text="Reconnect", icon="FILE_REFRESH")
            row.operator("remotegpu.copy_address", text="Copy URL", icon="COPYDOWN")
            row.operator("remotegpu.show_connection_info", text="", icon="QUESTION")

        elif conn and conn.error:
            # Error state (red background hint)
            col = status_box.column(align=True)
            col.label(text="🔴 CONNECTION FAILED", icon="ERROR")
            col.label(text=f"Error: {self._format_error(conn.error)}")

            # Helpful recovery steps
            col.separator()
            col.label(text="Recovery steps:", icon="CHECKMARK")
            col.label(text="1. Check IP/port in Server Settings")
            col.label(text="2. Verify server is running")
            col.label(text="3. Ensure Tailscale is active (if remote)")
            col.label(text="4. Check firewall/network")

            # Retry button
            row = status_box.row(align=True)
            row.scale_y = 1.3
            row.operator("remotegpu.connect", text="Try Again", icon="URL")

        else:
            # Not connected, not errored (initial state)
            col = status_box.column(align=True)
            col.label(text="⚫ NOT CONNECTED", icon="BLANK1")

            # Quick test button
            row = status_box.row(align=True)
            row.scale_y = 1.3
            row.operator("remotegpu.connect", text="Connect to Server", icon="PLAY")

        # SERVER SETTINGS PANEL
        settings_box = layout.box()
        settings_box.label(text="SERVER SETTINGS", icon="PREFERENCES")

        row = settings_box.row(align=True)
        row.prop(prefs, "server_ip", text="IP")
        row.prop(prefs, "server_port", text="Port")

        # IP validation indicator
        if not self._is_valid_ip(prefs.server_ip):
            row = settings_box.row()
            row.label(text="⚠️ Invalid IP format", icon="ERROR")

        # Quick test button
        row = settings_box.row()
        row.operator("remotegpu.quick_test", text="Quick Test Connection", icon="ZOOM_SELECTED")

        # Settings info (collapsible in future)
        row = settings_box.row()
        row.label(text="IP: Server hostname or Tailscale IP (e.g., 100.x.x.x)", icon="INFO")
        row = settings_box.row()
        row.label(text="Port: WebSocket port (default 9876)", icon="INFO")

    @staticmethod
    def _format_elapsed(seconds):
        """Convert seconds to human-readable format: '3m 42s', '1h 5m', etc."""
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
    def _is_valid_ip(ip_str):
        """Check if string is a valid IPv4 or reasonable hostname."""
        import re
        ipv4_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        hostname_pattern = r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z]{2,}$'

        if re.match(ipv4_pattern, ip_str):
            parts = ip_str.split('.')
            return all(0 <= int(p) <= 255 for p in parts)
        return re.match(hostname_pattern, ip_str) is not None

    @staticmethod
    def _format_error(error_str):
        """Convert technical error to user-friendly message."""
        error_map = {
            "timeout": "Server not responding (timeout)",
            "refused": "Server rejected connection (wrong IP/port?)",
            "connection failed": "Could not reach server",
            "unexpected server response": "Server sent invalid data",
        }

        lower_error = error_str.lower()
        for key, friendly in error_map.items():
            if key in lower_error:
                return friendly
        return error_str  # Fallback to original
```

**File:** `addon/operators.py` — Add new operators
```python
class REMOTEGPU_OT_reconnect(bpy.types.Operator):
    """Reconnect using same IP/port"""
    bl_idname = "remotegpu.reconnect"
    bl_label = "Reconnect"
    bl_description = "Reconnect to the server without changing settings"

    def execute(self, context):
        # Same as connect, but shows "Reconnecting..." feedback
        bpy.ops.remotegpu.disconnect()
        bpy.ops.remotegpu.connect()
        return {"FINISHED"}


class REMOTEGPU_OT_copy_address(bpy.types.Operator):
    """Copy connection URL to clipboard"""
    bl_idname = "remotegpu.copy_address"
    bl_label = "Copy Address"
    bl_description = "Copy server URL to clipboard"

    def execute(self, context):
        try:
            from . import engine
            conn = engine.RemoteRenderEngine._connection
            if conn:
                import subprocess
                subprocess.run(['pbcopy'], input=conn.url.encode(), check=True)
                self.report({"INFO"}, f"Copied: {conn.url}")
            else:
                self.report({"WARNING"}, "Not connected")
        except Exception as e:
            self.report({"ERROR"}, f"Copy failed: {e}")
        return {"FINISHED"}


class REMOTEGPU_OT_quick_test(bpy.types.Operator):
    """Test connection without disconnecting current connection"""
    bl_idname = "remotegpu.quick_test"
    bl_label = "Quick Test"
    bl_description = "Test the IP/port without changing current connection"

    def execute(self, context):
        from .connection import Connection

        try:
            prefs = context.preferences.addons.get("addon").preferences
        except:
            self.report({"ERROR"}, "Addon preferences not found")
            return {"CANCELLED"}

        # Create temporary test connection
        url = f"ws://{prefs.server_ip}:{prefs.server_port}"
        test_conn = Connection(url)
        test_conn.connect()

        if test_conn.connected:
            self.report({"INFO"}, f"✓ Server OK: {test_conn.gpu_name}")
            test_conn.close()
        else:
            error = test_conn.error or "Unknown error"
            self.report({"ERROR"}, f"✗ Test failed: {error}")
            test_conn.close()

        return {"FINISHED"}
```

**Priority:** MUST-HAVE
**Difficulty:** Medium (logic already exists, mostly UI refactoring)
**Time:** 4-5 hours

---

## 2. ERROR MESSAGES & RECOVERY — HIGH PRIORITY

### Current State
```
Error: Connection failed: [Errno 61] Connection refused
Error: Connection timed out
Error: Unexpected server response
```

**Problems:**
- Generic/cryptic messages don't help users fix the problem
- No suggested recovery steps
- No context about what user should check
- Mixed technical + user-facing language

### Desired State

**Map connection errors to actionable guidance:**

```python
# addon/error_messages.py (NEW FILE)

ERROR_RECOVERY = {
    "Connection refused": {
        "title": "Server rejected connection",
        "steps": [
            "1. Check IP address is correct",
            "2. Check port number is correct (default: 9876)",
            "3. Verify server is running: python server/server.py",
            "4. Try pinging the server first to verify network",
        ],
        "icon": "ERROR",
    },
    "Connection timed out": {
        "title": "Server not responding",
        "steps": [
            "1. Is the server running? Check the terminal",
            "2. Is Tailscale active? (if using remote IP like 100.x.x.x)",
            "3. Try a different network (WiFi vs Ethernet)",
            "4. Check firewall isn't blocking port 9876",
        ],
        "icon": "ERROR",
    },
    "Unexpected server response": {
        "title": "Server sent invalid data",
        "steps": [
            "1. Server and addon versions might be incompatible",
            "2. Try restarting the server",
            "3. Check server console for errors",
        ],
        "icon": "ERROR",
    },
    "Connection reset by peer": {
        "title": "Server disconnected unexpectedly",
        "steps": [
            "1. Check server console for crash or errors",
            "2. Verify network stability",
            "3. Try reconnecting",
        ],
        "icon": "ERROR",
    },
}

def get_error_help(error_str):
    """Return user-friendly error info."""
    lower_error = error_str.lower()
    for key, help_info in ERROR_RECOVERY.items():
        if key.lower() in lower_error:
            return help_info
    return None
```

**Update operators.py:**
```python
class REMOTEGPU_OT_connect(bpy.types.Operator):
    # ... existing code ...

    def execute(self, context):
        # ... existing connect logic ...

        if conn.connected:
            engine.RemoteRenderEngine._connection = conn
            self.report({"INFO"}, f"Connected — {conn.gpu_name}")
        else:
            error = conn.error or "Unknown error"

            # Log detailed error to console
            print(f"[RemoteGPU] Connection error: {error}")

            # Map to user-friendly message
            from . import error_messages
            help_info = error_messages.get_error_help(error)

            if help_info:
                self.report({"ERROR"}, help_info["title"])
                # Store steps for UI display
                context.scene.remotegpu_error_help = help_info
            else:
                self.report({"ERROR"}, error)

            conn.close()

        return {"FINISHED"}
```

**Priority:** HIGH (greatly improves user experience)
**Difficulty:** Easy (mostly data mapping)
**Time:** 1-2 hours

---

## 3. RENDER PROGRESS UI — HIGH PRIORITY

### Current State
```
Rendering on remote GPU...
```
Shows single status message, no progress bar or time estimates.

**Problems:**
- User doesn't know if render is stuck or progressing
- No way to estimate time remaining
- Can't see sample count or completion %
- No cancel button during render

### Desired State

**New component: `RenderProgressBar`**

```
┌─ Remote GPU ─────────────────────┐
│ RENDER IN PROGRESS                │
├─────────────────────────────────────
│ Status: Uploading scene (2/3)     │
│ ╔════════════════════════════════╗│
│ ║████████░░░░░░░░░░░░░░░░░░░░░░║│  ← 33% progress
│ ╚════════════════════════════════╝│
│ Time elapsed: 12s                 │
│ Est. remaining: ~45s              │
├─────────────────────────────────────
│ Samples: 64 / 128 (50%)           │
│ ╔════════════════════════════════╗│
│ ║████████████████░░░░░░░░░░░░░░║│
│ ╚════════════════════════════════╝│
│ Denoiser: Processing...           │
│                            [CANCEL]│
└─────────────────────────────────────
```

**Implementation approach:**

1. **Extend Connection class to track render state:**

```python
# addon/connection.py
class Connection:
    def __init__(self, url):
        # ... existing code ...
        self.render_state = "idle"  # idle, uploading, waiting, rendering, denoising
        self.render_progress = 0.0  # 0.0 to 1.0
        self.samples_done = 0
        self.samples_total = 0
        self.start_time = None
        self.last_update_time = None

    def _worker(self):
        # ... existing code ...
        # In message handling loop:
        msg = conn.recv(timeout=0.05)
        if msg and msg.get("type") == "progress":
            self.samples_done = msg.get("samples_done", 0)
            self.samples_total = msg.get("samples_total", 0)
            self.render_progress = (self.samples_done / self.samples_total
                                   if self.samples_total > 0 else 0)
            self.last_update_time = time.time()
```

2. **Create new UI component: `addon/ui_progress.py`**

```python
import bpy
import time

class REMOTEGPU_PT_render_progress(bpy.types.Panel):
    """Show render progress when rendering."""
    bl_label = "Render Progress"
    bl_idname = "REMOTEGPU_PT_render_progress"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "render"

    @classmethod
    def poll(cls, context):
        from . import engine
        conn = engine.RemoteRenderEngine._connection
        return context.engine == "REMOTE_GPU" and conn and conn.render_state != "idle"

    def draw(self, context):
        from . import engine
        conn = engine.RemoteRenderEngine._connection

        if not conn or conn.render_state == "idle":
            return

        layout = self.layout

        # Title
        layout.label(text="RENDER IN PROGRESS", icon="RENDER_RESULT")

        # Status line
        status_map = {
            "uploading": "Uploading scene",
            "waiting": "Waiting for server",
            "rendering": "Rendering",
            "denoising": "Denoising",
            "receiving": "Receiving result",
        }
        status_text = status_map.get(conn.render_state, conn.render_state)
        layout.label(text=f"Status: {status_text}")

        # Main progress bar (overall)
        row = layout.row()
        row.scale_y = 1.5

        # Use visual bar
        if conn.render_progress > 0:
            row.prop(context.scene, "render_progress", slider=True)  # Will use custom progress prop

        # Samples counter (if rendering)
        if conn.render_state == "rendering":
            layout.label(text=f"Samples: {conn.samples_done} / {conn.samples_total}")

            # Sample progress bar
            if conn.samples_total > 0:
                row = layout.row()
                row.scale_y = 1.2
                layout.separator()

        # Time info
        if conn.start_time:
            elapsed = time.time() - conn.start_time
            elapsed_str = self._format_time(elapsed)
            layout.label(text=f"Time elapsed: {elapsed_str}")

            # Estimate remaining (if we have sample progress)
            if conn.samples_done > 0 and conn.samples_total > 0:
                rate = conn.samples_done / elapsed  # samples per second
                remaining_samples = conn.samples_total - conn.samples_done
                est_seconds = remaining_samples / rate if rate > 0 else 0
                est_str = self._format_time(est_seconds)
                layout.label(text=f"Est. remaining: ~{est_str}")

        # Cancel button
        row = layout.row()
        row.scale_y = 1.3
        row.operator("remotegpu.cancel_render", text="Cancel Render", icon="CANCEL")

    @staticmethod
    def _format_time(seconds):
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
```

3. **Add cancel operator:**

```python
# addon/operators.py
class REMOTEGPU_OT_cancel_render(bpy.types.Operator):
    """Cancel ongoing render"""
    bl_idname = "remotegpu.cancel_render"
    bl_label = "Cancel Render"
    bl_description = "Stop the render on the remote server"

    def execute(self, context):
        from . import engine
        conn = engine.RemoteRenderEngine._connection

        if conn and conn.connected:
            conn.send_json({"type": "render_cancel"})
            conn.render_state = "idle"
            self.report({"INFO"}, "Render cancelled")
        else:
            self.report({"ERROR"}, "Not connected")

        return {"FINISHED"}
```

4. **Update engine.py to track render state:**

```python
# addon/engine.py
def render(self, depsgraph):
    conn = RemoteRenderEngine._connection
    # ... existing validation ...

    conn.render_state = "uploading"
    conn.start_time = time.time()

    # Upload scene
    # ...
    conn.send_json({"type": "scene_upload", ...})
    conn.send_binary(blend_data)

    # Wait for ack
    conn.render_state = "waiting"
    ack = self._wait_for_message("scene_ack", timeout=30.0)

    # Start render
    conn.render_state = "rendering"
    conn.send_json({"type": "render_start", ...})

    # Wait for result...
    frame_meta = self._wait_for_message("frame", timeout=300.0)

    conn.render_state = "receiving"
    image_data = self._wait_for_binary(timeout=30.0)

    # Finish
    conn.render_state = "idle"
    # ... rest of render logic ...
```

**Priority:** HIGH (core UX gap)
**Difficulty:** Medium (new UI component + state tracking)
**Time:** 3-4 hours

---

## 4. PREFERENCES PANEL IMPROVEMENTS — MEDIUM PRIORITY

### Current Issues
- No validation of IP format
- No description of what settings do
- No obvious "save" action (implicit autosave)
- No way to test settings without full connect

### Improvements

**File:** `addon/preferences.py` — Enhanced preferences
```python
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

    # NEW: Auto-reconnect settings
    auto_reconnect: bpy.props.BoolProperty(
        name="Auto-reconnect on disconnect",
        description="Automatically try to reconnect if connection drops",
        default=True,
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

    def draw(self, context):
        layout = self.layout

        # Info section
        layout.label(text="Configure your remote GPU server connection", icon="INFO")
        layout.separator()

        # Server section
        box = layout.box()
        box.label(text="SERVER ADDRESS", icon="URL")

        col = box.column(align=True)
        col.prop(self, "server_ip", text="IP Address")
        col.prop(self, "server_port", text="Port")

        # Help text
        sub = col.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="• Tailscale IP: 100.x.x.x", icon="BLANK1")
        sub.label(text="• Hostname: myserver.local or gpu.company.com", icon="BLANK1")
        sub.label(text="• Local network: 192.168.x.x", icon="BLANK1")

        # Validation
        if self.server_ip and not self._is_valid_ip(self.server_ip):
            col.label(text="⚠️ Invalid IP/hostname format", icon="ERROR")

        layout.separator()

        # Connection options
        box = layout.box()
        box.label(text="CONNECTION OPTIONS", icon="PREFERENCES")
        box.prop(self, "auto_reconnect")

        # Restore last good connection
        if self.last_good_ip and self.last_good_ip != self.server_ip:
            row = box.row()
            row.label(text=f"Last good: {self.last_good_ip}:{self.last_good_port}")
            row.operator("remotegpu.restore_last_good", text="Restore", icon="BACK")

        layout.separator()

        # Advanced section (collapsible in future)
        box = layout.box()
        box.label(text="ADVANCED", icon="PREFERENCES")

        # Currently just placeholders for future features
        sub = box.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="• Connection timeout: 10s", icon="BLANK1")
        sub.label(text="• Render timeout: 300s", icon="BLANK1")

        layout.separator()
        layout.label(text="Connection tests in Render panel →", icon="QUESTION")

    @staticmethod
    def _is_valid_ip(ip_str):
        import re
        ipv4_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        hostname_pattern = r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z]{2,}$'

        if re.match(ipv4_pattern, ip_str):
            parts = ip_str.split('.')
            return all(0 <= int(p) <= 255 for p in parts)
        return re.match(hostname_pattern, ip_str) is not None or '.' not in ip_str
```

**Add restore operator:**
```python
class REMOTEGPU_OT_restore_last_good(bpy.types.Operator):
    """Restore last known good server address"""
    bl_idname = "remotegpu.restore_last_good"
    bl_label = "Restore"
    bl_description = "Restore the last server address that worked"

    def execute(self, context):
        prefs = context.preferences.addons.get("addon").preferences
        prefs.server_ip = prefs.last_good_ip
        prefs.server_port = prefs.last_good_port
        self.report({"INFO"}, f"Restored: {prefs.last_good_ip}:{prefs.last_good_port}")
        return {"FINISHED"}
```

**Update connect operator to remember good connections:**
```python
class REMOTEGPU_OT_connect(bpy.types.Operator):
    def execute(self, context):
        prefs = context.preferences.addons.get("addon").preferences
        # ... connection logic ...

        if conn.connected:
            # Save as last good connection
            prefs.last_good_ip = prefs.server_ip
            prefs.last_good_port = prefs.server_port
            engine.RemoteRenderEngine._connection = conn
            self.report({"INFO"}, f"Connected — {conn.gpu_name}")
        else:
            # ... error handling ...
```

**Priority:** MEDIUM (nice-to-have, doesn't block core functionality)
**Difficulty:** Easy (mostly UI improvements)
**Time:** 1-2 hours

---

## 5. VISUAL POLISH & ICON CONSISTENCY — LOW PRIORITY

### Improvements

**Standardize icon usage:**

| Icon | Usage | Blender Equivalent |
|------|-------|-------------------|
| 🟢 Connected | Active state | `CHECKMARK` or highlight |
| 🔴 Error | Failed state | `ERROR` |
| ⚫ Idle | Not started | `BLANK1` + gray text |
| ⚙️ | Settings/config | `PREFERENCES` |
| 📡 | Network/connection | `URL` |
| ▶ | Play/start | `PLAY` |
| ⏸ | Pause | `PAUSE` |
| ✕ | Cancel | `CANCEL` |
| ⓘ | Info/help | `QUESTION` or `INFO` |
| ↻ | Refresh/reconnect | `FILE_REFRESH` |
| ✓ | Success | `CHECKMARK` |
| ⚠ | Warning | `ERROR` (orange in context) |

**Color coding hints (via icon + text styling):**

```python
# addon/ui_colors.py (NEW)
STATUS_COLORS = {
    "connected": ("CHECKMARK", (0.2, 0.8, 0.2, 1.0)),      # Green
    "connecting": ("TIME", (1.0, 0.8, 0.2, 1.0)),          # Yellow
    "error": ("ERROR", (1.0, 0.2, 0.2, 1.0)),              # Red
    "idle": ("BLANK1", (0.6, 0.6, 0.6, 1.0)),              # Gray
}
```

Note: Blender UI doesn't support direct color assignment to labels easily, so use icon + clear text instead.

**Spacing improvements:**

```python
# Use consistent spacing
layout.separator()        # After sections
layout.separator(factor=0.5)  # Small separator

# Use scale_y for button emphasis
row.scale_y = 1.5  # Primary action buttons
```

**Priority:** LOW (polish only, doesn't affect functionality)
**Difficulty:** Easy
**Time:** 30-45 minutes

---

## Implementation Roadmap

### Phase 1: Core Improvements (Days 1-2)
1. **Connection Status Panel** ✓ MUST-HAVE
   - Track elapsed time, latency, server version
   - Color-coded visual states
   - Add reconnect + copy address buttons
2. **Error Messages** ✓ HIGH
   - Friendly error mapping
   - Recovery step guidance

### Phase 2: Progress & Validation (Day 3)
3. **Render Progress UI** ✓ HIGH
   - Progress bar + time estimates
   - Cancel button
4. **Preferences Validation** ✓ MEDIUM
   - IP format check
   - Quick test button

### Phase 3: Polish (Optional)
5. **Visual consistency** ✓ LOW
   - Icon standardization
   - Spacing/grouping

---

## File Summary

| File | Changes | Priority |
|------|---------|----------|
| `addon/connection.py` | Add latency, version, elapsed time tracking | HIGH |
| `addon/preferences.py` | Redesign status panel, add validation | MUST-HAVE |
| `addon/operators.py` | Add reconnect, copy, quick test, cancel ops | HIGH |
| `addon/engine.py` | Track render state machine | HIGH |
| `addon/error_messages.py` | NEW: Error recovery mapping | HIGH |
| `addon/ui_progress.py` | NEW: Render progress panel | HIGH |
| `addon/ui_colors.py` | NEW: Color/icon constants (optional) | LOW |
| `server/server.py` | Include version/build in PONG | HIGH |

---

## Testing Checklist

### Connection Status Panel
- [ ] Display correctly when connected
- [ ] Update elapsed time in real-time
- [ ] Show correct GPU name and VRAM
- [ ] Display latency accurately
- [ ] Handle disconnection gracefully
- [ ] Show error state with recovery steps
- [ ] Copy address button works
- [ ] Reconnect button works

### Error Messages
- [ ] "Connection refused" shows helpful steps
- [ ] "Timeout" message is clear
- [ ] Recovery steps are actionable
- [ ] Error is logged to console for debugging

### Render Progress
- [ ] Progress panel appears during render
- [ ] Samples counter updates correctly
- [ ] Progress bar reflects completion %
- [ ] Time estimates are reasonable
- [ ] Cancel button stops render
- [ ] Panel hides when render completes

### Preferences
- [ ] IP validation prevents invalid input
- [ ] Quick test doesn't disconnect active connection
- [ ] Last good connection is remembered
- [ ] Help text is clear and helpful

---

## Before/After Comparison

### Connection Status
**Before:**
```
Connected
GPU: RTX 4090
VRAM Free: 23456 MB
```

**After:**
```
🟢 CONNECTED (3m 42s elapsed)
Server: ws://100.74.135.83:9876
Latency: 12ms
GPU: RTX 4090
VRAM: 23,456 MB free
Server v1.0.4 (b4)

[Reconnect] [Copy URL] [Info]
```

### Error Message
**Before:**
```
Error: Connection refused
```

**After:**
```
🔴 CONNECTION FAILED
Error: Server rejected connection

Recovery steps:
1. Check IP/port in Server Settings
2. Verify server is running
3. Ensure Tailscale is active (if remote)
4. Check firewall/network

[Try Again]
```

### Render Feedback
**Before:**
```
Rendering on remote GPU...
```

**After:**
```
RENDER IN PROGRESS
Status: Rendering
Samples: 64 / 128 (50%)
████████████████░░░░░░░░░░░░░░
Time elapsed: 2m 15s
Est. remaining: ~2m 10s
[Cancel Render]
```

---

## Notes & Open Questions

1. **Server API** — Current server doesn't include version/build in PONG. Consider making this optional (graceful fallback if missing).

2. **Network latency** — Currently no latency measurement. Could add via PING → PONG round-trip timing.

3. **Auto-reconnect** — Preferences include flag but not implemented. Worth doing in Phase 2.

4. **Render cancel** — Server must handle `render_cancel` message. Verify protocol supports this.

5. **Progress messages** — Server already sends `{"type": "progress", "samples_done": x, "samples_total": y}`. Ensure this is sent frequently (every sample or every N samples).

6. **Visual theming** — Blender's theming system is limited. Focus on icons + text clarity rather than background colors.

---

## References

- **Current addon version:** 1.0.4 (b4)
- **Server version:** Same versioning scheme
- **Protocol:** `shared/protocol.py` defines message types
- **Target Blender:** 4.0.0+

