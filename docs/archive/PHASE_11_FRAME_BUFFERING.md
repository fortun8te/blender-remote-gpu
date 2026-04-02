# Phase 11 — Bounded Ring Buffer Frame Buffering

## Overview

Implements a bounded ring buffer for frame data with automatic FIFO eviction, latency tracking, and metrics collection. Prevents memory leaks caused by unbounded frame queue growth during network congestion.

## Implementation Summary

### 1. Configuration (shared/constants.py)

Added frame buffering parameters:

```python
FRAME_BUFFER_SIZE_MS = 500          # Capacity in milliseconds (500ms worth of frames)
FRAME_BUFFER_MAX_FRAMES = 50        # Absolute max frames in ring buffer
MAX_FRAMES_TO_DROP_PER_SECOND = 10  # Rate limit for overflow logging
FRAME_STALE_THRESHOLD_MS = 500      # Skip frames received >500ms ago
```

### 2. Client-Side: addon/connection.py

#### RingBuffer Class

- **Thread-safe FIFO eviction**: Uses `deque(maxlen=N)` which auto-drops oldest on overflow
- **Timestamp tracking**: Records frame arrival time and server send time for latency calculation
- **Metrics collection**:
  - `buffer_size`: Current frames in buffer
  - `frames_received`: Total frames queued
  - `frames_dropped`: Total frames dropped due to overflow
  - `avg_latency_ms`: Average round-trip latency (last 100 frames)

**Key Methods**:
- `put(frame, timestamp_sent)`: Queue frame, return True if added (False if dropped)
- `get()`: Return latest frame without removing it
- `drain()`: Get all frames (for testing/debugging)
- `get_metrics()`: Return dictionary with buffer statistics

#### Connection Integration

- Replaced `queue.Queue(maxsize=3)` with `RingBuffer(max_frames=50)`
- Added rate-limited logging: logs overflow at most 10 times/second
- Frame buffer metrics accessible via `conn._frame_buffer.get_metrics()`

### 3. Server-Side: server/server.py

#### ClientFrameBuffer Class

Tracks frames queued for a single client connection:

**Key Methods**:
- `put(frame_data, metadata)`: Queue frame (async), return True if added
- `get_metrics()`: Return buffer statistics
- `should_report_overflow()`: Rate-limited overflow reporting

#### RenderServer Integration

- **Per-client tracking**: Each `handle_client()` creates dedicated `ClientFrameBuffer`
- **Overflow detection**: Logs when buffer exceeds `FRAME_BUFFER_MAX_FRAMES`
- **Status messages**: Sends `STATUS` message with `BUFFER_FULL` when overflow detected
- **Final metrics**: Logs session summary on disconnect

**Updated Signatures**:
```python
async def _handle_message(self, ws, msg_type, data, binary, session_id, frame_buffer)
async def _handle_viewport_start(self, ws, data, session_id, frame_buffer)
async def _handle_viewport_camera(self, ws, data, session_id, frame_buffer)
async def _viewport_render_loop(self, ws, blend_path, view_matrix, proj_matrix,
                               resolution, max_samples, operation_id, session_id, frame_buffer)
```

### 4. Client-Side: addon/engine.py

#### Stale Frame Detection (view_draw)

- Checks `timestamp_sent` in frame metadata
- Skips frames with latency > `FRAME_STALE_THRESHOLD_MS` (500ms)
- Falls back to cached frame if current is stale
- Logs latency every ~6 frames for monitoring

**Code**:
```python
timestamp_sent = meta.get("timestamp_sent")
if timestamp_sent is not None:
    latency_ms = (time.time() - timestamp_sent) * 1000
    if latency_ms > FRAME_STALE_THRESHOLD_MS:
        logger.debug(f"Skipping stale frame (latency {latency_ms:.0f}ms)")
        # Draw cached frame instead
```

## Data Flow

### Frame Transmission

1. **Server renders frame** → captures `time.time()` as `timestamp_sent`
2. **Server queues frame** to `ClientFrameBuffer` (FIFO eviction if full)
3. **Server sends `FRAME_VIEWPORT`** message with `timestamp_sent` in metadata
4. **Client receives frame** → `RingBuffer.put(frame, timestamp_sent)`
5. **Client measures latency** = `time.time() - timestamp_sent`

### Overflow Behavior

**Server-side:**
- If `ClientFrameBuffer.buffer_size >= FRAME_BUFFER_MAX_FRAMES`:
  - New frame is NOT added (oldest is dropped by `deque(maxlen=50)`)
  - `frames_dropped` counter incremented
  - Rate-limited log message at ~10 msgs/sec
  - Optional: Sends `STATUS` message to client

