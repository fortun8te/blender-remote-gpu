"""Development configuration - hardcoded for fast iteration.

Set these once, addon auto-connects on startup. No UI fiddling needed.
"""

# Tailscale IPs (VPN-based, works anywhere)
REMOTE_SERVER_IP = "100.74.135.83"  # Windows (degitaar) Tailscale IP
REMOTE_SERVER_PORT = 9876
USE_TAILSCALE = True

# Mac Tailscale IP (for server to know client)
LOCAL_CLIENT_IP = "100.119.65.49"  # macbook-pro-van-michael

# Auto-connect on Blender startup
AUTO_CONNECT = True
AUTO_RECONNECT = True
RECONNECT_MAX_RETRIES = 10
RECONNECT_BACKOFF_MS = 500

# Render settings (change anytime without restart)
DEFAULT_RENDER_ENGINE = "REMOTE_CYCLES"
DEFAULT_SAMPLES = 128
DEFAULT_DENOISER = "OPTIX"  # "OFF", "OIDN", "OPTIX"
DEFAULT_DENOISER_INTENSITY = 0.8

# Viewport settings
VIEWPORT_QUALITY = 75  # JPEG quality 40-95 (user can adjust in UI)
VIEWPORT_FPS = 30  # Target frame rate
AUTO_UPDATE_VIEWPORT = True

# Compression (progressive JPEG tiles)
USE_PROGRESSIVE_JPEG = True
JPEG_TILE_SIZE = 64  # Send 64x64 tiles top-to-bottom
JPEG_QUALITY_ADAPTIVE = True  # Auto-scale quality based on network

# GPU backend
PREFERRED_GPU_BACKEND = "OPTIX"  # "OPTIX", "CUDA", "HIP", "VULKAN"

# Security & Network
USE_TLS = True
API_KEY = "sk-render-dev-2026"  # Change in production
VERIFY_SERVER_CERT = False  # Set True in production with proper certs

# Monitoring
SHOW_LATENCY_DISPLAY = True
LATENCY_UPDATE_INTERVAL_MS = 100
LOG_NETWORK_STATS = True

# Development mode
DEV_MODE = True  # Show debug info, verbose logging
HOT_RELOAD = True  # Auto-reload addon on file changes (Blender 4.1+)
VERBOSE_LOGGING = True

print(f"[RemoteGPU] Dev config loaded: Tailscale {REMOTE_SERVER_IP}:{REMOTE_SERVER_PORT}")
