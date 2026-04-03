# Test Plan v4.0 — Dispatcher Architecture

## Overview
v4.0 introduces a complete rewrite with **dispatcher architecture**. The main application acts as an HTTP API server that queues rendering jobs and distributes them to a worker pool.

**Key Change from v3 (b37):**
- Old: Blender addon directly spawned Cycles processes on the worker machine
- New: Blender addon submits jobs to dispatcher via HTTP, dispatcher manages queue + workers

---

## Phase 1: Syntax & Build Verification

### ✅ All Python files compile (verified)
```bash
python3 -m py_compile server.py render_worker.py job_queue.py single_job_worker.py
python3 -m py_compile remote_gpu_render/*.py
# Result: PASS — no syntax errors
```

---

## Phase 2: Manual Integration Tests

### Test 2.1 — Start Dispatcher
**Goal:** Verify dispatcher HTTP server starts without error

```bash
cd /Users/mk/Downloads/blender-remote-gpu
python3 server.py
```

**Expected Output:**
```
[RemoteGPU Dispatcher] Starting server on 0.0.0.0:5000...
[RemoteGPU Dispatcher] Server ready. Waiting for jobs.
```

**Acceptance:** Server listens on 0.0.0.0:5000, no crashes

---

### Test 2.2 — Health Endpoint
**Goal:** Verify dispatcher is accessible via HTTP

```bash
curl -s http://localhost:5000/health | jq .
```

**Expected Response:**
```json
{
  "status": "ready",
  "queue_size": 0,
  "workers_online": 0,
  "version": "v4",
  "timestamp": "2026-04-03T..."
}
```

**Acceptance:** Returns valid JSON with status=ready

---

### Test 2.3 — Submit Render Job via cURL
**Goal:** Verify job submission returns job_id

**Setup:** Have a valid .blend scene file available (e.g., `/tmp/test.blend`)

```bash
curl -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{
    "scene_path": "/tmp/test.blend",
    "output_path": "/tmp/render_output.exr",
    "frame": 1,
    "samples": 128,
    "resolution": [1280, 720]
  }' | jq .
```

**Expected Response:**
```json
{
  "job_id": "job_12345abcde",
  "status": "queued",
  "position": 1,
  "submitted_at": "2026-04-03T18:30:00Z"
}
```

**Acceptance:** Returns unique job_id, status=queued

---

### Test 2.4 — Poll Job Status
**Goal:** Verify status endpoint works during job lifecycle

Using job_id from Test 2.3:

```bash
curl -s http://localhost:5000/job_status/job_12345abcde | jq .
```

**Possible Responses:**

#### While queued:
```json
{
  "job_id": "job_12345abcde",
  "status": "queued",
  "position": 1,
  "progress": null
}
```

#### While rendering:
```json
{
  "job_id": "job_12345abcde",
  "status": "rendering",
  "progress": {
    "current_sample": 64,
    "total_samples": 128,
    "percent": 50
  },
  "worker_id": "worker_001"
}
```

#### Completed:
```json
{
  "job_id": "job_12345abcde",
  "status": "complete",
  "result": {
    "output_path": "/tmp/render_output.exr",
    "samples_rendered": 128,
    "render_time_seconds": 45.3,
    "worker_id": "worker_001"
  },
  "completed_at": "2026-04-03T18:31:15Z"
}
```

**Acceptance:** Returns current job state, progress updates during render

---

### Test 2.5 — Get Job Result
**Goal:** Verify result retrieval after job completes

```bash
curl -s http://localhost:5000/job_result/job_12345abcde | jq .
```

**Expected Response:**
```json
{
  "job_id": "job_12345abcde",
  "status": "complete",
  "output_file": "/tmp/render_output.exr",
  "render_time_seconds": 45.3,
  "samples_rendered": 128,
  "file_size_bytes": 5242880
}
```

**Acceptance:** Returns valid file path, render metadata

---

### Test 2.6 — Error Handling
**Goal:** Verify dispatcher handles invalid requests gracefully

#### Invalid scene path:
```bash
curl -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"scene_path": "/nonexistent/scene.blend"}' | jq .
```

**Expected Response:**
```json
{
  "error": "Scene file not found",
  "code": "SCENE_NOT_FOUND",
  "details": "/nonexistent/scene.blend does not exist"
}
```

#### Missing required fields:
```bash
curl -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"output_path": "/tmp/out.exr"}' | jq .
```

**Expected Response:**
```json
{
  "error": "Missing required field: scene_path",
  "code": "VALIDATION_ERROR"
}
```

#### Query non-existent job:
```bash
curl -s http://localhost:5000/job_status/job_nonexistent | jq .
```

**Expected Response:**
```json
{
  "error": "Job not found",
  "code": "JOB_NOT_FOUND",
  "job_id": "job_nonexistent"
}
```

**Acceptance:** All errors return HTTP error codes (4xx/5xx) with descriptive JSON

---

## Phase 3: Worker Pool Tests

### Test 3.1 — Start Single Worker
**Goal:** Verify worker can connect to dispatcher

**Terminal 1:** Dispatcher running (from Test 2.1)

