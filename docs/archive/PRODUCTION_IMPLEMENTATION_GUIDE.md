# Production Implementation Guide: Step-by-Step

This guide walks through implementing the 5 blocking issues in priority order.

---

## Priority 1: Docker Containerization (1-2 Days)

### Why This First?
- Makes testing locally repeatable
- Enables deployment to any machine (Windows, Linux, macOS)
- Auto-restart on crash
- Foundation for scaling (easier to add more containers)

### Step 1.1: Create Dockerfile

**File:** `/Dockerfile`

```dockerfile
# Use NVIDIA CUDA base image for GPU support
FROM nvidia/cuda:12.1-runtime-ubuntu22.04

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    bzip2 \
    python3.11 \
    python3.11-dev \
    python3-pip \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Blender (use specific version for reproducibility)
RUN wget https://download.blender.org/release/Blender4.0/blender-4.0.1-linux-x64.tar.xz && \
    tar -xf blender-4.0.1-linux-x64.tar.xz && \
    mv blender-4.0.1-linux-x64/blender /usr/local/bin/blender && \
    rm -rf blender-4.0.1-linux-x64 blender-4.0.1-linux-x64.tar.xz

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY server ./server
COPY shared ./shared
COPY addon ./addon

# Health check (server must respond to ping within 5s)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import websockets; import asyncio; asyncio.run(websockets.connect('ws://localhost:9876'))" || exit 1

# Environment variables (can be overridden at runtime)
ENV BLENDER_PATH=/usr/local/bin/blender
ENV GPU_BACKEND=optix
ENV RENDER_SERVER_PORT=9876
ENV LOG_LEVEL=INFO

# Expose the server port
EXPOSE 9876

# Run the server
CMD ["python3", "server/server.py", \
     "--port", "$RENDER_SERVER_PORT", \
     "--blender", "$BLENDER_PATH", \
     "--log-level", "$LOG_LEVEL"]
```

### Step 1.2: Create docker-compose.yml

**File:** `/docker-compose.yml`

```yaml
version: '3.9'

services:
  # Primary render server (GPU 0)
  gpu-server-1:
    image: blender-remote-gpu:latest
    build:
      context: .
      dockerfile: Dockerfile
    container_name: gpu-server-1

    # GPU access
    runtime: nvidia
    environment:
      NVIDIA_VISIBLE_DEVICES: all
      NVIDIA_DRIVER_CAPABILITIES: compute,utility
      BLENDER_PATH: /usr/local/bin/blender
      GPU_BACKEND: optix
      RENDER_SERVER_PORT: 9876
      LOG_LEVEL: INFO

    # Network
    ports:
      - "9876:9876"
    networks:
      - render-network

    # Storage
    volumes:
      - ./render_output:/output
      - ./server_logs:/logs
      - /tmp/blender_cache:/tmp/blender_cache

    # Auto-restart on crash
    restart: unless-stopped

    # Resource limits
    deploy:
      resources:
        limits:
          cpus: '8'
          memory: 16G
        reservations:
          cpus: '4'
          memory: 8G
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

    # Health check
    healthcheck:
      test: ["CMD", "python3", "-c", "import websockets; import asyncio; asyncio.run(websockets.connect('ws://localhost:9876'))"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

  # (Optional) Secondary server for load balancing
  gpu-server-2:
    image: blender-remote-gpu:latest
    container_name: gpu-server-2

    runtime: nvidia
    environment:
      NVIDIA_VISIBLE_DEVICES: all
      NVIDIA_DRIVER_CAPABILITIES: compute,utility
      BLENDER_PATH: /usr/local/bin/blender
      GPU_BACKEND: optix
      RENDER_SERVER_PORT: 9877
      LOG_LEVEL: INFO

    ports:
      - "9877:9877"
    networks:
      - render-network

    volumes:
      - ./render_output:/output
      - ./server_logs:/logs
      - /tmp/blender_cache:/tmp/blender_cache

    restart: unless-stopped

    deploy:
      resources:
        limits:
          cpus: '8'
          memory: 16G
        reservations:
          cpus: '4'
          memory: 8G
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

networks:
  render-network:
    driver: bridge
```

### Step 1.3: Create .dockerignore

**File:** `/.dockerignore`

```
.git
.github
tests
.pytest_cache
*.pyc
__pycache__
.venv
venv
*.egg-info
.DS_Store
render_output
server_logs
```

### Step 1.4: Test Locally

