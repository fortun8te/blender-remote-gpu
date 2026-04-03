# Agent R3: Dispatcher Job API Contract

## Overview
Blender addon now uses stateless job dispatcher API instead of persistent worker connection.

## JobDispatcherClient API

### Constructor
```python
dispatcher = JobDispatcherClient(server_ip="100.74.135.83", server_port=9876)
```

### Methods

#### 1. ping() → bool
**Purpose:** Test dispatcher connectivity

**HTTP:** `GET /health`

**Response (on success):**
```json
{"status": "ok"}
```

**Returns:** True if reachable, False otherwise

**Usage in addon:**
```python
if dispatcher.ping():
    # Dispatcher is reachable
```

---

#### 2. submit_render_job(scene_path, width, height, samples) → dict or None
**Purpose:** Submit a render job to dispatcher

**Parameters:**
- `scene_path` (str): Full path to .blend file on dispatcher machine
- `width` (int): Output image width in pixels
- `height` (int): Output image height in pixels
- `samples` (int): Cycles samples

**HTTP:** `POST /render_job`
```json
{
  "scene_path": "/path/to/scene.blend",
  "width": 1920,
  "height": 1080,
  "samples": 128
}
```

**Response (on success):**
```json
{
  "status": "queued",
  "job_id": "uuid-string-here"
}
```

**Returns:** dict with job_id, or None on error

**Usage in addon:**
```python
result = dispatcher.submit_render_job(
    scene_path="/tmp/blender_xxxxx.blend",
    width=1920,
    height=1080,
    samples=128
)
if result:
    job_id = result["job_id"]
```

---

#### 3. get_job_status(job_id) → dict or None
**Purpose:** Poll job progress

**Parameters:**
- `job_id` (str): Job ID from submit_render_job response

**HTTP:** `GET /job_status/{job_id}`

**Response (on success):**
```json
{
  "status": "queued|running|done|error",
  "progress": 0.5,
  "message": "Rendering at 512 samples...",
  "error": null
}
```

**Fields:**
- `status`: One of "queued", "running", "done", "error"
- `progress`: Float 0.0-1.0 (only valid when status is "running")
- `message`: Human-readable status (optional)
- `error`: Error string (only present if status is "error")

**Returns:** dict with status, progress, etc., or None on network error

**Usage in addon:**
```python
# Poll loop
while True:
    status = dispatcher.get_job_status(job_id)
    if status["status"] == "done":
        break
    elif status["status"] == "error":
        print(f"Error: {status['error']}")
        break
    else:
        progress = status.get("progress", 0.0)
        # Update UI progress bar: 0.2 + (progress * 0.65)
        ui_progress = 0.2 + (progress * 0.65)
```

---

#### 4. get_job_result(job_id) → dict or None
**Purpose:** Fetch completed render result

**Parameters:**
- `job_id` (str): Job ID from submit_render_job response

**HTTP:** `GET /job_result/{job_id}`

**Response (on success):**
```json
{
  "status": "success",
  "image_path": "/path/to/render.png",
  "file_size": 1024000
}
```

**Or on failure:**
```json
{
  "status": "error",
  "error": "Job not found or still processing"
}
```

**Fields:**
- `status`: "success" or "error"
- `image_path`: Full path to PNG on dispatcher machine (success only)
- `file_size`: PNG file size in bytes (success only)
- `error`: Error string (error only)

**Returns:** dict with status and image_path, or None on network error

**Usage in addon:**
```python
result = dispatcher.get_job_result(job_id)
if result and result["status"] == "success":
    # Read PNG from file system (assumes mounted or accessible path)
    with open(result["image_path"], "rb") as f:
        png_bytes = f.read()
    # Display in Blender's render buffer
```

---

## Response Codes

All endpoints return JSON with appropriate HTTP status:

| Status | Meaning |
|--------|---------|
| 200 OK | Request succeeded |
| 400 Bad Request | Invalid parameters |
| 404 Not Found | Job ID not found |
| 500 Internal Server Error | Dispatcher error |
| Connection timeout | Network error (addon returns None) |

---

## Addon Integration

### RemoteRenderEngine._dispatcher
Global dispatcher client instance. Set by `REMOTEGPU_OT_connect` operator.

### Render Flow
```python
# 1. Connect (creates _dispatcher)
dispatcher = RemoteRenderEngine._dispatcher

# 2. Save scene locally
scene_path = "/tmp/blender_xxxxx.blend"

# 3. Submit job
job_result = dispatcher.submit_render_job(scene_path, 1920, 1080, 128)
job_id = job_result["job_id"]

# 4. Poll status (with timeout)
start = time.time()
while time.time() - start < 600:  # 10 minute timeout
    status = dispatcher.get_job_status(job_id)
    if status["status"] == "done":
        break
    time.sleep(1.0)

# 5. Fetch result
result = dispatcher.get_job_result(job_id)
png_bytes = read_file(result["image_path"])

# 6. Display in Blender
display_png_in_render_buffer(png_bytes, width, height)
```

---

## Error Handling

The addon handles:
1. Network errors (timeout, connection refused) → None return
2. Missing job (404) → None return
3. Job errors (render failed) → status="error" with error message
4. Render timeout (10+ minutes) → User error message

No persistent state is maintained. Addon can safely reconnect between renders.

---

## Future Extensions

Once dispatcher stabilizes, can add:
- Streaming progress updates (WebSocket)
- HTTP result download (instead of file path)
- Batch job submission
- Job cancellation endpoint
- Queue status endpoint (/queue_status)
