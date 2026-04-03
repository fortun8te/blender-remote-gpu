# Dispatcher Architecture v4.0

## Overview

The server has been completely rewritten as a **stateless dispatcher** that manages a pool of isolated Blender processes. This eliminates the blocking main thread issue and state leakage that plagued the persistent worker approach.

## Architecture Diagram

```
HTTP Client
    │
    │ POST /render (JSON)
    │
    ▼
┌─────────────────────────────┐
│   server.py (Dispatcher)    │
│  - HTTP handler             │
│  - Job queue manager        │
│  - Worker pool controller   │
└─────────────────────────────┘
    │
    │ Enqueue job
    │
    ▼
┌─────────────────────────────┐
│   job_queue.py (FIFO)       │
│  - Thread-safe deque        │
│  - Status tracking          │
│  - Result caching (5 min)   │
└─────────────────────────────┘
    │
    ├─→ submit_job(job_type, params) → job_id
    ├─→ get_next_job() → (job_id, type, params)
    ├─→ set_result(job_id, result)
    └─→ get_status(job_id) / get_result(job_id)
    │
    ▼
┌─────────────────────────────┐
│  Worker Thread Pool (N=3)   │
│  Each thread:               │
│  1. Poll job_queue          │
│  2. Spawn Blender subprocess│
│  3. Wait for completion     │
│  4. Store result            │
│  5. Repeat                  │
└─────────────────────────────┘
    │
    ├─→ subprocess.Popen([blender, --background, --python, single_job_worker.py])
    │
    ▼
┌─────────────────────────────┐
│ Blender Process (isolated)  │
│  single_job_worker.py:      │
│  1. Parse RENDER_JOB_JSON   │
│  2. Load scene              │
│  3. Set frame/camera        │
│  4. Execute render          │
│  5. Save PNG                │
│  6. Exit (clean process)    │
└─────────────────────────────┘
```

## Files

### New Files

| File | Purpose |
|------|---------|
| **job_queue.py** | Thread-safe job queue with status tracking and result caching |
| **single_job_worker.py** | Stateless Blender subprocess worker (1 job per process) |

### Modified Files

| File | Changes |
|------|---------|
| **server.py** | Completely rewritten as dispatcher (removed persistent worker logic) |

### Removed Logic

- `render_worker.py` — No longer used (persistent worker pattern eliminated)
- All socket communication with persistent worker
- Worker restart/corruption tracking
- Scene load polling and fallback logic

## Key Design Decisions

### 1. Stateless Dispatcher
- **server.py** is now a pure HTTP handler + job queue manager
- **No persistent Blender process** = no blocking on scene load
- **Main thread never blocks** on render operations
- Results are returned asynchronously via job polling

### 2. Subprocess-Per-Job Pattern
Each render spawns a **fresh Blender process**:

```python
# Benefits:
- Memory isolation (no fragmentation)
- Clean process state (no leakage)
- Crash resilience (failed render doesn't affect dispatcher)
- Deterministic cleanup (process exit = cleanup)

# Tradeoff:
- Startup overhead (~2-5s per job)
- No persistent texture cache
- Higher resource spikes with parallel jobs
```

### 3. Job Queue (In-Memory, FIFO)
- **Thread-safe** (all operations protected by lock)
- **FIFO ordering** (first submitted = first rendered)
- **Status tracking**: queued → running → done/error
- **Result caching**: 5-minute TTL (configurable)
- **Automatic cleanup**: expired results auto-deleted

### 4. Worker Pool (N=3 by default)
- **3 worker threads** continuously poll the job queue
- **Highly configurable**: change `NUM_WORKERS` in server.py
- **Independent threads**: one job failure doesn't block others
- **Scalable**: spawn more threads for more parallelism

### 5. No External Dependencies
- **In-memory queue** (no Redis, RabbitMQ, etc.)
- **No message broker** overhead
- **Minimal setup**: just start server.py

## API

### Endpoints

#### 1. Upload Scene
```json
POST /
{
  "type": "scene_upload",
  "blend_data": "base64-encoded-blend-file"
}

Response:
{
  "type": "scene_cached",
  "scene_id": "abc123",
  "blend_path": "/tmp/scene_abc123.blend",
  "size_mb": 45.2
}
```