```bash
# Build image
docker-compose build

# Start server(s)
docker-compose up -d

# Check logs
docker-compose logs -f gpu-server-1

# Test connection
python3 -c "import asyncio, websockets; asyncio.run(websockets.connect('ws://localhost:9876'))"

# Stop servers
docker-compose down
```

### Step 1.5: Production Deployment (systemd)

**File:** `/etc/systemd/system/blender-gpu-render.service`

```ini
[Unit]
Description=Blender Remote GPU Render Server
After=docker.service
Requires=docker.service

[Service]
Type=simple
WorkingDirectory=/home/render/blender-remote-gpu
ExecStart=/usr/local/bin/docker-compose up
ExecStop=/usr/local/bin/docker-compose down
Restart=unless-stopped
RestartSec=10s
User=render
Environment="PATH=/usr/local/bin:/usr/bin"

# Resource limits
MemoryLimit=24G
CPUQuota=80%

[Install]
WantedBy=multi-user.target
```

```bash
# Enable service
sudo systemctl enable blender-gpu-render.service

# Start service
sudo systemctl start blender-gpu-render.service

# Check status
sudo systemctl status blender-gpu-render.service

# View logs
sudo journalctl -u blender-gpu-render.service -f
```

---

## Priority 2: Network Resilience (1-2 Days)

### Why This?
- Handles packet loss on VPN/Tailscale
- Prevents silent failures
- Essential for remote access

### Step 2.1: Add Retry Logic to Client

**File:** `addon/connection.py` (modify existing)

```python
import asyncio
import logging

logger = logging.getLogger(__name__)

class RobustRemoteGPUConnection:
    """WebSocket connection with automatic retry and failover."""

    def __init__(self, server_ips: list[str], port: int, max_retries: int = 3):
        """
        Args:
            server_ips: List of server IPs to try (in order of preference)
            port: WebSocket port
            max_retries: Number of retries per server
        """
        self.server_ips = server_ips
        self.port = port
        self.max_retries = max_retries
        self.ws = None
        self.active_server = None

    async def connect(self) -> bool:
        """Connect to server with automatic failover. Returns True if successful."""
        for server_ip in self.server_ips:
            if await self._connect_to_server(server_ip):
                self.active_server = server_ip
                logger.info(f"Connected to {server_ip}:{self.port}")
                return True

        logger.error(f"Failed to connect to any server: {self.server_ips}")
        return False

    async def _connect_to_server(self, server_ip: str) -> bool:
        """Try to connect to a specific server. Returns True if successful."""
        url = f"ws://{server_ip}:{self.port}"

        for attempt in range(self.max_retries):
            try:
                logger.info(f"Attempt {attempt + 1}/{self.max_retries} to {url}")
                self.ws = await asyncio.wait_for(
                    websockets.connect(url),
                    timeout=10.0  # 10s timeout per attempt
                )
                return True

            except asyncio.TimeoutError:
                logger.warning(f"Connection timeout to {url}")
            except (websockets.InvalidURI, ConnectionRefusedError, OSError) as e:
                logger.warning(f"Connection failed to {url}: {e}")

            if attempt < self.max_retries - 1:
                delay = 2 ** attempt  # 1s, 2s, 4s
                logger.info(f"Retrying in {delay}s...")
                await asyncio.sleep(delay)

        return False

    async def send_with_retry(self, msg_type, data, binary=None) -> bool:
        """Send message with automatic retry. Returns True if successful."""
        if self.ws is None or self.ws.closed:
            # Try to reconnect
            if not await self.connect():
                return False

        for attempt in range(self.max_retries):
            try:
                msg = pack_websocket(msg_type, data, binary)
                for part in msg:
                    await asyncio.wait_for(self.ws.send(part), timeout=30.0)
                return True

            except (websockets.ConnectionClosed, asyncio.TimeoutError) as e:
                logger.warning(f"Send failed (attempt {attempt + 1}): {e}")

                if attempt < self.max_retries - 1:
                    # Try to reconnect and retry
                    if await self.connect():
                        continue
                    else:
                        delay = 2 ** attempt
                        await asyncio.sleep(delay)
                else:
                    return False

        return False
```

### Step 2.2: Add Failover to Server List

**File:** `addon/preferences.py` (modify existing)

```python
# In RemoteGPUPreferences class, add:

server_list: StringProperty(
    name="Server IPs (comma-separated)",
    description="Primary server IP, then backup servers (e.g., 192.168.1.100, 192.168.1.101, 192.168.1.102)",
    default="192.168.1.100",
)

# Parsing helper
def get_server_list(self) -> list[str]:
    """Parse comma-separated server list, removing whitespace."""
    return [ip.strip() for ip in self.server_list.split(",") if ip.strip()]
```

