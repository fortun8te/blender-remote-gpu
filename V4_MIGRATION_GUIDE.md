# Migration Guide: v3 (b37) → v4

## Overview

v4 is a **complete rewrite** of the rendering architecture. Instead of direct worker control, v4 uses a **job dispatcher** with an HTTP queue.

**Migration difficulty:** Medium
- Configuration changes required
- Workflow similar, but error messages different
- Rollback procedure available (see end of guide)

---

## What Changed

### Architecture

| Aspect | v3 (b37) | v4 |
|--------|----------|-----|
| Connection model | Blender → Worker (direct socket) | Blender → Dispatcher (HTTP) → Worker |
| Job submission | Synchronous (blocks Blender) | Asynchronous (returns immediately) |
| Error reporting | Socket timeouts | HTTP error codes + JSON |
| Scalability | Manual (1 worker at a time) | Automatic (all workers in queue) |
| Config | Worker IP/port | Dispatcher IP/port |

### Blender Addon UI

**v3 settings:**
```
Remote GPU Render:
  ├─ Worker IP: [192.168.1.100]
  ├─ Worker Port: [9000]
  └─ Connect
```

**v4 settings:**
```
Remote GPU Render:
  ├─ Dispatcher IP: [192.168.1.100]
  ├─ Dispatcher Port: [5000]
  └─ Connect
```

**Key difference:** No longer need to manually select a worker. Submit to dispatcher, which assigns to available workers.

### File Locations

**v3:**
- Dispatcher: none (direct worker connection)
- Worker: `render_worker.py`
- Config: Hardcoded in addon

**v4:**
- Dispatcher: `server.py` (NEW)
- Worker: `single_job_worker.py` (renamed from `render_worker.py`)
- Config: `src/config/infrastructure.ts` (frontend) or env vars (backend)

### Command Line

**v3 startup:**
```bash
# Start worker on GPU machine
python3 render_worker.py --port 9000

# Blender → configure → enter worker IP:9000
```

**v4 startup:**
```bash
# Start dispatcher on any machine
python3 server.py

# Start worker(s) on GPU machine(s)
python3 single_job_worker.py --dispatcher http://localhost:5000 --worker_id worker_001

# Blender → configure → enter dispatcher IP:5000
```

---

## Step-by-Step Migration

### Phase 1: Install v4 Code

**1.1 Backup v3**
```bash
cd /Users/mk/Downloads/blender-remote-gpu
git stash  # Save local changes
cp -r . ../blender-remote-gpu-b37-backup/
```

**1.2 Update to v4**
```bash
git checkout v4  # or main, depending on branch
git pull origin v4
```

**1.3 Verify files exist**
```bash
ls -la server.py single_job_worker.py job_queue.py
```

---

### Phase 2: Start New Infrastructure

**2.1 Kill old worker (if running)**
```bash
pkill -f "render_worker.py"
```

**2.2 Start dispatcher (Port 5000)**
```bash
python3 server.py
# Expected: [RemoteGPU Dispatcher] Server ready on 0.0.0.0:5000
```

**2.3 Start worker(s) (Optional, if separate machines)**
```bash
# On GPU machine
python3 single_job_worker.py --dispatcher http://<dispatcher-ip>:5000 --worker_id worker_001
# Expected: [Worker worker_001] Registration successful
```

**2.4 Verify health**
```bash
curl -s http://localhost:5000/health | jq .
# Expected: {"status": "ready", "workers_online": 1}
```

---

### Phase 3: Update Blender Addon

**3.1 Uninstall v3 addon**
- Blender Preferences > Add-ons
- Search "Remote GPU"
- Click "Remove" (uninstall)

**3.2 Build v4 addon**
```bash
cd /Users/mk/Downloads/blender-remote-gpu
./build_addon.sh
# Creates: remote_gpu_render.zip
```

**3.3 Install v4 addon**
- Blender Preferences > Add-ons > Install
- Select `remote_gpu_render.zip`
- Enable "Remote GPU Render"

**3.4 Configure**
- Render Properties > Remote GPU Render
- Dispatcher IP: `localhost` (or machine IP if remote)
- Dispatcher Port: `5000`
- Click "Connect to Dispatcher"