**Terminal 2:**
```bash
python3 single_job_worker.py --dispatcher http://localhost:5000 --worker_id worker_001
```

**Expected Output:**
```
[Worker worker_001] Connecting to dispatcher at http://localhost:5000
[Worker worker_001] Registration successful
[Worker worker_001] Polling for jobs...
```

**Acceptance:** Worker registers with dispatcher, polls for jobs

---

### Test 3.2 — Submit Job → Worker Processes
**Goal:** Verify end-to-end job flow

**Setup:** Have dispatcher running (Test 2.1) and worker running (Test 3.1)

**Action:** Submit job via cURL (Test 2.3)

**Worker Expected Output:**
```
[Worker worker_001] Job assigned: job_12345abcde
[Worker worker_001] Loading scene: /tmp/test.blend
[Worker worker_001] Rendering frame 1, samples 128...
[Worker worker_001] Render progress: 32/128 samples (25%)
[Worker worker_001] Render progress: 64/128 samples (50%)
[Worker worker_001] Render progress: 96/128 samples (75%)
[Worker worker_001] Render complete: 128/128 samples
[Worker worker_001] Saving output: /tmp/render_output.exr
[Worker worker_001] Job complete. Polling for next job...
```

**Dispatcher Output:**
```
[Dispatcher] Job job_12345abcde assigned to worker_001
[Dispatcher] Job job_12345abcde progress: 25%
[Dispatcher] Job job_12345abcde progress: 50%
[Dispatcher] Job job_12345abcde progress: 75%
[Dispatcher] Job job_12345abcde complete
```

**Acceptance:** Job flows from submit → worker assignment → progress → completion

---

## Phase 4: Blender Addon Integration Tests

### Test 4.1 — Install Addon
**Goal:** Verify addon installs cleanly

1. Build addon:
   ```bash
   cd /Users/mk/Downloads/blender-remote-gpu
   ./build_addon.sh
   ```

2. Install in Blender:
   - Edit > Preferences > Add-ons > Install
   - Select `remote_gpu_render.zip`
   - Enable "Remote GPU Render"

**Expected:** Addon appears in Preferences without errors

**Console check:**
```
[RemoteGPU] Registered v2.0.0 v4 (live preview active)
```

**Acceptance:** Addon registers successfully

---

### Test 4.2 — Configure Dispatcher IP
**Goal:** Verify addon can be pointed at dispatcher

1. In Blender Preferences > Remote GPU Render:
   - Set Dispatcher IP to `localhost`
   - Set Port to `5000`

2. Click "Connect to Dispatcher"

**Expected Output in System Console:**
```
[RemoteGPU] Connecting to dispatcher at http://localhost:5000...
[RemoteGPU] Connected! Dispatcher healthy.
```

**Addon UI:**
- Status shows "CONNECTED"
- Latency displays (e.g., "12ms")

**Acceptance:** Addon connects to dispatcher, shows latency

---

### Test 4.3 — Submit Render from Addon
**Goal:** Verify addon can submit jobs to dispatcher

1. In Blender N-panel > Remote GPU:
   - Ensure scene is saved locally (e.g., `~/test_scene.blend`)
   - Dispatcher connected
   - Click "Upload Scene"
   - Click "Start Live Preview"

**Expected:**
- Scene file uploaded to dispatcher
- Live preview window opens
- Render starts on remote worker
- Preview updates as samples progress

**Console:**
```
[RemoteGPU] Uploading scene: /Users/mk/test_scene.blend
[RemoteGPU] Scene uploaded successfully
[RemoteGPU] Starting live preview...
[RemoteGPU] Job assigned: job_xyz123
[RemoteGPU] Render progress: 12%
```

**Acceptance:** Scene uploads, render jobs submit, preview displays

---

### Test 4.4 — Handle Disconnect
**Goal:** Verify addon gracefully handles network issues

1. While render is in progress, kill dispatcher:
   ```bash
   kill $(ps aux | grep "server.py" | grep -v grep | awk '{print $2}')
   ```

2. Check addon response

**Expected:**
- Addon shows "NOT CONNECTED"
- Error message in console
- Live preview pauses
- Can reconnect after dispatcher restarts

**Acceptance:** Graceful degradation, no crashes

---

## Phase 5: Edge Cases & Error Scenarios

### Test 5.1 — Invalid Scene Path
**Dispatcher running, worker running**

```bash
curl -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{
    "scene_path": "/invalid/path/scene.blend",
    "output_path": "/tmp/out.exr",
    "frame": 1
  }' | jq .
```

**Expected:** Error response, job never queued

---

### Test 5.2 — Render Timeout
**Goal:** Verify worker timeout handling

1. Submit job with very high sample count (10000 samples)
2. Worker has 30-second timeout
3. Wait for timeout

**Expected:**
- Worker cancels job after 30 seconds
- Job status returns "timeout"
- Dispatcher marks job as failed
- Output file partial/missing

---

### Test 5.3 — Multiple Workers
**Goal:** Verify load balancing

**Setup:** 3 workers + dispatcher