### Step 2.3: Add Message Sequence Numbers (Optional, Low Priority)

**File:** `shared/protocol.py` (enhancement)

```python
# Add sequence number to frames for reordering buffer
class FrameSequencer:
    def __init__(self):
        self.seq = 0

    def next_seq(self) -> int:
        self.seq = (self.seq + 1) % 2**32
        return self.seq

# In viewport frame metadata:
{
    "seq": 12345,  # Sequence number
    "width": 1920,
    "height": 1080,
    "samples": 128,
    "render_time": 0.15,
}
```

### Testing Step 2

```bash
# Simulate packet loss on macOS
sudo pfctl -e
echo "pass out on en0 probability 0.05 drop" | sudo pfctl -f -

# Run render, should auto-retry
# Check logs for "Attempt 1/3", "Retrying in 1s", etc.

# Disable packet loss
sudo pfctl -d
```

---

## Priority 3: Multi-Server Load Balancing (2-3 Days)

### Why This?
- Prevents single point of failure
- Distributes load across multiple GPUs
- Required for production at scale

### Step 3.1: Create Server Registry

**File:** `shared/server_registry.py` (new file)

```python
"""Server discovery and health checking."""

from dataclasses import dataclass
import asyncio
import time
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)

@dataclass
class ServerInfo:
    """Information about a render server."""
    ip: str
    port: int
    last_health_check: float = 0
    is_healthy: bool = False
    queue_depth: int = 0  # Number of queued renders
    vram_used_mb: int = 0

    def is_stale(self, max_age_s: float = 30.0) -> bool:
        """Check if health check is stale."""
        return (time.time() - self.last_health_check) > max_age_s

class ServerRegistry:
    """Tracks available render servers."""

    def __init__(self):
        self.servers: dict[str, ServerInfo] = {}  # ip -> ServerInfo
        self.check_interval = 10.0  # seconds
        self._health_check_task: Optional[asyncio.Task] = None

    def add_server(self, ip: str, port: int):
        """Add a server to the registry."""
        key = f"{ip}:{port}"
        self.servers[key] = ServerInfo(ip=ip, port=port)
        logger.info(f"Added server: {key}")

    def load_from_env(self, env_var: str = "RENDER_SERVERS"):
        """Load server list from environment variable.

        Format: "192.168.1.100:9876,192.168.1.101:9876,..."
        """
        import os
        server_list = os.getenv(env_var, "localhost:9876")
        for server_addr in server_list.split(","):
            if ":" in server_addr:
                ip, port = server_addr.rsplit(":", 1)
                self.add_server(ip.strip(), int(port.strip()))
            else:
                self.add_server(server_addr.strip(), 9876)

    async def health_check(self, server: ServerInfo) -> bool:
        """Check if server is healthy and get status."""
        import websockets
        from shared.protocol import MsgType, pack_websocket

        url = f"ws://{server.ip}:{server.port}"
        try:
            async with asyncio.timeout(5.0):
                async with websockets.connect(url) as ws:
                    # Send PING
                    msg = pack_websocket(MsgType.PING, {})[0]
                    await ws.send(msg)

                    # Receive PONG
                    response = await ws.recv()

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

        # Sort by queue depth, then VRAM usage
        return min(healthy, key=lambda s: (s.queue_depth, s.vram_used_mb))

    def get_all_servers(self) -> List[ServerInfo]:
        """Return all servers, healthy ones first."""
        return sorted(
            self.servers.values(),
            key=lambda s: (not s.is_healthy, s.queue_depth)
        )
```

### Step 3.2: Update Server to Report Status

**File:** `server/server.py` (modify `_get_status()`)

```python
async def _get_status(self) -> dict:
    """Get current server status."""
    status = {
        "version": "0.1.0",
        "server_id": self.server_id,  # Add unique ID
        "timestamp": time.time(),
    }

    # GPU info
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        status["gpu_name"] = pynvml.nvmlDeviceGetName(handle)
        status["vram_total"] = info.total // (1024 * 1024)
        status["vram_used"] = info.used // (1024 * 1024)
        status["vram_free"] = info.free // (1024 * 1024)
        status["gpu_temp"] = pynvml.nvmlDeviceGetTemperature(handle, 0)
        pynvml.nvmlShutdown()
    except Exception as e:
        status["gpu_error"] = str(e)

    # Queue info
    status["queue_depth"] = len(self.render_queue)  # Track if you add a queue
    status["is_healthy"] = True

    return status
```

