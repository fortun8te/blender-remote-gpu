# Code Examples: Implementing the 5 Blocking Issues

This file contains copy-paste ready code snippets for each issue.

## Issue 1: Docker

### File: `Dockerfile`

```dockerfile
FROM nvidia/cuda:12.1-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y \
    wget bzip2 python3.11 python3.11-dev python3-pip git \
    && rm -rf /var/lib/apt/lists/*

# Download Blender (adjust version as needed)
RUN wget https://download.blender.org/release/Blender4.0/blender-4.0.1-linux-x64.tar.xz \
    && tar -xf blender-4.0.1-linux-x64.tar.xz \
    && cp blender-4.0.1-linux-x64/blender /usr/local/bin/ \
    && rm -rf blender-4.0.1-linux-x64*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server
COPY shared ./shared
COPY addon ./addon

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import websockets; import asyncio; asyncio.run(websockets.connect('ws://localhost:9876'))" || exit 1

ENV BLENDER_PATH=/usr/local/bin/blender GPU_BACKEND=optix RENDER_SERVER_PORT=9876 LOG_LEVEL=INFO
EXPOSE 9876
CMD ["python3", "server/server.py", "--port", "9876", "--blender", "/usr/local/bin/blender"]
```

---

## Issue 2: Network Resilience

### File: `addon/connection.py` (enhanced version)

```python
import asyncio
import websockets
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class RobustRemoteGPUConnection:
    """WebSocket connection with automatic retry and failover."""

    def __init__(self, server_ips: list, port: int, max_retries: int = 3):
        self.server_ips = server_ips
        self.port = port
        self.max_retries = max_retries
        self.ws = None
        self.active_server = None

    async def connect(self) -> bool:
        """Connect to server with automatic failover."""
        for server_ip in self.server_ips:
            if await self._connect_to_server(server_ip):
                self.active_server = server_ip
                logger.info(f"Connected to {server_ip}:{self.port}")
                return True
        logger.error(f"Failed to connect to any server: {self.server_ips}")
        return False

    async def _connect_to_server(self, server_ip: str) -> bool:
        """Try to connect to a specific server."""
        url = f"ws://{server_ip}:{self.port}"

        for attempt in range(self.max_retries):
            try:
                logger.info(f"Attempt {attempt + 1}/{self.max_retries} to {url}")
                self.ws = await asyncio.wait_for(
                    websockets.connect(url),
                    timeout=10.0
                )
                return True

            except asyncio.TimeoutError:
                logger.warning(f"Connection timeout to {url}")
            except (websockets.InvalidURI, ConnectionRefusedError, OSError) as e:
                logger.warning(f"Connection failed to {url}: {e}")

            if attempt < self.max_retries - 1:
                delay = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.info(f"Retrying in {delay}s...")
                await asyncio.sleep(delay)

        return False

    async def send_with_retry(self, msg_type, data, binary=None) -> bool:
        """Send message with automatic retry."""
        if self.ws is None or self.ws.closed:
            if not await self.connect():
                return False

        from shared.protocol import pack_websocket

        for attempt in range(self.max_retries):
            try:
                msg = pack_websocket(msg_type, data, binary)
                for part in msg:
                    await asyncio.wait_for(self.ws.send(part), timeout=30.0)
                return True

            except (websockets.ConnectionClosed, asyncio.TimeoutError) as e:
                logger.warning(f"Send failed (attempt {attempt + 1}): {e}")

                if attempt < self.max_retries - 1:
                    if await self.connect():
                        continue
                    else:
                        delay = 2 ** attempt
                        await asyncio.sleep(delay)

        return False

    async def receive_message(self):
        """Receive message with connection validation."""
        if self.ws is None or self.ws.closed:
            return None

        try:
            return await asyncio.wait_for(self.ws.recv(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.error("Receive timeout")
            return None
```

---

## Issue 3: Multi-Server Load Balancing

### File: `shared/server_registry.py`

```python
import asyncio
import time
import logging
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger(__name__)

@dataclass
class ServerInfo:
    """Information about a render server."""
    ip: str
    port: int
    last_health_check: float = 0
    is_healthy: bool = False
    queue_depth: int = 0
    vram_used_mb: int = 0

    def is_stale(self, max_age_s: float = 30.0) -> bool:
        return (time.time() - self.last_health_check) > max_age_s

class ServerRegistry:
    """Tracks available render servers with health checking."""

    def __init__(self):
        self.servers: dict = {}
        self.check_interval = 10.0
        self._health_check_task: Optional[asyncio.Task] = None

    def add_server(self, ip: str, port: int):
        """Add a server to the registry."""
        key = f"{ip}:{port}"
        self.servers[key] = ServerInfo(ip=ip, port=port)
        logger.info(f"Added server: {key}")

    def load_from_env(self, env_var: str = "RENDER_SERVERS"):
        """Load server list from environment variable."""
        import os
        server_list = os.getenv(env_var, "localhost:9876")
        for server_addr in server_list.split(","):
            if ":" in server_addr:
                ip, port = server_addr.rsplit(":", 1)
                self.add_server(ip.strip(), int(port.strip()))
            else:
                self.add_server(server_addr.strip(), 9876)

    async def health_check(self, server: ServerInfo) -> bool:
        """Check if server is healthy."""
        import websockets
        from shared.protocol import MsgType, pack_websocket

        url = f"ws://{server.ip}:{server.port}"
        try:
            async with asyncio.timeout(5.0):
                async with websockets.connect(url) as ws:
                    msg = pack_websocket(MsgType.PING, {})[0]
                    await ws.send(msg)
                    await ws.recv()

                    server.is_healthy = True
                    server.last_health_check = time.time()
                    logger.debug(f"Health check passed: {url}")
                    return True

        except (asyncio.TimeoutError, websockets.ConnectionClosed, OSError) as e:
            server.is_healthy = False
            server.last_health_check = time.time()
            logger.warning(f"Health check failed for {url}: {e}")
            return False

    async def start_health_monitor(self):
        """Start background health check loop."""
        self._health_check_task = asyncio.create_task(self._health_monitor_loop())

    async def _health_monitor_loop(self):
        """Periodically check server health."""
        while True:
            try:
                for server in self.servers.values():
                    if server.is_stale():
                        await self.health_check(server)
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Health monitor error: {e}")

    def get_best_server(self) -> Optional[ServerInfo]:
        """Return healthiest server with smallest queue."""
        healthy = [s for s in self.servers.values() if s.is_healthy]
        if not healthy:
            return None
        return min(healthy, key=lambda s: (s.queue_depth, s.vram_used_mb))

    def get_all_servers(self) -> List[ServerInfo]:
        """Return all servers, healthy ones first."""
        return sorted(
            self.servers.values(),
            key=lambda s: (not s.is_healthy, s.queue_depth)
        )
```