**Action:** Submit 5 jobs rapidly
```bash
for i in {1..5}; do
  curl -X POST http://localhost:5000/submit \
    -H "Content-Type: application/json" \
    -d '{"scene_path": "/tmp/test.blend", "output_path": "/tmp/out_'$i'.exr"}' &
done
```

**Expected:**
- Jobs distributed across workers
- Each worker processes ~1-2 jobs
- Queue clears as workers finish

---

### Test 5.4 — Worker Crash Recovery
**Goal:** Verify dispatcher detects dead workers

1. Submit job (worker picks it up)
2. Kill worker mid-render
3. Check job status

**Expected:**
- Dispatcher detects worker timeout (no heartbeat for 10s)
- Job reassigned to new worker
- Render completes successfully

---

## Phase 6: Performance Baseline

### Test 6.1 — Single-Core Render
**Setup:** 1 worker, simple scene

```bash
time curl -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{
    "scene_path": "/tmp/test_simple.blend",
    "output_path": "/tmp/render.exr",
    "samples": 256,
    "resolution": [1280, 720]
  }' | jq .job_id

# Wait for completion
sleep 60
curl -s http://localhost:5000/job_result/job_xyz | jq .render_time_seconds
```

**Target:** < 45 seconds for 256 samples @ 1280x720

---

### Test 6.2 — Multi-Worker Throughput
**Setup:** 3 workers

**Action:** Submit 10 jobs sequentially, measure total time

**Target:** 3x throughput vs single worker (within 15% variance)

---

## Acceptance Criteria

### Must Pass (Blocking)
- [x] All Python files compile (Phase 1)
- [ ] Dispatcher starts without error (Test 2.1)
- [ ] Health endpoint responds (Test 2.2)
- [ ] Job submission returns job_id (Test 2.3)
- [ ] Job status polling works (Test 2.4)
- [ ] Worker connects and processes jobs (Tests 3.1-3.2)
- [ ] Blender addon connects to dispatcher (Test 4.2)
- [ ] Error responses are valid JSON (Test 2.6)

### Should Pass (Nice to Have)
- [ ] Live preview renders in Blender (Test 4.3)
- [ ] Multiple workers load-balance (Test 5.3)
- [ ] Worker crash recovery works (Test 5.4)
- [ ] Performance baseline met (Test 6.1)

### Known Limitations
- Blender addon only supports scenes on local disk (no .blend files > 2GB)
- Worker pool limited by system CPU cores
- No GPU affinity yet (jobs can land on any GPU)
- No job prioritization (FIFO queue only)

---

## Test Execution Log

| Test | Date | Result | Notes |
|------|------|--------|-------|
| Phase 1 (Syntax) | 2026-04-03 | PASS | 0 syntax errors |
| Test 2.1 | | | |
| Test 2.2 | | | |
| Test 2.3 | | | |
| Test 2.4 | | | |
| Test 2.5 | | | |
| Test 2.6 | | | |
| Test 3.1 | | | |
| Test 3.2 | | | |
| Test 4.1 | | | |
| Test 4.2 | | | |
| Test 4.3 | | | |
| Test 4.4 | | | |
| Test 5.1 | | | |
| Test 5.2 | | | |
| Test 5.3 | | | |
| Test 5.4 | | | |
| Test 6.1 | | | |
| Test 6.2 | | | |

---

## Running the Full Test Suite

```bash
#!/bin/bash
# Run all tests in order

echo "=== Phase 1: Syntax Check ==="
python3 -m py_compile server.py render_worker.py job_queue.py single_job_worker.py
python3 -m py_compile remote_gpu_render/*.py
echo "✓ Phase 1 complete"

echo ""
echo "=== Phase 2: Dispatcher Tests ==="
echo "Start dispatcher in Terminal 1:"
echo "  python3 server.py"
echo ""
echo "In Terminal 2, run:"
echo "  curl -s http://localhost:5000/health | jq ."
echo "  curl -X POST http://localhost:5000/submit ..."
echo ""

echo "=== Phase 3: Worker Tests ==="
echo "In Terminal 3, run:"
echo "  python3 single_job_worker.py --dispatcher http://localhost:5000"
echo ""

echo "=== Phase 4: Blender Tests ==="
echo "In Blender:"
echo "  1. Install addon"
echo "  2. Configure dispatcher IP: localhost:5000"
echo "  3. Connect & render"
```

---

## Troubleshooting Guide

### Dispatcher won't start
```
Error: Address already in use: ('0.0.0.0', 5000)
```
**Solution:** Kill existing process
```bash
lsof -i :5000
kill -9 <PID>
```

### Worker connection refused
```
Error: Connection refused connecting to http://localhost:5000
```
**Solution:** Ensure dispatcher is running on correct IP/port

### Blender addon says "NOT CONNECTED"
```
1. Check dispatcher is running (lsof -i :5000)
2. Check IP config in addon preferences
3. Test manually: curl http://localhost:5000/health
4. Check System Console for detailed error
```

### Render stuck in "rendering" state
```
1. Check worker process (ps aux | grep worker)
2. Check worker logs for errors
3. Force timeout: wait 30s, job should reassign
4. Restart worker to clear stuck jobs
```

---

End of Test Plan v4.0