**3.5 Verify connection**
- If "CONNECTED" appears with latency, success!
- If error, check dispatcher health: `curl http://localhost:5000/health`

---

### Phase 4: Test Rendering

**4.1 Test job submission**
```bash
# Create simple scene or use existing
# Render Properties > Remote GPU Render
# Click "Start Render" or press F12

# In dispatcher terminal, watch for:
# [Dispatcher] Job submitted: job_xyz...
# [Dispatcher] Job assigned to worker_001
```

**4.2 Monitor progress**
```bash
# In another terminal
watch -n 1 'curl -s http://localhost:5000/health | jq .'

# Should show:
# "queue_size": 0  (if job complete)
# "active_jobs": 1 (if still rendering)
```

**4.3 Check result**
```bash
# After render complete
ls -la /tmp/blender_renders/  # Or configured output path
```

---

## Configuration Changes

### Blender Addon Preferences

**Old (v3):**
```
Remote GPU Render
├─ Server IP: 192.168.1.100    (worker machine)
├─ Server Port: 9000           (worker port)
└─ Connection Mode: Direct Socket
```

**New (v4):**
```
Remote GPU Render
├─ Dispatcher IP: 192.168.1.100    (dispatcher machine)
├─ Dispatcher Port: 5000           (dispatcher port)
└─ Connection Mode: HTTP REST API
```

### Environment Variables

**v3 (if used):**
```bash
export WORKER_IP=192.168.1.100
export WORKER_PORT=9000
```

**v4 (if used):**
```bash
export DISPATCHER_HOST=0.0.0.0
export DISPATCHER_PORT=5000
export DISPATCHER_URL=http://localhost:5000
```

### Network Configuration

**v3 — Firewall rules:**
```
Port 9000 (worker) must be open from Blender machine
```

**v4 — Firewall rules:**
```
Port 5000 (dispatcher) must be open from Blender machine
No direct connection needed to worker machines
```

---

## Behavioral Changes

### Job Submission

**v3 behavior:**
```
Click Render
├─ Blender blocks until connection established
├─ Blender waits for render to start
└─ UI freezes for ~2 seconds
```

**v4 behavior:**
```
Click Render
├─ Blender submits job immediately (non-blocking)
├─ Returns with job_id
├─ Continues polling in background
└─ UI responsive immediately
```

**User experience:** Much faster feedback, UI doesn't freeze

---

### Error Messages

**v3 errors:**
```
"Failed to connect to worker: Connection refused"
"Socket timeout: Worker not responding"
"Worker disconnected during render"
```

**v4 errors:**
```
"Dispatcher unavailable: HTTP 503 Service Unavailable"
"Job not found: job_xyz already retrieved"
"Invalid scene path: /path/to/scene.blend does not exist"
```

**User action:** Check dispatcher health (`curl http://<ip>:5000/health`)

---

### Multiple GPU Machines

**v3 approach:**
```
Blender → Select Worker A or B manually
         → Connect → Render
```

**v4 approach:**
```
Blender → Submit to dispatcher (1 time)
Dispatcher → Assigns to available worker automatically
```

**Benefit:** No manual selection needed. Dispatcher auto-load-balances.

---

## Troubleshooting Migration Issues

### Issue: Addon shows "NOT CONNECTED"

**Cause:** Dispatcher not running or wrong IP/port

**Solution:**
```bash
# 1. Verify dispatcher is running
lsof -i :5000

# 2. Test manually
curl http://localhost:5000/health

# 3. Check Blender console
Help > Toggle System Console
# Look for: [RemoteGPU] Connecting to http://...
```

---

### Issue: "Job submitted but status shows queued forever"

**Cause:** No workers registered

**Solution:**
```bash
# Start worker
python3 single_job_worker.py --dispatcher http://localhost:5000

# Verify registration
curl http://localhost:5000/health | jq '.workers_online'
# Should show: 1 (or more)
```

---

### Issue: v3 addon still shows in Blender

**Cause:** Old addon not fully uninstalled

**Solution:**
```bash
# Find addon directory
find ~/.config/blender -name "remote_gpu_render" -type d

# Remove manually
rm -rf ~/.config/blender/4.0/scripts/addons/remote_gpu_render

# Restart Blender
# Reinstall v4 addon
```