**Client-side:**
- If `RingBuffer.buffer_size >= FRAME_BUFFER_MAX_FRAMES`:
  - New frame is NOT added (oldest is dropped by `deque(maxlen=50)`)
  - `frames_dropped` counter incremented
  - Rate-limited log message at ~10 msgs/sec

### Stale Frame Handling

- Client detects frames with `latency_ms > 500`
- Skips decoding and rendering
- Falls back to last good frame
- Prevents displaying outdated renders

## Metrics & Monitoring

### Available Metrics

```python
# Client-side (addon)
metrics = conn._frame_buffer.get_metrics()
{
    "buffer_size": 12,
    "frames_received": 1523,
    "frames_dropped": 45,
    "avg_latency_ms": 85.3,
    "max_frames": 50
}

# Server-side
metrics = await frame_buffer.get_metrics()
{
    "client_addr": "192.168.1.100:54321",
    "buffer_size": 8,
    "frames_queued": 512,
    "frames_dropped": 3,
    "max_frames": 50
}
```

### Logging

**Client logs:**
```
WARNING: Frame buffer overflow: 45 frames dropped, buffer_size=50, avg_latency=85.3ms
DEBUG: Frame latency: 82.5ms, buffer_metrics: {'buffer_size': 12, ...}
DEBUG: Skipping stale frame (latency 520ms > 500ms)
```

**Server logs:**
```
INFO: [session_id] Frame buffer overflow: dropped=3, queued=512, buffer_size=50
INFO: Client 192.168.1.100:54321 session summary: {'frames_dropped': 3, ...}
```

## Backward Compatibility

- All changes are additive (new classes, new metrics)
- Existing `Connection` API unchanged (except internal queue replaced)
- Protocol unchanged (new `timestamp_sent` field is optional)
- If server doesn't send `timestamp_sent`, latency tracking gracefully disabled

## Configuration

All parameters are centralized in `shared/constants.py` and can be tuned:

```python
# Adjust buffer capacity (fewer frames = more drops, but less latency)
FRAME_BUFFER_MAX_FRAMES = 50  # Change to 25 for very low latency, 100 for high throughput

# Adjust stale frame threshold (higher = tolerate more latency)
FRAME_STALE_THRESHOLD_MS = 500  # Change to 1000 for slow networks

# Adjust logging frequency
MAX_FRAMES_TO_DROP_PER_SECOND = 10  # Change to 5 for less noise
```

## Testing

Comprehensive tests in `tests/test_frame_buffer.py`:

1. **Basic operations**: Add, retrieve, get metrics
2. **Overflow behavior**: FIFO eviction on full buffer
3. **Latency tracking**: Timestamp-based latency calculation
4. **Thread safety**: Concurrent producer/consumer stress test
5. **Async operations**: ClientFrameBuffer async methods
6. **Network jitter**: Simulated variable latency scenario

### Run Tests

```bash
cd /Users/mk/Downloads/blender-remote-gpu
python tests/test_frame_buffer.py
```

## Advantages Over Previous Implementation

| Aspect | Before | After |
|--------|--------|-------|
| Buffer | `queue.Queue(maxsize=3)` | `RingBuffer(maxlen=50)` |
| Overflow behavior | N/A (blocks or drops) | Automatic FIFO eviction |
| Latency tracking | None | Per-frame via timestamps |
| Metrics | Limited | Detailed (frames_dropped, avg_latency, etc.) |
| Stale frame handling | None | Detected and skipped |
| Memory safety | Small queue, risk of blocking | Bounded by FRAME_BUFFER_MAX_FRAMES |
| Logging | Limited | Rate-limited detailed overflow reports |

## Known Limitations

1. **No adaptive buffering**: Buffer size is fixed at config time
   - Future: Could implement adaptive sizing based on network conditions

2. **No network-aware dropping**: Drops oldest frames regardless of importance
   - Future: Could prioritize keyframes or certain quality tiers

3. **Latency measured end-to-end**: Includes client processing
   - Future: Could separate network vs. processing latency with additional timestamps

## Next Steps (Future Phases)

- [ ] Adaptive buffer sizing based on congestion detection
- [ ] Per-frame quality metadata (keyframe vs. intermediate)
- [ ] Network jitter estimation and QoS adaptation
- [ ] Client-side performance metrics dashboard
- [ ] Server-side buffer depth visualization in admin UI

## Files Modified

1. **shared/constants.py** — Added frame buffer configuration
2. **addon/connection.py** — Added RingBuffer class, updated Connection
3. **server/server.py** — Added ClientFrameBuffer class, updated RenderServer
4. **addon/engine.py** — Added stale frame detection in view_draw()
5. **tests/test_frame_buffer.py** — New comprehensive test suite
