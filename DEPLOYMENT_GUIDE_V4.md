# Deployment Guide v4.0

## Quick Start (5 minutes)

### Prerequisites
- Python 3.8+ with pip
- Blender 4.0+
- GPU access (NVIDIA with CUDA, AMD with HIP, or CPU fallback)
- Network connectivity between Blender machine and dispatcher

### 1. Start Dispatcher (Production GPU Machine)

```bash
# Clone or download repo
git clone <repo-url>
cd blender-remote-gpu

# Start HTTP server (listens on 0.0.0.0:5000)
python3 server.py
```

**Expected output:**
```
[RemoteGPU Dispatcher] Starting server on 0.0.0.0:5000...
[RemoteGPU Dispatcher] Workers online: 0
[RemoteGPU Dispatcher] Server ready. Waiting for jobs.
```

**Verify health:**
```bash
curl http://localhost:5000/health
# {"status": "ready", "queue_size": 0, "workers_online": 0}
```

---

### 2. Start Worker(s) (Optional, default: local machine)

**Same machine as dispatcher (automatic):**
Dispatcher auto-starts a worker pool if workers are not explicitly registered.

**Remote machine (e.g., second GPU):**
```bash
# On remote machine
python3 single_job_worker.py \
  --dispatcher http://<dispatcher-ip>:5000 \
  --worker_id worker_001
```

**Multiple workers (same machine, load balance):**
```bash
python3 single_job_worker.py --dispatcher http://localhost:5000 --worker_id worker_001 &
python3 single_job_worker.py --dispatcher http://localhost:5000 --worker_id worker_002 &
python3 single_job_worker.py --dispatcher http://localhost:5000 --worker_id worker_003 &
```

---

### 3. Install Blender Addon

#### Build addon package
```bash
cd blender-remote-gpu
./build_addon.sh
# Outputs: remote_gpu_render.zip
```

#### Install in Blender
1. Open Blender
2. Edit > Preferences > Add-ons
3. Click "Install"
4. Select `remote_gpu_render.zip`
5. Enable "Remote GPU Render"

**Verify in System Console:**
```
[RemoteGPU] Registered v2.0.0 v4
```

---

### 4. Configure & Connect

**In Blender Preferences > Remote GPU Render:**
- Dispatcher IP: `<dispatcher-machine-ip>` (or `localhost` if same machine)
- Dispatcher Port: `5000`
- Click "Connect to Dispatcher"

**If connected:**
- Render Properties panel shows "CONNECTED"
- Status shows latency (e.g., "12ms")

---

### 5. Submit Your First Render

1. Open a Blender scene
2. Render Properties > Remote GPU Render section
3. Click "Start Render" or press F12
4. Watch live preview in N-panel

---

## Architecture Overview

```
┌─────────────────────────┐
│   Blender (Addon)       │
│  (Scene Submission)     │
└────────────┬────────────┘
             │ HTTP POST /submit
             ▼
    ┌────────────────────┐
    │   Dispatcher       │
    │  (Job Queue)       │
    │  (Status Tracking) │
    └────────┬───────────┘
             │ Job assignment
             │
    ┌────────┴──────┬──────────┐
    ▼               ▼          ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Worker 1 │  │ Worker 2 │  │ Worker 3 │
│(Render)  │  │(Render)  │  │(Render)  │
└──────────┘  └──────────┘  └──────────┘
```

**Data Flow:**
1. Addon submits scene + params to `/submit`
2. Dispatcher creates job, returns `job_id`
3. Addon polls `/job_status/job_id` (100ms intervals)
4. Worker claims job from queue, renders, updates progress
5. On completion, addon retrieves result via `/job_result/job_id`

---

## Configuration

### Environment Variables (Optional)

```bash
# Dispatcher config
export DISPATCHER_HOST=0.0.0.0
export DISPATCHER_PORT=5000
export JOB_TIMEOUT_SECONDS=300
export WORKER_POLL_INTERVAL=2

# Worker config
export DISPATCHER_URL=http://localhost:5000
export WORKER_IDLE_TIMEOUT_SECONDS=60
```

### Config Files

