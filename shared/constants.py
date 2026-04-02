"""Shared constants for blender-remote-gpu."""

VERSION = "0.1.0"

# Network
DEFAULT_PORT = 9876
MAX_MESSAGE_SIZE = 500 * 1024 * 1024  # 500 MB (large .blend files)
HEARTBEAT_INTERVAL = 5.0  # seconds (legacy name; see WS_* below)

# WebSocket tuning — Tailscale / cross-continent TLS can exceed defaults
WS_AUTH_FIRST_MESSAGE_TIMEOUT = 30.0  # server waits for client auth (seconds)
WS_CLIENT_OPEN_TIMEOUT = 60.0  # client TCP+TLS handshake (seconds)
WS_CLIENT_CONNECT_WAIT = 90.0  # addon connect() blocks until connected (seconds)
WS_PING_INTERVAL = 20.0  # application-level ping interval (seconds)
WS_PING_TIMEOUT = 90.0  # must exceed worst-case Tailscale RTT

# Rendering
VIEWPORT_JPEG_QUALITY = 75
VIEWPORT_UPDATE_INTERVAL = 0.05  # 50ms = ~20 FPS progressive updates
VIEWPORT_MAX_SAMPLES = 4096
FINAL_RENDER_FORMAT = "EXR"  # lossless

# Scene sync
FULL_SYNC_THRESHOLD = 1024 * 1024  # 1 MB delta triggers full re-sync instead

# Frame Buffering (Phase 11 — Bounded Ring Buffer)
FRAME_BUFFER_SIZE_MS = 500  # Capacity: 500ms worth of frames (~50 frames at 100fps)
FRAME_BUFFER_MAX_FRAMES = 50  # Absolute max frames in ring buffer
MAX_FRAMES_TO_DROP_PER_SECOND = 10  # Rate limit for buffer overflow logging
FRAME_STALE_THRESHOLD_MS = 500  # Skip frames received >500ms ago

# Issue #11: Constant validation — all must be > 0
assert DEFAULT_PORT > 0, "DEFAULT_PORT must be > 0"
assert MAX_MESSAGE_SIZE > 0, "MAX_MESSAGE_SIZE must be > 0"
assert HEARTBEAT_INTERVAL > 0, "HEARTBEAT_INTERVAL must be > 0"
assert WS_AUTH_FIRST_MESSAGE_TIMEOUT > 0, "WS_AUTH_FIRST_MESSAGE_TIMEOUT must be > 0"
assert WS_CLIENT_OPEN_TIMEOUT > 0, "WS_CLIENT_OPEN_TIMEOUT must be > 0"
assert WS_CLIENT_CONNECT_WAIT > 0, "WS_CLIENT_CONNECT_WAIT must be > 0"
assert WS_PING_INTERVAL > 0, "WS_PING_INTERVAL must be > 0"
assert WS_PING_TIMEOUT > 0, "WS_PING_TIMEOUT must be > 0"
assert VIEWPORT_JPEG_QUALITY > 0, "VIEWPORT_JPEG_QUALITY must be > 0"
assert VIEWPORT_UPDATE_INTERVAL > 0, "VIEWPORT_UPDATE_INTERVAL must be > 0"
assert VIEWPORT_MAX_SAMPLES > 0, "VIEWPORT_MAX_SAMPLES must be > 0"
assert FULL_SYNC_THRESHOLD > 0, "FULL_SYNC_THRESHOLD must be > 0"
assert FRAME_BUFFER_SIZE_MS > 0, "FRAME_BUFFER_SIZE_MS must be > 0"
assert FRAME_BUFFER_MAX_FRAMES > 0, "FRAME_BUFFER_MAX_FRAMES must be > 0"
assert MAX_FRAMES_TO_DROP_PER_SECOND > 0, "MAX_FRAMES_TO_DROP_PER_SECOND must be > 0"
assert FRAME_STALE_THRESHOLD_MS > 0, "FRAME_STALE_THRESHOLD_MS must be > 0"