### Step 3.3: Update Client to Use Registry

**File:** `addon/connection.py` (integrate with registry)

```python
class RemoteGPUConnector:
    def __init__(self, registry: ServerRegistry):
        self.registry = registry

    async def connect_to_best_server(self) -> bool:
        """Connect to healthiest server, with fallback."""
        servers = self.registry.get_all_servers()

        for server in servers:
            url = f"ws://{server.ip}:{server.port}"
            try:
                self.ws = await asyncio.wait_for(
                    websockets.connect(url),
                    timeout=5.0
                )
                self.active_server = server
                logger.info(f"Connected to {url} (queue depth: {server.queue_depth})")
                return True
            except Exception as e:
                logger.warning(f"Failed to connect to {url}: {e}")
                server.is_healthy = False

        return False
```

### Testing Step 3

```bash
# Start multiple servers
docker-compose up -d gpu-server-1 gpu-server-2

# In client, set RENDER_SERVERS env var
export RENDER_SERVERS="localhost:9876,localhost:9877"

# Test: sends render to server-1, second render to server-2
# Check logs: "Connected to localhost:9876 (queue depth: 0)"
```

---

## Priority 4: Metrics & Alerting (1-2 Days)

### Why This?
- Early warning for failures
- Performance tracking
- Production observability

### Step 4.1: Add Prometheus Metrics

**File:** `server/metrics.py` (new file)

```python
"""Prometheus metrics for monitoring."""

from prometheus_client import Counter, Gauge, Histogram, generate_latest

# Counters (monotonically increasing)
frames_rendered = Counter(
    "gpu_frames_rendered_total",
    "Total frames rendered",
    ["render_type"]  # viewport, final
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

# Gauges (current values)
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
    "Number of renders waiting in queue"
)

# Histograms (distributions)
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

### Step 4.2: Integrate Metrics into Server

**File:** `server/server.py` (add metrics)

```python
from server.metrics import (
    frames_rendered,
    gpu_vram_used_mb,
    gpu_temperature_c,
    connected_clients,
    frame_latency_ms,
)