#### 2. Submit Render Job
```json
POST /
{
  "type": "viewport_render",
  "blend_path": "/tmp/scene_abc123.blend",
  "frame": 1,
  "view_matrix": [[4x4 matrix]],
  "timeout": 3600
}

Response:
{
  "type": "job_submitted",
  "job_id": "def456",
  "status": "queued"
}
```

#### 3. Check Job Status
```json
POST /
{
  "type": "job_status",
  "job_id": "def456"
}

Response:
{
  "type": "job_status",
  "job_id": "def456",
  "status": "running"  // queued | running | done | error
}
```

#### 4. Get Job Result
```json
POST /
{
  "type": "job_result",
  "job_id": "def456"
}

Response (on success):
{
  "type": "render_complete",
  "job_id": "def456",
  "status": "success",
  "output_path": "/tmp/render_def456.png",
  "file_size_mb": 2.5
}

Response (on error):
{
  "type": "error",
  "message": "Result not ready (status: running)"
}
```

#### 5. Queue Status
```json
POST /
{
  "type": "queue_status"
}

Response:
{
  "type": "queue_status",
  "queue_size": 5,
  "total_submitted": 12,
  "queued": 5,
  "running": 2,
  "done": 5,
  "error": 0,
  "expired_results": 0
}
```

#### 6. Health Check
```json
POST /
{
  "type": "health"
}

Response:
{
  "type": "health",
  "status": "ok",
  "timestamp": 1712189400.123,
  "queue": { /* queue_status */ }
}
```

#### 7. Ping
```json
POST /
{
  "type": "ping"
}

Response:
{
  "type": "pong",
  "gpu": "NVIDIA RTX 4090",
  "vram_free": 24000,
  "timestamp": 1712189400.123,
  "version": "4.0.0",
  "build": "dispatcher",
  "worker_ready": true
}
```

## Configuration

### Environment Variables

None required (all defaults are sensible).

### Customizable Settings (in server.py)

```python
NUM_WORKERS = 3                    # Number of worker threads
RESULT_TTL_SECONDS = 300          # Cache results for 5 minutes
HTTP_PORT = 9876                  # HTTP listen port
```

## Client Workflow

### Full Example: Upload & Render

```python
import requests
import json
import time

BASE_URL = "http://localhost:9876"

# 1. Upload scene
with open("scene.blend", "rb") as f:
    import base64
    blend_b64 = base64.b64encode(f.read()).decode()

upload_resp = requests.post(
    BASE_URL,
    json={
        "type": "scene_upload",
        "blend_data": blend_b64
    }
)
blend_path = upload_resp.json()["blend_path"]

# 2. Submit render job
render_resp = requests.post(
    BASE_URL,
    json={
        "type": "viewport_render",
        "blend_path": blend_path,
        "frame": 1,
        "view_matrix": camera_matrix,
    }
)
job_id = render_resp.json()["job_id"]

# 3. Poll for completion
while True:
    status_resp = requests.post(
        BASE_URL,
        json={"type": "job_status", "job_id": job_id}
    )
    status = status_resp.json()["status"]

    if status == "done":
        # 4. Get result
        result_resp = requests.post(
            BASE_URL,
            json={"type": "job_result", "job_id": job_id}
        )
        output_path = result_resp.json()["output_path"]
        print(f"Render saved to {output_path}")
        break
    elif status == "error":
        print(f"Render failed: {result_resp.json()['message']}")
        break

    time.sleep(0.1)  # Poll every 100ms
```

## Performance Characteristics

### Advantages Over Persistent Worker

| Aspect | Persistent | Dispatcher |
|--------|-----------|-----------|
| Main thread blocking | Yes (scene load) | No |
| State leakage | Possible | Impossible |
| Memory fragmentation | Over time | Fresh each job |
| Crash resilience | Affects all jobs | Single job only |
| Restart complexity | High | N/A |
| Debugging | Hard | Simple (logs per job) |
| Scaling | Difficult | Easy (add workers) |

