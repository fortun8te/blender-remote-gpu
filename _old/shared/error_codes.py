"""Error code definitions for the Blender Remote GPU system.

All error codes follow a pattern: CATEGORY_NUMBER
- CONN_* = Connection errors
- RENDER_* = Render pipeline errors
- SYNC_* = Scene synchronization errors
- SCENE_* = Scene loading/validation errors
- GPU_* = GPU/hardware errors
- ENCODE_* = Encoding/decoding errors
- PROTO_* = Protocol/communication errors
- SYS_* = System/environment errors
- AUTH_* = Authentication/permissions errors
- PERF_* = Performance/timeout errors

Each error code includes:
- code: unique identifier (e.g., "CONN_001")
- category: logical grouping
- severity: CRITICAL, ERROR, WARNING, INFO
- message: technical description
- user_message: user-friendly description
- recovery_suggestions: list of things to try
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class ErrorCode:
    """Represents a single error code with metadata."""
    code: str
    category: str
    severity: str  # CRITICAL, ERROR, WARNING, INFO
    message: str
    user_message: str
    recovery_suggestions: List[str]

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ErrorCodes:
    """Registry of all error codes in the system."""

    # ============================================================================
    # CONNECTION ERRORS (CONN_*)
    # ============================================================================

    CONN_001 = ErrorCode(
        code="CONN_001",
        category="connection",
        severity="CRITICAL",
        message="Connection timeout while connecting to render server",
        user_message="Could not reach the render server within 10 seconds",
        recovery_suggestions=[
            "Check that the server IP and port are correct",
            "Verify the Windows machine with GPU is powered on and running the server",
            "Check network connectivity (ping the server IP)",
            "Ensure firewall isn't blocking port 9876",
            "Check server logs for startup errors",
        ]
    )

    CONN_002 = ErrorCode(
        code="CONN_002",
        category="connection",
        severity="CRITICAL",
        message="Connection refused (server not listening)",
        user_message="The server rejected the connection",
        recovery_suggestions=[
            "Ensure the render server is running: `python server.py`",
            "Check that the port (default 9876) is not in use",
            "Verify the server is listening on the correct network interface",
            "Check server startup logs for errors",
        ]
    )

    CONN_003 = ErrorCode(
        code="CONN_003",
        category="connection",
        severity="CRITICAL",
        message="Connection lost (disconnected during operation)",
        user_message="Connection to render server was lost",
        recovery_suggestions=[
            "Check network connectivity",
            "Look for server crashes in server logs",
            "Ensure the server process is still running",
            "Try reconnecting after 5 seconds",
            "Check for network latency or packet loss",
        ]
    )

    CONN_004 = ErrorCode(
        code="CONN_004",
        category="connection",
        severity="ERROR",
        message="Invalid connection URL format",
        user_message="The server address is not in valid format",
        recovery_suggestions=[
            "Check the server IP format (should be like 192.168.1.100:9876)",
            "Ensure you're using ws:// protocol (not wss://)",
            "Remove any trailing slashes from the URL",
        ]
    )

    CONN_005 = ErrorCode(
        code="CONN_005",
        category="connection",
        severity="ERROR",
        message="WebSocket upgrade failed",
        user_message="Could not establish WebSocket connection",
        recovery_suggestions=[
            "Verify the server is a valid WebSocket server",
            "Check server logs for WebSocket errors",
            "Ensure network allows WebSocket connections",
            "Try using a different port if 9876 is blocked",
        ]
    )

    CONN_006 = ErrorCode(
        code="CONN_006",
        category="connection",
        severity="WARNING",
        message="Heartbeat/ping timeout (no response from server)",
        user_message="Server is not responding to heartbeat",
        recovery_suggestions=[
            "Server may be busy rendering - wait for current render to complete",
            "Check network for high latency or packet loss",
            "Verify server is still running",
            "Reconnect if problem persists",
        ]
    )

    # ============================================================================
    # SCENE SYNC ERRORS (SYNC_*)
    # ============================================================================

    SYNC_001 = ErrorCode(
        code="SYNC_001",
        category="sync",
        severity="CRITICAL",
        message="Scene data is corrupt or incomplete",
        user_message="The scene file was corrupted during transfer",
        recovery_suggestions=[
            "Save your Blender file (Ctrl+S)",
            "Try uploading the scene again",
            "Check that your Blender file is not too large (max 500MB)",
            "Ensure no other applications are using high bandwidth",
        ]
    )

    SYNC_002 = ErrorCode(
        code="SYNC_002",
        category="sync",
        severity="ERROR",
        message="Required texture not found on server",
        user_message="A texture used in the scene could not be found",
        recovery_suggestions=[
            "Pack all textures into the .blend file (File > External Data > Pack All)",
            "Ensure all texture paths are relative, not absolute",
            "Check that texture files exist and are readable",
            "Re-save the project after packing textures",
        ]
    )

    SYNC_003 = ErrorCode(
        code="SYNC_003",
        category="sync",
        severity="ERROR",
        message="Scene too large for server (file size exceeds limit)",
        user_message="Scene file is too large to process",
        recovery_suggestions=[
            "Optimize the scene (delete unused objects/materials)",
            "Use linked/appended scenes instead of duplicating",
            "Reduce texture resolution or use compressed formats",
            "Split large scenes into smaller sub-scenes",
            "Check scene statistics in Blender (Shift+F10)",
        ]
    )

    SYNC_004 = ErrorCode(
        code="SYNC_004",
        category="sync",
        severity="ERROR",
        message="Server failed to load scene file",
        user_message="The render server could not open the scene",
        recovery_suggestions=[
            "Verify your Blender file is not corrupted (open it locally first)",
            "Check that Blender version on server matches addon version",
            "Look for specific errors in server logs",
            "Try saving the file as a fresh .blend",
        ]
    )

    SYNC_005 = ErrorCode(
        code="SYNC_005",
        category="sync",
        severity="WARNING",
        message="Scene is missing some dependencies but can still render",
        user_message="Some scene elements are missing but render will proceed",
        recovery_suggestions=[
            "Check Blender console for missing library messages",
            "Verify all linked files are available on the server",
            "Consider baking out dependent elements",
        ]
    )

    SYNC_006 = ErrorCode(
        code="SYNC_006",
        category="sync",
        severity="ERROR",
        message="Scene delta (incremental update) is invalid",
        user_message="Could not apply scene changes to server",
        recovery_suggestions=[
            "Perform a full scene resync",
            "Avoid rapid changes while syncing",
            "Check for concurrent edits in Blender",
        ]
    )

    # ============================================================================
    # RENDER PIPELINE ERRORS (RENDER_*)
    # ============================================================================

    RENDER_001 = ErrorCode(
        code="RENDER_001",
        category="render",
        severity="CRITICAL",
        message="Scene load failed on render server",
        user_message="The server failed to load your scene",
        recovery_suggestions=[
            "Check scene for errors (Ctrl+Alt+F12 for debug console)",
            "Verify all objects have valid geometry",
            "Check that all modifiers are compatible with Cycles",
            "Try rendering a simple cube first to verify setup",
            "Review server logs for specific errors",
        ]
    )

    RENDER_002 = ErrorCode(
        code="RENDER_002",
        category="render",
        severity="CRITICAL",
        message="GPU not available (out of VRAM or no device found)",
        user_message="GPU is not available or out of memory",
        recovery_suggestions=[
            "Close other applications using GPU (games, video editors, etc.)",
            "Check GPU temperature (may be throttling)",
            "Reduce render resolution or sample count",
            "Enable 'Use Unified Memory' in Cycles settings",
            "Check that Blender can detect GPU (should show 'NVIDIA' or 'AMD' in render properties)",
            "On Windows, check Device Manager for GPU driver issues",
        ]
    )

    RENDER_003 = ErrorCode(
        code="RENDER_003",
        category="render",
        severity="CRITICAL",
        message="Render timeout (no progress for too long)",
        user_message="Render is taking too long",
        recovery_suggestions=[
            "Reduce sample count (currently set very high?)",
            "Lower render resolution",
            "Simplify the scene (reduce geometry, disable complex effects)",
            "Check GPU isn't being used by other processes",
            "Increase timeout limit in settings if needed",
            "Check server GPU temperature and usage with nvidia-smi",
        ]
    )

    RENDER_004 = ErrorCode(
        code="RENDER_004",
        category="render",
        severity="ERROR",
        message="Render cancelled by user",
        user_message="You cancelled the render",
        recovery_suggestions=[
            "Adjust render settings and try again",
            "Check settings in Render Properties panel",
        ]
    )

    RENDER_005 = ErrorCode(
        code="RENDER_005",
        category="render",
        severity="ERROR",
        message="Invalid render settings (samples, resolution, etc.)",
        user_message="Render settings are invalid or missing",
        recovery_suggestions=[
            "Check Render Properties panel",
            "Ensure resolution is at least 1x1 pixel",
            "Ensure sample count is positive",
            "Reset render settings to defaults (if corrupted)",
        ]
    )

    RENDER_006 = ErrorCode(
        code="RENDER_006",
        category="render",
        severity="ERROR",
        message="Render engine mismatch (addon uses Cycles, server uses Eevee, etc.)",
        user_message="Render engine settings don't match",
        recovery_suggestions=[
            "Ensure both Blender instances use 'Cycles' render engine",
            "Check Render Engine dropdown in Render Properties",
        ]
    )

    RENDER_007 = ErrorCode(
        code="RENDER_007",
        category="render",
        severity="WARNING",
        message="Render completed but with lower quality than requested",
        user_message="Render completed but may not match your settings",
        recovery_suggestions=[
            "Check if GPU ran out of VRAM (would complete early)",
            "Verify render settings in Render Properties",
            "Try with lower resolution or samples first",
        ]
    )

    # ============================================================================
    # SCENE VALIDATION ERRORS (SCENE_*)
    # ============================================================================

    SCENE_001 = ErrorCode(
        code="SCENE_001",
        category="scene",
        severity="ERROR",
        message="Scene contains invalid geometry (non-manifold, self-intersecting, etc.)",
        user_message="Scene has geometry problems",
        recovery_suggestions=[
            "Select all objects in Edit Mode and run: Mesh > Clean Up > Remove Doubles",
            "Run: Mesh > Validate Mesh",
            "Check for non-manifold edges: Shift+Alt+M (select non-manifold)",
            "Use modifier: Corrective Smooth or Remesh to fix geometry",
        ]
    )

    SCENE_002 = ErrorCode(
        code="SCENE_002",
        category="scene",
        severity="ERROR",
        message="Scene references missing external libraries or files",
        user_message="Scene is missing files or libraries",
        recovery_suggestions=[
            "Use File > External Data > Pack All to embed everything",
            "Check for broken library links in Outliner",
            "Ensure all .blend library files exist and are readable",
        ]
    )

    SCENE_003 = ErrorCode(
        code="SCENE_003",
        category="scene",
        severity="WARNING",
        message="Scene uses unsupported features (CUDA, OptiX, etc.)",
        user_message="Some features may not render correctly on the server",
        recovery_suggestions=[
            "Check server GPU type and switch to compatible device",
            "Review Cycles render settings for GPU compatibility",
            "Some features may need to fall back to CPU",
        ]
    )

    SCENE_004 = ErrorCode(
        code="SCENE_004",
        category="scene",
        severity="ERROR",
        message="Scene has invalid or missing camera",
        user_message="Scene needs a valid camera",
        recovery_suggestions=[
            "Add a camera: Shift+A > Camera",
            "Set as active camera: Object > Set as Active Camera",
            "Verify camera is visible (eye icon in outliner)",
        ]
    )

    # ============================================================================
    # GPU/HARDWARE ERRORS (GPU_*)
    # ============================================================================

    GPU_001 = ErrorCode(
        code="GPU_001",
        category="gpu",
        severity="CRITICAL",
        message="GPU out of VRAM",
        user_message="GPU does not have enough memory for this render",
        recovery_suggestions=[
            "Close other GPU-intensive applications",
            "Reduce render resolution",
            "Reduce sample count",
            "Simplify the scene (reduce geometry, materials, textures)",
            "Use Unified Memory mode if available",
            "Check current GPU load with nvidia-smi (Windows) or Activity Monitor (Mac)",
        ]
    )

    GPU_002 = ErrorCode(
        code="GPU_002",
        category="gpu",
        severity="CRITICAL",
        message="GPU driver error or crash",
        user_message="The GPU encountered an error",
        recovery_suggestions=[
            "Update GPU drivers to latest version",
            "Restart the server",
            "Check server logs for GPU error codes",
            "Reboot Windows machine if driver recovery fails",
            "On Windows: restart NVIDIA Driver Daemon",
        ]
    )

    GPU_003 = ErrorCode(
        code="GPU_003",
        category="gpu",
        severity="WARNING",
        message="GPU is thermal throttling (too hot)",
        user_message="GPU is overheating and performance is reduced",
        recovery_suggestions=[
            "Stop rendering and let GPU cool down (5-10 minutes)",
            "Improve case ventilation",
            "Check GPU fan speed and operation",
            "Clean dust from GPU heatsink",
            "Verify ambient temperature is not too high",
        ]
    )

    GPU_004 = ErrorCode(
        code="GPU_004",
        category="gpu",
        severity="CRITICAL",
        message="GPU device not found or disabled",
        user_message="GPU is not available",
        recovery_suggestions=[
            "Check that GPU is properly seated in PCIe slot",
            "Verify GPU is enabled in BIOS",
            "Check Device Manager (Windows) for GPU driver status",
            "Reinstall GPU drivers",
            "Try different PCIe slot if available",
        ]
    )

    GPU_005 = ErrorCode(
        code="GPU_005",
        category="gpu",
        severity="WARNING",
        message="GPU memory fragmentation (slower access)",
        user_message="GPU memory is fragmented, performance may be reduced",
        recovery_suggestions=[
            "Restart the server to clear GPU memory",
            "Restart Blender to reset context",
        ]
    )

    # ============================================================================
    # ENCODING/DECODING ERRORS (ENCODE_*)
    # ============================================================================

    ENCODE_001 = ErrorCode(
        code="ENCODE_001",
        category="encoding",
        severity="ERROR",
        message="Failed to decode JPEG frame",
        user_message="Received frame is corrupted or incompatible",
        recovery_suggestions=[
            "Check network stability (may be packet loss)",
            "Reduce frame size or compression ratio",
            "Restart render if isolated incident",
        ]
    )

    ENCODE_002 = ErrorCode(
        code="ENCODE_002",
        category="encoding",
        severity="ERROR",
        message="Failed to decode EXR (final render) frame",
        user_message="Render result is corrupted",
        recovery_suggestions=[
            "Check network stability",
            "Try rendering again",
            "Check server logs for EXR save errors",
        ]
    )

    ENCODE_003 = ErrorCode(
        code="ENCODE_003",
        category="encoding",
        severity="ERROR",
        message="Failed to encode .blend file to bytes",
        user_message="Could not prepare scene file for sending",
        recovery_suggestions=[
            "Save the Blender file fresh (Ctrl+S)",
            "Check that file is not read-only",
            "Verify sufficient disk space available",
            "Try File > Recover > Auto Save",
        ]
    )

    ENCODE_004 = ErrorCode(
        code="ENCODE_004",
        category="encoding",
        severity="ERROR",
        message="Unsupported image format or codec",
        user_message="Image format is not supported",
        recovery_suggestions=[
            "Check that render output format is JPEG or PNG",
            "Avoid exotic file formats",
        ]
    )

    # ============================================================================
    # PROTOCOL/COMMUNICATION ERRORS (PROTO_*)
    # ============================================================================

    PROTO_001 = ErrorCode(
        code="PROTO_001",
        category="protocol",
        severity="ERROR",
        message="Protocol version mismatch (addon vs server)",
        user_message="Addon and server are incompatible versions",
        recovery_suggestions=[
            "Ensure addon version matches server version",
            "Update both to the same release",
            "Check GitHub for latest version",
        ]
    )

    PROTO_002 = ErrorCode(
        code="PROTO_002",
        category="protocol",
        severity="ERROR",
        message="Invalid message format received",
        user_message="Server sent invalid data",
        recovery_suggestions=[
            "Check server logs for errors",
            "Restart server",
            "Reconnect from Blender",
        ]
    )

    PROTO_003 = ErrorCode(
        code="PROTO_003",
        category="protocol",
        severity="ERROR",
        message="Message size exceeds maximum allowed",
        user_message="Data is too large to send",
        recovery_suggestions=[
            "Reduce scene size or file size",
            "Compress textures",
            "Split scene into smaller parts",
        ]
    )

    PROTO_004 = ErrorCode(
        code="PROTO_004",
        category="protocol",
        severity="ERROR",
        message="Msgpack serialization failed",
        user_message="Internal communication error",
        recovery_suggestions=[
            "Restart Blender and server",
            "Check system logs for memory issues",
        ]
    )

    # ============================================================================
    # SYSTEM/ENVIRONMENT ERRORS (SYS_*)
    # ============================================================================

    SYS_001 = ErrorCode(
        code="SYS_001",
        category="system",
        severity="CRITICAL",
        message="Required Python dependency not installed (websockets, msgpack, etc.)",
        user_message="System is missing required software",
        recovery_suggestions=[
            "Install dependencies: pip install websockets msgpack numpy pillow",
            "Check that Blender's Python is being used",
            "Verify pip is installed for the correct Python version",
        ]
    )

    SYS_002 = ErrorCode(
        code="SYS_002",
        category="system",
        severity="ERROR",
        message="Blender not found at specified path",
        user_message="Cannot find Blender executable",
        recovery_suggestions=[
            "Check 'Blender Path' in Remote GPU settings",
            "Ensure Blender is installed and path is correct",
            "Use 'blender' if in PATH or full absolute path",
        ]
    )

    SYS_003 = ErrorCode(
        code="SYS_003",
        category="system",
        severity="ERROR",
        message="Insufficient disk space on server",
        user_message="Server is out of disk space",
        recovery_suggestions=[
            "Delete old render outputs or temporary files",
            "Check disk usage with `df -h` or Disk Manager",
            "Clear temporary directory",
        ]
    )

    SYS_004 = ErrorCode(
        code="SYS_004",
        category="system",
        severity="ERROR",
        message="System memory (RAM) exhausted",
        user_message="System is out of memory",
        recovery_suggestions=[
            "Close other applications",
            "Reduce scene complexity",
            "Reduce render resolution or samples",
            "Restart server to clear memory leaks",
        ]
    )

    SYS_005 = ErrorCode(
        code="SYS_005",
        category="system",
        severity="WARNING",
        message="System is under heavy load",
        user_message="System performance is degraded",
        recovery_suggestions=[
            "Close background applications",
            "Reduce number of parallel renders",
            "Wait for system to cool down",
        ]
    )

    # ============================================================================
    # PERFORMANCE/TIMEOUT ERRORS (PERF_*)
    # ============================================================================

    PERF_001 = ErrorCode(
        code="PERF_001",
        category="performance",
        severity="WARNING",
        message="Slow network speed detected",
        user_message="Network is slow, transfers will take longer",
        recovery_suggestions=[
            "Check network bandwidth and latency (ping server)",
            "Move closer to router or use Ethernet",
            "Check for other bandwidth-heavy applications",
        ]
    )

    PERF_002 = ErrorCode(
        code="PERF_002",
        category="performance",
        severity="WARNING",
        message="High latency detected (>500ms)",
        user_message="Network latency is high",
        recovery_suggestions=[
            "Check ping to server (should be <100ms)",
            "Verify WiFi signal strength",
            "Use Ethernet cable instead of WiFi",
            "Check for packet loss with ping -c 100 <ip>",
        ]
    )

    PERF_003 = ErrorCode(
        code="PERF_003",
        category="performance",
        severity="ERROR",
        message="Viewport frame rate too low (<1 fps)",
        user_message="Viewport rendering is too slow",
        recovery_suggestions=[
            "Reduce viewport resolution or denoising",
            "Simplify scene for viewport",
            "Close other render operations",
            "Check network bandwidth",
        ]
    )

    PERF_004 = ErrorCode(
        code="PERF_004",
        category="performance",
        severity="WARNING",
        message="Frame encoding overhead very high (>50% of frame time)",
        user_message="Encoding frames is slow",
        recovery_suggestions=[
            "Reduce frame compression quality",
            "Use hardware-accelerated encoding if available",
            "Check GPU load with nvidia-smi",
        ]
    )

    # ============================================================================
    # AUTHENTICATION/PERMISSIONS ERRORS (AUTH_*)
    # ============================================================================

    AUTH_001 = ErrorCode(
        code="AUTH_001",
        category="auth",
        severity="ERROR",
        message="Permission denied (cannot read/write file)",
        user_message="Access to file was denied",
        recovery_suggestions=[
            "Check file permissions (chmod 644 on Unix)",
            "Ensure running user has permission",
            "Try running as administrator (Windows)",
            "Check file is not locked by another process",
        ]
    )

    AUTH_002 = ErrorCode(
        code="AUTH_002",
        category="auth",
        severity="ERROR",
        message="Addon not installed or not enabled",
        user_message="Remote GPU addon is not enabled",
        recovery_suggestions=[
            "Go to Edit > Preferences > Add-ons",
            "Search for 'Remote GPU'",
            "Enable the addon (checkbox)",
            "Restart Blender if needed",
        ]
    )

    AUTH_003 = ErrorCode(
        code="AUTH_003",
        category="auth",
        severity="ERROR",
        message="Server rejected connection (authentication failed)",
        user_message="Server rejected your connection",
        recovery_suggestions=[
            "Check firewall rules on server",
            "Verify IP is whitelisted (if using whitelist)",
            "Restart server",
        ]
    )

    # ============================================================================
    # SUCCESS/INFO MESSAGES
    # ============================================================================

    INFO_001 = ErrorCode(
        code="INFO_001",
        category="info",
        severity="INFO",
        message="Connected to server successfully",
        user_message="Successfully connected to render server",
        recovery_suggestions=[]
    )

    INFO_002 = ErrorCode(
        code="INFO_002",
        category="info",
        severity="INFO",
        message="Scene synced successfully",
        user_message="Scene uploaded to server",
        recovery_suggestions=[]
    )

    INFO_003 = ErrorCode(
        code="INFO_003",
        category="info",
        severity="INFO",
        message="Render completed successfully",
        user_message="Render finished",
        recovery_suggestions=[]
    )

    @classmethod
    def get_all_codes(cls) -> dict[str, ErrorCode]:
        """Return all error codes as a dict."""
        codes = {}
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name)
            if isinstance(attr, ErrorCode):
                codes[attr.code] = attr
        return codes

    @classmethod
    def by_code(cls, code: str) -> Optional[ErrorCode]:
        """Look up an error code by its string ID."""
        return cls.get_all_codes().get(code)

    @classmethod
    def by_category(cls, category: str) -> List[ErrorCode]:
        """Get all error codes in a category."""
        return [ec for ec in cls.get_all_codes().values() if ec.category == category]