class RenderServer:
    def __init__(self, ...):
        self.client_count = 0

    async def handle_client(self, websocket):
        self.client_count += 1
        connected_clients.set(self.client_count)

        try:
            # ... existing code ...
        finally:
            self.client_count -= 1
            connected_clients.set(self.client_count)

    async def _viewport_render_loop(self, ...):
        # ... existing code ...

        for samples in sample_steps:
            start = time.time()
            jpeg_data = self.live_viewport.render_frame(samples=samples)
            elapsed = time.time() - start

            # Record latency
            frame_latency_ms.observe(elapsed * 1000)
            frames_rendered.labels(render_type="viewport").inc()

            # Update GPU metrics
            try:
                import pynvml
                pynvml.nvmlInit()
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                gpu_vram_used_mb.set(info.used // (1024 * 1024))
                gpu_temperature_c.set(pynvml.nvmlDeviceGetTemperature(handle, 0))
                pynvml.nvmlShutdown()
            except:
                pass
```

### Step 4.3: Add Metrics Endpoint

**File:** `server/server.py` (add HTTP endpoint)

```python
from aiohttp import web
import asyncio

class MetricsServer:
    """Simple HTTP server for Prometheus scraping."""

    def __init__(self, port: int = 8000):
        self.port = port
        self.app = web.Application()
        self.app.router.add_get('/metrics', self.metrics_handler)

    async def metrics_handler(self, request):
        from server.metrics import get_metrics_text
        return web.Response(text=get_metrics_text(), content_type='text/plain')

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', self.port)
        await site.start()
        print(f"Metrics server running on http://0.0.0.0:{self.port}/metrics")

# In main():
async def main():
    server = RenderServer(port=9876)
    metrics = MetricsServer(port=8000)

    await asyncio.gather(
        server.start(),
        metrics.start(),
    )
```

### Step 4.4: Set Up Prometheus Monitoring

**File:** `monitoring/prometheus.yml`

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'blender-gpu-1'
    static_configs:
      - targets: ['localhost:8000']

  - job_name: 'blender-gpu-2'
    static_configs:
      - targets: ['localhost:8001']
```

**File:** `monitoring/alerts.yml`

```yaml
groups:
  - name: gpu_alerts
    rules:
      - alert: GPUTemperatureHigh
        expr: gpu_temperature_celsius > 80
        for: 5m
        annotations:
          summary: "GPU temperature high ({{ $value }}°C)"

      - alert: GPUVRAMHigh
        expr: gpu_vram_used_mb / gpu_vram_total_mb > 0.9
        for: 2m
        annotations:
          summary: "GPU VRAM usage high ({{ $value | humanizePercentage }})"

      - alert: FrameDropsHigh
        expr: rate(gpu_frames_dropped_total[5m]) > 1
        for: 2m
        annotations:
          summary: "Frame drops detected ({{ $value }} drops/sec)"

      - alert: ServerDown
        expr: up{job="blender-gpu-1"} == 0
        for: 1m
        annotations:
          summary: "Render server 1 is down"
```

### Step 4.5: Start Prometheus + Alertmanager

```bash
# Add to docker-compose.yml:

  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./monitoring/alerts.yml:/etc/prometheus/alerts.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--alerting.rules.files=/etc/prometheus/alerts.yml'
    networks:
      - render-network

  alertmanager:
    image: prom/alertmanager:latest
    ports:
      - "9093:9093"
    volumes:
      - ./monitoring/alertmanager.yml:/etc/alertmanager/alertmanager.yml
    networks:
      - render-network

volumes:
  prometheus_data:
```

### Testing Step 4

```bash
# Start services
docker-compose up -d prometheus gpu-server-1

# View metrics
curl http://localhost:8000/metrics

# View Prometheus dashboard
open http://localhost:9090
# Query: gpu_frames_rendered_total
# Query: gpu_temperature_celsius
# Query: gpu_vram_used_mb
```

---

## Priority 5: CI/CD Pipeline (0.5 Days)

### Why This?
- Catch bugs before production
- Automated testing on every commit
- Enforces code quality

### Step 5.1: Create GitHub Actions Workflow

**File:** `.github/workflows/test.yml`

```yaml
name: Test & Quality

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main, develop ]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
        pip install pytest pytest-cov black mypy pylint

    - name: Run unit tests
      run: |
        pytest tests/ -v --cov=server --cov=shared --cov-report=xml

    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        files: ./coverage.xml

    - name: Lint with Black
      run: black --check server addon shared tests

    - name: Type check with mypy
      run: mypy server shared --ignore-missing-imports

    - name: Lint with pylint
      run: pylint server shared --disable=C0111,R0903

    - name: Build Docker image
      run: docker-compose build gpu-server-1

    - name: Generate test report
      if: always()
      uses: dorny/test-reporter@v1
      with:
        name: Test Results
        path: 'test-results.xml'
        reporter: 'java-junit'
```

### Step 5.2: Add Pre-Commit Hooks

**File:** `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.1.0
    hooks:
      - id: black
        language_version: python3.11

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.4.0
    hooks:
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: check-yaml
      - id: check-added-large-files

  - repo: https://github.com/PyCQA/pylint
    rev: pylint-2.17.1
    hooks:
      - id: pylint
```

Install:
```bash
pip install pre-commit
pre-commit install
```

---

## Summary: Implementation Timeline

| Week | Task | Owner | Hours |
|------|------|-------|-------|
| Week 1 | Docker + docker-compose | DevOps | 8 |
| Week 1 | Network retry logic | Backend | 6 |
| Week 1 | Multi-server registry | Backend | 12 |
| Week 2 | Metrics + Prometheus | DevOps | 6 |
| Week 2 | CI/CD pipeline | DevOps | 4 |
| Week 2 | Testing + docs | QA | 8 |

**Total:** ~44 hours (1 person, ~1 month) or (2 people, ~2 weeks)

---

## Verification Checklist

After implementing each section:

- [ ] **Docker**: `docker-compose up` starts cleanly, server responds to pings
- [ ] **Retry**: Network with 5% packet loss doesn't cause silent failures
- [ ] **Multi-server**: Client connects to server-1, second render goes to server-2
- [ ] **Metrics**: `curl localhost:8000/metrics` returns Prometheus format
- [ ] **CI/CD**: PR checks pass, coverage >80%

---

## Production Deployment Checklist

Before going live:

- [ ] All 5 blocking items implemented and tested
- [ ] Docker images pushed to registry
- [ ] systemd service configured and tested
- [ ] Monitoring alerts configured
- [ ] Runbook created (how to respond to common alerts)
- [ ] Load test: 5 concurrent clients, no frame loss
- [ ] Stress test: VPN with 10% packet loss, still renders correctly