**`server.py` (Dispatcher):**
```python
DISPATCHER_HOST = os.getenv("DISPATCHER_HOST", "0.0.0.0")
DISPATCHER_PORT = int(os.getenv("DISPATCHER_PORT", 5000))
JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", 300))
```

**`single_job_worker.py` (Worker):**
```python
parser.add_argument("--dispatcher", default="http://localhost:5000")
parser.add_argument("--worker_id", default=f"worker_{uuid4()}")
parser.add_argument("--poll_interval", type=int, default=2)
```

---

## Monitoring & Logs

### Dispatcher Logs

**File:**
```bash
tail -f /tmp/blender_server.log
```

**Key events:**
```
[2026-04-03 18:30:00] Job submitted: job_abc123
[2026-04-03 18:30:05] Job assigned: job_abc123 → worker_001
[2026-04-03 18:30:45] Job complete: job_abc123 (45.3s)
```

### Worker Logs

**Console output:**
```bash
[Worker worker_001] Job assigned: job_abc123
[Worker worker_001] Rendering frame 1...
[Worker worker_001] Progress: 50/100 samples
[Worker worker_001] Complete
```

### Health Check

```bash
# Dispatcher health
curl http://<dispatcher-ip>:5000/health | jq .

# Response:
{
  "status": "ready",
  "queue_size": 0,
  "workers_online": 2,
  "timestamp": "2026-04-03T18:35:00Z",
  "version": "v4"
}
```

---

## Common Deployment Scenarios

### Scenario 1: Single Machine (Laptop + GPU)

```bash
# Terminal 1
python3 server.py

# Terminal 2 (optional, dispatcher auto-starts internal worker)
python3 single_job_worker.py --dispatcher http://localhost:5000

# Blender config: dispatcher IP = localhost:5000
```

**Use case:** Offload rendering from Blender viewport to keep UI responsive

---

### Scenario 2: Render Farm (Multi-GPU, Multi-Machine)

```
Machine A (Workstation + Blender):
  - Run Blender with addon
  - Configure: dispatcher_ip = 192.168.1.100

Machine B (GPU Server 1):
  - python3 server.py (dispatcher)
  - python3 single_job_worker.py --worker_id worker_001

Machine C (GPU Server 2):
  - python3 single_job_worker.py --dispatcher http://192.168.1.100:5000 --worker_id worker_002

Machine D (GPU Server 3):
  - python3 single_job_worker.py --dispatcher http://192.168.1.100:5000 --worker_id worker_003
```

**Benefits:**
- Blender on workstation (fast iteration)
- Rendering distributed across 3 GPUs
- Job queue automatically load-balances

---

### Scenario 3: Docker Container (Cloud Deployment)

```dockerfile
# Dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y python3 python3-pip
RUN pip install flask requests
COPY server.py /app/
WORKDIR /app
CMD ["python3", "server.py"]
```

**Build & run:**
```bash
docker build -t blender-dispatcher:v4 .
docker run -p 5000:5000 blender-dispatcher:v4
```

---

## Performance Tuning

### CPU Rendering
Default: Uses all CPU cores

**Limit to N cores:**
```python
# In render_worker.py
import os
os.environ["BLENDER_CORES"] = "4"  # Limit to 4 cores
```

### GPU Rendering
Default: Auto-detects NVIDIA CUDA / AMD HIP

**Force CUDA:**
```python
os.environ["BLENDER_DEVICE"] = "CUDA"
```

**Force CPU:**
```python
os.environ["BLENDER_DEVICE"] = "CPU"
```

### Memory Limits
**Prevent OOM kills:**
```python
# In job_queue.py
MAX_JOB_MEMORY_GB = 16  # Reserve 16GB per job
```

---

## Troubleshooting

### Issue: Dispatcher fails to start

```
Error: Address already in use: ('0.0.0.0', 5000)
```

**Solution:**
```bash
# Find & kill existing process
lsof -i :5000
kill -9 <PID>
```

---

### Issue: Worker won't connect to dispatcher

```
[Worker worker_001] Connection refused: http://localhost:5000
```

**Checklist:**
- Is dispatcher running? `lsof -i :5000`
- Is firewall blocking port 5000?
- Is dispatcher IP correct in worker args?
- Is Python 3.8+? `python3 --version`

