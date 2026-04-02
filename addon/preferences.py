"""Addon preferences and UI panels for Remote GPU Render."""

import bpy
from bpy.props import StringProperty, IntProperty, IntVectorProperty, EnumProperty, FloatProperty, BoolProperty


class RemoteGPUPreferences(bpy.types.AddonPreferences):
    # bl_idname MUST match the actual addon folder name ("addon" when installed from ZIP)
    bl_idname = "addon"

    server_ip: StringProperty(
        name="Server IP",
        description="IP address of the render server (LAN like 192.168.x.x or Tailscale like 100.x.x.x)",
        default="192.168.1.100",
    )
    server_port: IntProperty(
        name="Port",
        description="WebSocket port of the render server",
        default=9876,
        min=1024,
        max=65535,
    )
    viewport_quality: IntProperty(
        name="Viewport JPEG Quality",
        description="JPEG quality for viewport frames (lower = faster, less bandwidth)",
        default=75,
        min=30,
        max=95,
    )
    network_mode: EnumProperty(
        name="Network",
        description="Local network or Tailscale/VPN",
        items=[
            ("LOCAL", "Local LAN (Ethernet/WiFi)", "Same network (lowest latency)"),
            ("TAILSCALE", "Tailscale/VPN", "Remote access via Tailscale or VPN"),
        ],
        default="LOCAL",
    )
    use_denoiser: BoolProperty(
        name="Enable Denoising",
        description="Use AI denoiser to reduce render noise",
        default=True,
    )
    denoiser_type: EnumProperty(
        name="Denoiser",
        description="Choose denoiser type (OptiX for RTX GPUs, OIDN fallback)",
        items=[
            ("OFF", "Off", "No denoising"),
            ("OIDN", "OIDN", "Intel Open Image Denoise (CPU, all GPUs)"),
            ("OPTIX", "OptiX", "NVIDIA OptiX (RTX GPUs only, faster)"),
        ],
        default="OPTIX",
    )
    denoiser_intensity: FloatProperty(
        name="Denoiser Intensity",
        description="Strength of denoising (0=off, 1=maximum)",
        default=0.8,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    auto_aov_creation: BoolProperty(
        name="Auto AOV Creation",
        description="Automatically create Normal/Albedo AOVs for denoising",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "network_mode", expand=True)
        layout.prop(self, "server_ip")
        layout.prop(self, "server_port")
        layout.separator()
        layout.prop(self, "viewport_quality", slider=True)

        # Show expected latency
        row = layout.row()
        row.alignment = 'CENTER'
        if self.network_mode == "LOCAL":
            row.label(text="Expected: 1-5ms (LAN)")
        else:
            row.label(text="Expected: 10-100ms (Tailscale)")


class REMOTEGPU_PT_connection_panel(bpy.types.Panel):
    bl_label = "Remote GPU"
    bl_idname = "REMOTEGPU_PT_connection"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "render"

    @classmethod
    def poll(cls, context):
        return context.engine == "REMOTE_CYCLES"

    def draw(self, context):
        layout = self.layout

        # Try to get preferences safely
        try:
            addon_name = __package__ if __package__ else "addon"
            addon = context.preferences.addons.get(addon_name)
            if not addon or not hasattr(addon, 'preferences') or addon.preferences is None:
                layout.label(text="Please enable the addon in preferences first", icon="ERROR")
                return
            prefs = addon.preferences
        except Exception as e:
            layout.label(text=f"Error loading preferences", icon="ERROR")
            return

        # Connection settings
        box = layout.box()
        box.label(text="Server Connection", icon="URL")
        row = box.row(align=True)
        try:
            row.prop(prefs, "server_ip", text="IP")
            row.prop(prefs, "server_port", text="Port")
        except:
            row.label(text="Preferences error - restart Blender")

        # Connect/disconnect button
        from . import engine
        conn = engine.RemoteRenderEngine._connection
        if conn is not None and conn.connected:
            row = box.row()
            row.operator("remotegpu.disconnect", text="Disconnect", icon="CANCEL")
            row.label(text="Connected", icon="CHECKMARK")

            # Show server status if available
            if conn.server_status:
                # Issue #8: Use .get() for safer dict access throughout
                status = conn.server_status
                col = box.column(align=True)
                col.scale_y = 0.9

                gpu_name = status.get("gpu_name")
                if gpu_name:
                    col.label(text=f"GPU: {gpu_name}")
                gpu_temp = status.get("gpu_temp")
                if gpu_temp:
                    col.label(text=f"Temp: {gpu_temp}C")
                vram_used = status.get("vram_used")
                if vram_used is not None:
                    total = status.get("vram_total", 24)
                    pct = int(100 * vram_used / total) if total > 0 else 0
                    col.label(text=f"VRAM: {vram_used}MB / {total}MB ({pct}%)")
        else:
            col = box.column()
            col.scale_y = 1.5
            col.operator("remotegpu.connect", text="CONNECT TO SERVER", icon="URL")
            row = box.row()
            row.label(text="Status: DISCONNECTED", icon="ERROR")

        # Network mode
        box = layout.box()
        box.label(text="Network", icon="NETWORK_BIND")
        row = box.row()
        row.prop(prefs, "network_mode", expand=True)

        # Quality settings
        box = layout.box()
        box.label(text="Rendering Quality", icon="SHADING_SOLID")
        col = box.column()
        col.prop(prefs, "viewport_quality", slider=True)
        sub = col.row()
        sub.scale_y = 0.8
        if prefs.viewport_quality < 50:
            sub.label(text="Fast (low bandwidth)", icon="TRIA_DOWN")
        elif prefs.viewport_quality < 80:
            sub.label(text="Balanced", icon="TRIA_RIGHT")
        else:
            sub.label(text="Quality (high bandwidth)", icon="TRIA_UP")

        # Denoising & GPU Backend
        box = layout.box()
        box.label(text="GPU Backend", icon="GPU")
        col = box.column()
        col.prop(prefs, "denoiser_type", expand=False)

        # Show denoising options if enabled
        if prefs.denoiser_type != "OFF":
            sub = col.column()
            sub.prop(prefs, "denoiser_intensity", slider=True)
            sub.prop(prefs, "auto_aov_creation")

            # Show OptiX info if applicable
            if prefs.denoiser_type == "OPTIX":
                sub = col.row()
                sub.scale_y = 0.85
                sub.label(text="RTX GPU denoising (fastest)", icon="INFO")
        else:
            sub = col.row()
            sub.scale_y = 0.85
            sub.label(text="No denoising", icon="INFO")

        # Show server GPU status and backend selection if connected
        if conn is not None and conn.connected and conn.server_status:
            status = conn.server_status
            if "gpu_name" in status:
                gpu_name = status["gpu_name"]
                col = box.column()
                col.separator()
                col.label(text=f"Server GPU: {gpu_name}", icon="GPU")
                if "gpu_memory" in status:
                    col.label(text=f"VRAM: {status['gpu_memory']}GB")

            # GPU backend selection (NVRHI-style multi-backend support)
            if "available_backends" in status:
                backends_box = layout.box()
                backends_box.label(text="GPU Backends", icon="DRIVER")
                available = status.get("available_backends", [])
                current = status.get("current_backend", "NONE")

                if available:
                    col = backends_box.column()
                    for backend_name in available:
                        row = col.row()
                        is_current = (backend_name == current)
                        icon = "CHECKMARK" if is_current else "BLANK1"
                        text_label = f"{backend_name}"
                        if is_current:
                            text_label += " (active)"

                        # Show device count if available
                        devices = status.get("backend_devices", [])
                        if devices and is_current:
                            vram_total = sum(d.get("vram_gb", 0) for d in devices)
                            if vram_total > 0:
                                text_label += f" - {len(devices)} GPU(s), {vram_total:.1f}GB"

                        row.label(text=text_label, icon=icon)
                        if not is_current:
                            op = row.operator(
                                "remotegpu.select_device",
                                text="Switch",
                                icon="PLAY"
                            )
                            op.backend = backend_name
                else:
                    backends_box.label(text="No GPU backends available", icon="ERROR")


class REMOTEGPU_OT_connect(bpy.types.Operator):
    bl_idname = "remotegpu.connect"
    bl_label = "Connect to Render Server"
    bl_description = "Connect to the remote GPU render server"

    def execute(self, context):
        from . import engine
        from .connection import Connection

        prefs = context.preferences.addons[__package__].preferences

        # Try to load TLS/API config from dev_config
        use_tls = False
        api_key = None
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from shared import dev_config
            use_tls = getattr(dev_config, 'USE_TLS', False)
            api_key = getattr(dev_config, 'API_KEY', None)
        except (ImportError, AttributeError):
            pass

        protocol = "wss" if use_tls else "ws"
        url = f"{protocol}://{prefs.server_ip}:{prefs.server_port}"

        # Issue #16: Close old connection before opening new one
        old_conn = engine.RemoteRenderEngine._connection
        if old_conn is not None:
            old_conn.close()
            engine.RemoteRenderEngine._connection = None

        try:
            conn = Connection(url, api_key=api_key, use_tls=use_tls)
            conn.connect()
            engine.RemoteRenderEngine._connection = conn
            self.report({"INFO"}, f"Connected to {prefs.server_ip}:{prefs.server_port}")
        except Exception as e:
            self.report({"ERROR"}, f"Connection failed: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}


class REMOTEGPU_OT_disconnect(bpy.types.Operator):
    bl_idname = "remotegpu.disconnect"
    bl_label = "Disconnect from Render Server"
    bl_description = "Disconnect from the remote GPU render server"

    def execute(self, context):
        from . import engine

        conn = engine.RemoteRenderEngine._connection
        if conn is not None:
            conn.close()
            engine.RemoteRenderEngine._connection = None
            self.report({"INFO"}, "Disconnected")

        return {"FINISHED"}


class REMOTEGPU_OT_select_device(bpy.types.Operator):
    bl_idname = "remotegpu.select_device"
    bl_label = "Select GPU Backend"
    bl_description = "Switch to a different GPU rendering backend (OptiX, CUDA, HIP, Vulkan)"

    backend: StringProperty(name="Backend", description="GPU backend name (OPTIX, CUDA, HIP, VULKAN)")

    def execute(self, context):
        from . import engine
        from .connection import Connection
        from shared.protocol import MsgType, pack_websocket

        conn = engine.RemoteRenderEngine._connection
        if conn is None or not conn.connected:
            self.report({"ERROR"}, "Not connected to render server")
            return {"CANCELLED"}

        if not self.backend:
            self.report({"ERROR"}, "No backend specified")
            return {"CANCELLED"}

        try:
            # Send RENDER_DEVICE_SELECT message to server
            msg_data = {
                "backend": self.backend.upper(),
                "device_idx": 0,  # Use first device of the backend
            }
            messages = pack_websocket(MsgType.RENDER_DEVICE_SELECT, msg_data)
            for msg in messages:
                conn.websocket.send(msg)

            self.report({"INFO"}, f"Requesting GPU backend: {self.backend}")
            return {"FINISHED"}

        except Exception as e:
            self.report({"ERROR"}, f"Failed to select backend: {e}")
            return {"CANCELLED"}
