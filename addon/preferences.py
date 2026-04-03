"""UI panel for the Remote GPU addon. Shows connection status, IP/Port settings, validation, and progress."""

import bpy
import re
from bpy.props import StringProperty, IntProperty, BoolProperty

# UI/UX Improvement #5: Error mapping for user-friendly titles and recovery steps
ERROR_RECOVERY_MAP = {
    "Connection failed: [Errno 111] Connection refused": {
        "title": "Server not responding",
        "steps": [
            "1. Is the server running? Check status with 'systemctl status blender-gpu'",
            "2. Is Tailscale active? Run 'tailscale status' to verify",
            "3. Verify IP address and port are correct",
            "4. Check firewall: is port 9876 open?",
        ]
    },
    "Connection failed: [Errno 113] No route to host": {
        "title": "Network unreachable",
        "steps": [
            "1. Check network connectivity (ping the IP address)",
            "2. Verify Tailscale VPN is active on both machines",
            "3. Check if the IP address is correct",
            "4. Try connecting directly (not through VPN)",
        ]
    },
    "Connection timed out": {
        "title": "Connection timeout",
        "steps": [
            "1. Server may be slow or overloaded",
            "2. Check network latency (ping the IP address)",
            "3. Verify the server is responding to pings",
            "4. Try reconnecting after 30 seconds",
        ]
    },
    "Unexpected server response": {
        "title": "Server protocol error",
        "steps": [
            "1. Server version may be incompatible",
            "2. Try restarting the server",
            "3. Check addon and server versions match",
        ]
    },
    "Blender not found": {
        "title": "GPU machine doesn't have Blender",
        "steps": [
            "1. Install Blender 4.0+ on the server machine",
            "2. Use installer from blender.org",
            "3. Ensure Blender can be run from command line",
        ]
    },
    "Python error": {
        "title": "Server script error",
        "steps": [
            "1. Check server console for error details",
            "2. Verify Python 3.10+ is installed",
            "3. Check all required packages are available",
        ]
    },
}

def get_error_recovery(error_msg):
    """Look up friendly error recovery info."""
    for key, recovery in ERROR_RECOVERY_MAP.items():
        if key in error_msg:
            return recovery
    # Default fallback for unknown errors
    return {
        "title": "Connection error",
        "steps": ["1. Check server status", "2. Review server logs", "3. Try reconnecting"],
    }

def validate_ip(ip_str):
    """Validate IPv4 format. Returns (is_valid, error_message)."""
    if not ip_str or not isinstance(ip_str, str):
        return False, "IP address is empty"

    # Allow IPv4 format
    ipv4_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
    if re.match(ipv4_pattern, ip_str):
        # Check octets are 0-255
        octets = [int(x) for x in ip_str.split('.')]
        if all(0 <= octet <= 255 for octet in octets):
            return True, ""
        return False, "IP octets must be 0-255"

    # Allow localhost
    if ip_str.lower() == "localhost":
        return True, ""

    # Allow domain names (simplified check)
    domain_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'
    if re.match(domain_pattern, ip_str):
        return True, ""

    return False, f"Invalid IP format: {ip_str}"

def validate_port(port):
    """Validate port number. Returns (is_valid, error_message)."""
    if not isinstance(port, int):
        return False, "Port must be a number"
    if port < 1024:
        return False, f"Port {port} is reserved (use 1024-65535)"
    if port > 65535:
        return False, f"Port {port} is too high (max 65535)"
    return True, ""


class RemoteGPUPreferences(bpy.types.AddonPreferences):
    """Addon preferences with validation."""
    bl_idname = "addon"

    server_ip: StringProperty(
        name="Server IP",
        description="IP address of the remote GPU server (Tailscale or LAN)",
        default="100.74.135.83",
    )
    server_port: IntProperty(
        name="Port",
        description="WebSocket port (1024-65535)",
        default=9876,
        min=1024,
        max=65535,
    )
    # UI/UX Improvement #4: Remember last good connection
    remember_connection: BoolProperty(
        name="Remember last connection",
        description="Auto-restore the last working IP/port on startup",
        default=True,
    )
    last_good_ip: StringProperty(
        name="Last Good IP",
        description="Last successful connection IP (internal)",
        default="",
    )
    last_good_port: IntProperty(
        name="Last Good Port",
        description="Last successful connection port (internal)",
        default=0,
    )
    # UI/UX Improvement #4: Track last test status
    last_test_status: StringProperty(
        name="Last Test Status",
        description="Status of last quick test (internal)",
        default="",
    )

    def draw(self, context):
        layout = self.layout

        # Addon version header (UI/UX Improvement #5)
        try:
            from . import BUILD, BUILD_DATE
            header = layout.box()
            header.label(text=f"Remote GPU Addon v{BUILD} ({BUILD_DATE})", icon="PREFERENCES")
        except ImportError:
            pass

        # Server IP validation (UI/UX Improvement #4)
        box = layout.box()
        col = box.column(align=True)
        col.label(text="SERVER SETTINGS", icon="URL")

        row = col.row(align=True)
        row.prop(self, "server_ip", text="IP Address")

        # Validate IP format
        is_valid_ip, ip_error = validate_ip(self.server_ip)
        if not is_valid_ip and self.server_ip:
            row.label(text=ip_error, icon="ERROR")

        row = col.row(align=True)
        row.prop(self, "server_port", text="Port")

        # Validate port range
        is_valid_port, port_error = validate_port(self.server_port)
        if not is_valid_port:
            row.label(text=port_error, icon="ERROR")

        # Quick Test button (UI/UX Improvement #4)
        if is_valid_ip and is_valid_port:
            col.operator("remotegpu.quick_test", text="Test Connection", icon="FORWARD")
            if self.last_test_status:
                test_row = col.row()
                if "success" in self.last_test_status.lower():
                    test_row.label(text=self.last_test_status, icon="CHECKMARK")
                else:
                    test_row.label(text=self.last_test_status, icon="ERROR")

        # Remember connection toggle
        col.prop(self, "remember_connection")

        # Copy address button for debugging
        col.operator("remotegpu.copy_address", text="Copy Address to Clipboard", icon="COPYDOWN")