---

### Issue: Worker won't connect to dispatcher

**Cause:** Network unreachable or wrong dispatcher URL

**Solution:**
```bash
# Test connectivity
curl http://<dispatcher-ip>:5000/health

# If timeout, check:
# 1. Dispatcher running? (ps aux | grep server.py)
# 2. Firewall blocking? (sudo ufw allow 5000/tcp)
# 3. DNS resolution? (ping <dispatcher-ip>)

# Start worker with verbose output
DEBUG=1 python3 single_job_worker.py --dispatcher http://<ip>:5000
```

---

## Rollback Procedure (v4 → v3)

If v4 has critical issues, you can revert to v3:

### Step 1: Stop all services
```bash
pkill -f server.py
pkill -f single_job_worker.py
pkill -f blender
```

### Step 2: Uninstall v4 addon
- Blender Preferences > Add-ons
- Find "Remote GPU Render"
- Click "Remove"

### Step 3: Restore v3 code
```bash
cd /Users/mk/Downloads/blender-remote-gpu
git checkout b37  # or restore from backup
```

### Step 4: Rebuild v3 addon
```bash
./build_addon.sh
```

### Step 5: Reinstall addon
- Blender Preferences > Add-ons > Install
- Select `remote_gpu_render.zip`
- Enable

### Step 6: Configure v3 settings
- Render Properties > Remote GPU Render
- Set Worker IP & Port (v3 settings, not dispatcher)
- Connect

### Step 7: Restart worker
```bash
python3 render_worker.py --port 9000
```

---

## Performance Comparison

### Render Time (unchanged)
- Same GPU = same render time
- v4 adds negligible queue overhead (<500ms)

### UI Responsiveness (improved)
| Operation | v3 | v4 |
|-----------|-----|-----|
| Submit render | 2s (blocks) | 10ms (async) |
| Polling progress | Manual (every 5s) | Automatic (every 100ms) |
| Error recovery | Manual restart | Auto-reassign |

---

## Feature Parity

### v3 Features (still available in v4)
- [x] Remote rendering
- [x] F12 render
- [x] Progress polling
- [x] Live preview (experimental)
- [x] Multi-GPU rendering

### v4 New Features
- [x] Job queue
- [x] Automatic load balancing
- [x] Worker crash recovery
- [x] HTTP API (extensible)
- [x] Status monitoring
- [x] Non-blocking submission

### v3 Features (removed in v4)
- Socket-based streaming (replaced by HTTP)
- Direct worker selection (replaced by dispatcher assignment)

---

## Recommended Migration Timeline

### Day 1: Installation
- [ ] Backup v3 (git stash)
- [ ] Update to v4 code
- [ ] Start dispatcher & worker

### Day 2: Testing
- [ ] Test simple render
- [ ] Verify job completes
- [ ] Check progress tracking

### Day 3: Production
- [ ] Uninstall v3 addon
- [ ] Install v4 addon
- [ ] Configure & connect
- [ ] Submit production job

### Ongoing
- [ ] Monitor dispatcher logs
- [ ] Keep v3 backup until confident

---

## Getting Help

### Check v4 logs:
```bash
tail -f /tmp/blender_server.log
tail -f /tmp/blender_worker.log
```

### Verify each component:
```bash
# Dispatcher
curl http://localhost:5000/health

# Worker
ps aux | grep single_job_worker

# Blender
Help > Toggle System Console
```

### Compare with v3 behavior:
- v3 blocks during render
- v4 should never block
- If Blender freezes, check dispatcher logs

---

## Summary

| Aspect | v3 | v4 | Migration Impact |
|--------|-----|-----|------------------|
| Architecture | Direct | Queue | Moderate |
| Configuration | Worker IP:Port | Dispatcher IP:Port | Low (same concept) |
| Addon changes | Minor UI | Minor UI | Low |
| Performance | Same | Slightly better (+load balancing) | Positive |
| Reliability | Fragile | Resilient | Positive |
| Difficulty | Easy | Medium | Manageable |

**Bottom line:** v4 is a net positive. Takes ~2 hours to migrate. Rollback available if needed.

---

End of Migration Guide v4
