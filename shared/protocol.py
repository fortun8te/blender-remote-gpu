"""Message types for the Remote GPU protocol. JSON text frames + binary data frames."""

# Client -> Server
PING = "ping"
SCENE_UPLOAD = "scene_upload"
VIEWPORT_START = "viewport_start"
VIEWPORT_STOP = "viewport_stop"
RENDER_START = "render_start"
RENDER_CANCEL = "render_cancel"

# Server -> Client
PONG = "pong"
FRAME = "frame"
PROGRESS = "progress"
ERROR = "error"
SCENE_ACK = "scene_ack"

# Default config
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9876
TAILSCALE_SERVER_IP = "100.74.135.83"
TAILSCALE_CLIENT_IP = "100.119.65.49"