class REMOTEGPU_PT_panel(bpy.types.Panel):
    """Main render properties panel with improved connection status (Improvement #1)."""
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

        # Version header
        header_box = layout.box()
        header_box.label(text="Remote GPU Addon v1.0.4 (b4)", icon="RENDER_RESULT")

        # Get preferences
        try:
            addon = context.preferences.addons.get("addon")
            if not addon or not addon.preferences:
                layout.label(text="Enable addon in preferences first", icon="ERROR")
                return
            prefs = addon.preferences
        except Exception as e:
            layout.label(text=f"Preferences error: {e}", icon="ERROR")
            return

        # Get connection
        from . import engine
        conn = engine.RemoteRenderEngine._connection

        # IMPROVEMENT #1: Enhanced Connection Status Panel
        status_box = layout.box()
        status_box.label(text="CONNECTION STATUS", icon="OUTLINER_DATA_LIGHTPROBE")

        if conn and conn.connected:
            # Connection state: green checkmark
            status_col = status_box.column(align=True)

            # Status line with color-coded icon and elapsed time
            status_row = status_col.row(align=True)
            status_row.label(text="🟢 CONNECTED", icon="CHECKMARK")
            status_row.label(text=f"({conn.get_elapsed_time_str()} elapsed)")

            # Server address
            status_col.label(text=f"Server: ws://{prefs.server_ip}:{prefs.server_port}")

            # Latency info
            status_col.label(text=f"Latency: {conn.latency_ms}ms")

            # GPU info
            status_col.label(text=f"GPU: {conn.gpu_name}")

            # VRAM (formatted with commas)
            if conn.vram_free > 0:
                vram_str = f"{conn.vram_free:,} MB free" if conn.vram_free >= 1000 else f"{conn.vram_free} MB free"
                status_col.label(text=f"VRAM: {vram_str}")

            # Server version
            if conn.server_version or conn.server_build:
                version_str = f"Server v{conn.server_version}"
                if conn.server_build:
                    version_str += f" ({conn.server_build})"
                status_col.label(text=version_str)

            # Action buttons
            button_row = status_col.row(align=True)
            button_row.operator("remotegpu.connect", text="Reconnect", icon="FILE_REFRESH")
            button_row.operator("remotegpu.copy_address", text="Copy Address", icon="COPYDOWN")

            # Disconnect button
            status_col.operator("remotegpu.disconnect", text="Disconnect", icon="CANCEL")

        elif conn and conn.error:
            # IMPROVEMENT #2: Error Message Recovery - User-friendly error with recovery steps
            status_col = status_box.column(align=True)

            # Status line with error icon
            status_row = status_col.row()
            status_row.label(text="🔴 ERROR", icon="ERROR")

            # Get recovery info
            recovery = get_error_recovery(conn.error)
            status_col.label(text=recovery["title"], icon="INFO")

            # Show recovery steps (collapsed in a text area)
            status_col.label(text="Recovery steps:")
            for step in recovery["steps"]:
                status_col.label(text=step)

            # Reconnect button (instead of forcing disconnect/connect)
            status_col.operator("remotegpu.connect", text="Reconnect", icon="FILE_REFRESH")

            # Show technical details (collapsed by default)
            details_col = status_col.column(align=True)
            details_col.label(text=f"Technical: {conn.error}", icon="CONSOLE")

        else:
            # Not connected state
            status_col = status_box.column(align=True)
            status_row = status_col.row()
            status_row.label(text="⚫ DISCONNECTED", icon="RADIOBUT_OFF")

            # Connect button (large, prominent)
            status_col.scale_y = 1.5
            status_col.operator("remotegpu.connect", text="Connect to Server", icon="PLAY")

            status_col.scale_y = 1.0
            status_col.label(text=f"Target: ws://{prefs.server_ip}:{prefs.server_port}")

        # IMPROVEMENT #3: Render Progress UI (shown during render only)
        self._draw_render_progress(layout)

    def _draw_render_progress(self, layout):
        """Show render progress if render is in progress."""
        # This will be populated by the engine when rendering
        from . import engine
        if hasattr(engine, '_render_progress_data'):
            progress_data = engine._render_progress_data
            if progress_data and progress_data.get("active"):
                progress_box = layout.box()
                progress_box.label(text="RENDER PROGRESS", icon="RENDER_ANIMATION")

                col = progress_box.column(align=True)

                # Status message
                status = progress_data.get("status", "Rendering...")
                col.label(text=status)

                # Progress bar (simulated with labels for now)
                if "progress" in progress_data:
                    progress = progress_data["progress"]
                    col.label(text=f"Progress: {progress}%")

                # Samples
                if "samples_done" in progress_data and "samples_total" in progress_data:
                    col.label(text=f"Samples: {progress_data['samples_done']}/{progress_data['samples_total']}")

                # Elapsed time
                if "elapsed_time" in progress_data:
                    col.label(text=f"Elapsed: {progress_data['elapsed_time']}")

                # Estimated remaining (if available)
                if "estimated_remaining" in progress_data:
                    col.label(text=f"Est. remaining: {progress_data['estimated_remaining']}")

                # Cancel button
                col.operator("remotegpu.cancel_render", text="Cancel Render", icon="CANCEL")