---

## Issue 4: Metrics & Monitoring

### File: `server/metrics.py`

```python
"""Prometheus metrics for monitoring."""

from prometheus_client import Counter, Gauge, Histogram, generate_latest

# Counters
frames_rendered = Counter(
    "gpu_frames_rendered_total",
    "Total frames rendered",
    ["render_type"]
)

renders_completed = Counter(
    "gpu_renders_completed_total",
    "Total renders completed"
)

renders_failed = Counter(
    "gpu_renders_failed_total",
    "Total renders failed",
    ["error_code"]
)

frames_dropped = Counter(
    "gpu_frames_dropped_total",
    "Total frames dropped due to buffer overflow"
)

# Gauges
gpu_vram_used_mb = Gauge(
    "gpu_vram_used_mb",
    "GPU VRAM in use (MB)"
)

gpu_vram_total_mb = Gauge(
    "gpu_vram_total_mb",
    "GPU VRAM total (MB)"
)

gpu_temperature_c = Gauge(
    "gpu_temperature_celsius",
    "GPU temperature (Celsius)"
)

connected_clients = Gauge(
    "gpu_connected_clients",
    "Number of connected clients"
)

render_queue_depth = Gauge(
    "gpu_render_queue_depth",
    "Renders waiting in queue"
)

# Histograms
frame_latency_ms = Histogram(
    "gpu_frame_latency_ms",
    "Frame render latency (milliseconds)",
    buckets=[10, 50, 100, 200, 500, 1000, 2000]
)

scene_upload_time_ms = Histogram(
    "gpu_scene_upload_time_ms",
    "Scene upload time (milliseconds)",
    buckets=[100, 500, 1000, 5000, 10000]
)

def get_metrics_text() -> str:
    """Return all metrics in Prometheus text format."""
    return generate_latest().decode('utf-8')
```

### File: `server/server.py` (integrate metrics)

```python
# In _handle_client():
async def handle_client(self, websocket):
    from server.metrics import connected_clients
    self.client_count += 1
    connected_clients.set(self.client_count)

    try:
        # ... existing code ...
    finally:
        self.client_count -= 1
        connected_clients.set(self.client_count)

# In _viewport_render_loop():
for samples in sample_steps:
    start = time.time()
    jpeg_data = self.live_viewport.render_frame(samples=samples)
    elapsed = time.time() - start

    from server.metrics import frame_latency_ms, frames_rendered
    frame_latency_ms.observe(elapsed * 1000)
    frames_rendered.labels(render_type="viewport").inc()

# Add metrics endpoint:
async def start_metrics_server(self, port: int = 8000):
    from aiohttp import web
    from server.metrics import get_metrics_text

    async def metrics_handler(request):
        return web.Response(text=get_metrics_text(), content_type='text/plain')

    app = web.Application()
    app.router.add_get('/metrics', metrics_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Metrics server on http://0.0.0.0:{port}/metrics")
```

---

## Issue 5: Systemd Service

### File: `/etc/systemd/system/blender-gpu-render.service`

```ini
[Unit]
Description=Blender Remote GPU Render Server
After=docker.service
Requires=docker.service

[Service]
Type=simple
WorkingDirectory=/opt/blender-remote-gpu
ExecStart=/usr/local/bin/docker-compose up
ExecStop=/usr/local/bin/docker-compose down

Restart=unless-stopped
RestartSec=10s

# Resource limits
MemoryLimit=24G
CPUQuota=80%

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=blender-gpu

User=render
Environment="PATH=/usr/local/bin:/usr/bin"

[Install]
WantedBy=multi-user.target
```

### Usage:

```bash
# Copy service file
sudo cp blender-gpu-render.service /etc/systemd/system/

# Enable service (starts on boot)
sudo systemctl enable blender-gpu-render.service

# Start service
sudo systemctl start blender-gpu-render.service

# Check status
sudo systemctl status blender-gpu-render.service

# View logs
sudo journalctl -u blender-gpu-render.service -f

# Stop service
sudo systemctl stop blender-gpu-render.service
```

---

## Summary: Files to Create/Copy

1. `Dockerfile` — Docker image definition
2. `docker-compose.yml` — Multi-server orchestration
3. `shared/server_registry.py` — Server discovery
4. `server/metrics.py` — Prometheus metrics
5. `/etc/systemd/system/blender-gpu-render.service` — Auto-restart

Then modify:
- `addon/connection.py` — Add RobustRemoteGPUConnection
- `addon/preferences.py` — Add server list
- `server/server.py` — Integrate metrics + systemd
- `requirements.txt` — Add prometheus_client

**Total effort:** 2-3 weeks for 1 developer