**Solution:**
```bash
# Test connectivity
curl http://<dispatcher-ip>:5000/health

# If timeout, check firewall
sudo ufw allow 5000/tcp
```

---

### Issue: Blender addon says "NOT CONNECTED"

```
[RemoteGPU] Dispatcher unreachable: Connection refused
```

**Checklist:**
1. Is dispatcher running? `curl http://localhost:5000/health`
2. Is IP correct in addon preferences?
3. Is firewall blocking?
4. Check Blender System Console for detailed error

**Solution:**
```bash
# In Blender System Console (Help > Toggle System Console)
# Should show connection attempt:
[RemoteGPU] Connecting to http://192.168.1.100:5000...
[RemoteGPU] Connected! Latency: 12ms
```

---

### Issue: Render stuck in "rendering" state

```
Job status shows: {"status": "rendering", "progress": "50%"}
But nothing is happening for 10+ minutes
```

**Possible causes:**
- Worker crashed mid-render
- Blender process hung
- Network timeout

**Solutions:**
```bash
# Check worker process
ps aux | grep "single_job_worker"

# Check Blender render status
ps aux | grep blender

# Force timeout (job reassigns after 5 min)
# Or manually restart worker to clear stuck jobs
```

---

### Issue: Memory leak / dispatcher slow after many jobs

```
Dispatcher uses 5GB+ RAM after 1000 jobs
```

**Cause:** Job history not cleaned

**Solution:**
```python
# In server.py, add job cleanup
import time
for job_id in list(self.completed_jobs.keys()):
    if time.time() - self.completed_jobs[job_id]["timestamp"] > 3600:
        del self.completed_jobs[job_id]  # Delete > 1 hour old
```

---

## Production Checklist

- [ ] Dispatcher running on dedicated GPU machine
- [ ] Firewall allows port 5000 (or custom port)
- [ ] At least 1 worker registered
- [ ] Health endpoint responds: `curl http://<ip>:5000/health`
- [ ] Test job completes: `curl -X POST http://<ip>:5000/submit ...`
- [ ] Blender addon installed and enabled
- [ ] Addon connects to dispatcher (shows CONNECTED + latency)
- [ ] Test render completes end-to-end
- [ ] Monitor logs for errors: `tail -f /tmp/blender_server.log`
- [ ] Set up log rotation for dispatcher logs
- [ ] Document dispatcher IP/port for team
- [ ] Backup job queue database (if persistent storage added)

---

## Rollback Procedure

If v4 has critical issues and you need to revert to v3 (b37):

**1. Stop all services:**
```bash
pkill -f server.py
pkill -f single_job_worker.py
pkill -f blender
```

**2. Uninstall v4 addon:**
- Blender Preferences > Add-ons
- Search "Remote GPU"
- Uninstall

**3. Check out v3 branch (if available):**
```bash
git checkout b37
# Or restore from backup
```

**4. Rebuild v3 addon:**
```bash
./build_addon.sh
```

**5. Reinstall in Blender**

**Note:** v3 uses direct worker connection (no dispatcher queue). Performance will be slightly slower due to lack of job batching.

---

## Performance Expectations

| Scenario | Resolution | Samples | GPU | Time |
|----------|------------|---------|-----|------|
| Simple scene | 1280x720 | 256 | RTX 3080 | ~45s |
| Complex scene | 1920x1080 | 512 | RTX 4090 | ~90s |
| Very complex | 3840x2160 | 2048 | RTX 4090 | ~8m |
| CPU (24-core) | 1280x720 | 256 | — | ~180s |

**Network latency:** ~10-30ms (typical LAN)

**Queue overhead:** ~100-500ms per job (negligible)

---

## Getting Help

**Check logs:**
```bash
tail -f /tmp/blender_server.log
tail -f /tmp/blender_worker.log
```

**Enable debug mode:**
```bash
export DEBUG=1
python3 server.py
```

**Test network:**
```bash
# Dispatcher reachable?
curl http://<dispatcher-ip>:5000/health

# Can submit job?
curl -X POST http://<dispatcher-ip>:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"scene_path": "/tmp/test.blend"}'
```

**Check Blender System Console:**
- Help > Toggle System Console
- Look for `[RemoteGPU]` messages

---

End of Deployment Guide v4.0