### Latency Breakdown (per job)

```
Total ~6-8s for first render:
├─ Blender startup: 2-3s
├─ Scene load: 1-2s
├─ Render: 1-2s (depends on complexity)
├─ PNG encode: 0.1-0.5s
└─ I/O: <0.1s

Subsequent renders: ~3-5s (no startup)
```

### Memory Usage

```
Per worker thread: ~200MB (Python + threading overhead)
Per Blender process: ~1-3GB (depends on scene)
Per job result cache: 1-10MB (PNG data)

Example (3 workers + 2 active renders):
- Dispatcher: ~600MB
- Blender 1: ~2GB
- Blender 2: ~2GB
- Cache (5 jobs): ~20MB
─────────
Total: ~4.6GB (highly variable)
```

## Monitoring

### Log Output

The dispatcher logs everything to `/tmp/blender_server.log`:

```
[14:23:45] [Server] [INFO] Blender Remote GPU Render Server v4.0 (DISPATCHER)
[14:23:45] [Server] [INFO] GPU: NVIDIA RTX 4090 (24000 MB VRAM)
[14:23:45] [Server] [INFO] Starting 3 worker threads...
[14:23:45] [Worker-0] [INFO] Started
[14:23:45] [Worker-1] [INFO] Started
[14:23:45] [Worker-2] [INFO] Started
[14:23:46] [Server] [INFO] Starting HTTP server on port 9876...
[14:23:50] [HTTP] [127.0.0.1] scene_upload OK in 245ms
[14:23:51] [HTTP] [127.0.0.1] viewport_render OK in 5ms
[14:23:51] [Worker-0] [INFO] Processing job abc123 (viewport_render)
[14:23:51] [Worker-0] [INFO] Spawning Blender for job abc123
...
```

### Health Check Response

```json
{
  "type": "health",
  "status": "ok",
  "timestamp": 1712189400.123,
  "queue": {
    "queue_size": 2,
    "total_submitted": 47,
    "queued": 2,
    "running": 1,
    "done": 43,
    "error": 1,
    "expired_results": 3
  }
}
```

## Migration Path (from v3 to v4)

For clients previously using the persistent worker:

### Old Pattern (v3)
```python
# 1. Upload scene (waits for load completion)
response = scene_upload(blend_data)
# Takes 30-120s due to scene load polling

# 2. Render immediately
response = viewport_render(view_matrix)
# Returns PNG directly in response
```

### New Pattern (v4 — Recommended)
```python
# 1. Upload scene (returns immediately)
response = scene_upload(blend_data)
blend_path = response["blend_path"]

# 2. Submit render job
response = viewport_render(blend_path, view_matrix)
job_id = response["job_id"]

# 3. Poll for result (non-blocking)
while True:
    status = job_status(job_id)
    if status == "done":
        result = job_result(job_id)
        png_path = result["output_path"]
        break
    time.sleep(0.1)
```

### Benefits
- **Immediate feedback** on job submission
- **Non-blocking** main thread
- **Scalable** for batch renders
- **Resilient** to individual job failures

## Troubleshooting

### "Job not found"
- Result TTL expired (default 5 min)
- Job ID typo
- Server restarted

### "Blender process timed out"
- Scene too complex
- Render resolution too high
- Check `/tmp/blender_worker.log` for details
- Increase `timeout` param in job

### "Queue stuck / not processing"
- Check worker threads are running: `ps aux | grep blender`
- Check logs: `tail -f /tmp/blender_server.log`
- Monitor queue: POST `{"type": "queue_status"}`
- If stuck: restart server

### "Out of memory"
- Reduce `NUM_WORKERS`
- Reduce job complexity
- Increase result TTL cleanup frequency

## Future Improvements

1. **Redis/RabbitMQ backend** — for distributed workers
2. **Job prioritization** — urgent jobs first
3. **Persistent storage** — track results across restarts
4. **Metrics dashboard** — HTTP endpoint with stats
5. **Job scheduling** — cron-like job submission
6. **Figma MCP integration** — automated design-to-render pipeline
