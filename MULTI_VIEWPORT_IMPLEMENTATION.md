# Multi-Viewport Implementation (Phase 12)

## Overview

Robust multi-viewport handling allows users to open 2-4 Blender viewports simultaneously, each rendering live with intelligent priority-based resource allocation.

**Key Features:**
- Per-viewport frame queues with FIFO eviction
- Priority-based resolution scaling (P0 full, P1+ reduced)
- Viewport registry tracking IDs, resolution, zoom
- Server fallback mode if can't keep up
- Resolution mismatch detection
- Zoom-aware rendering (50-200%)

---

## Architecture

### 1. Client-Side (Addon)

#### `addon/viewport_registry.py` (NEW)
Central registry for managing viewport lifecycle.

**Key Classes:**
- `ViewportState`: Per-viewport metadata
  - `viewport_id`: Stable hash from region space_data
  - `resolution`: (width, height)
  - `zoom_level`: Client zoom percentage
  - `priority`: 0 = primary, 1+ = secondary
  - `get_adjusted_resolution()`: Returns resolution for zoom level

- `ViewportRegistry`: Global singleton
  - `register_viewport(region, zoom)`: Add/update viewport
  - `unregister_viewport(viewport_id)`: Remove viewport
  - `get_primary_viewport()`: Return P0 viewport
  - `get_viewport_count()`: Active viewport count
  - `should_drop_frame(viewport_id)`: Check if frame can be dropped

**Usage:**
```python
from addon.viewport_registry import get_viewport_registry

registry = get_viewport_registry()
vp_state = registry.register_viewport(context.region, zoom_level=100.0)
viewport_id = vp_state.viewport_id
priority = vp_state.priority

# Later, unregister when viewport closes
registry.unregister_viewport(viewport_id)
```

#### `addon/connection.py` (UPDATED)
Enhanced to support per-viewport frame queues.

**New Methods:**
```python
# Register frame queue for a viewport
queue = conn.register_viewport_queue(viewport_id, max_frames=50)

# Get frame for specific viewport
frame = conn.get_frame(viewport_id=viewport_id)

# Unregister when viewport closes
conn.unregister_viewport_queue(viewport_id)

# Send viewport updates with viewport_id
conn.send_viewport_start(..., viewport_id=viewport_id, zoom_level=100.0)
conn.send_viewport_camera(..., viewport_id=viewport_id)
```

**Frame Routing:**
- Each viewport has own RingBuffer (50 frame capacity)
- Frames routed by `viewport_id` in metadata
- If queue full, drops oldest frame for that viewport only
- Fallback to global buffer for legacy single-viewport mode

#### `addon/engine.py` (UPDATED)
Viewport registration and frame rendering.

**Key Changes:**
- `_viewport_id`: Per-engine instance viewport ID
- `_viewport_registry`: Global registry reference
- `_textures`: Dict mapping viewport_id -> GPU texture cache
- `view_draw()`: Registers viewport, fetches frame for that VP
- `_draw_frame()`: Caches texture per viewport

**Flow:**
```
view_draw()
  ├─ registry.register_viewport(region, zoom)
  ├─ Get viewport_id and priority
  ├─ conn.get_frame(viewport_id=viewport_id)
  ├─ Decode JPEG
  ├─ _draw_frame(..., viewport_id)
  │   └─ Cache texture in _textures[viewport_id]
  └─ tag_redraw()
```

---

### 2. Server-Side (New)

#### `server/viewport_manager.py` (NEW)
Tracks active viewports and assigns priorities.

**Key Classes:**
- `ServerViewportState`: Per-viewport server state
  - `viewport_id`: Client's viewport ID
  - `priority`: 0 = primary, 1+ = secondary
  - `target_resolution`: Adjusted resolution for priority
  - `get_adjusted_resolution()`: P0 = full, P1-2 = 50%, P3+ = 33%

- `ViewportManager`: Global singleton
  - `register_viewport(viewport_id, resolution, zoom)`: Add viewport
  - `unregister_viewport(viewport_id)`: Remove viewport
  - `get_all_viewports()`: Return ordered list
  - `should_fallback_to_primary()`: Check if overloaded
  - `record_frame_sent(viewport_id, size)`: Track stats

**Usage:**
```python
from server.viewport_manager import get_viewport_manager

mgr = get_viewport_manager()
vp_state = mgr.register_viewport(viewport_id, (1920, 1080), zoom=100.0)

# Resolution automatically adjusted based on priority
target_res = vp_state.target_resolution  # P0: (1920,1080), P1: (960,540)

# Track frames
mgr.record_frame_sent(viewport_id, frame_size_bytes)
```

---

### 3. Protocol Updates

#### `shared/protocol.py` (UPDATED)

**New Message Types:**
```python
VIEWPORT_PRIORITY = 0x83    # Server -> Client: priority assignment
  {
    "viewport_id": str,
    "priority": int,
    "target_resolution": [w, h],
  }

FALLBACK_MODE = 0x84        # Server -> Client: fallback to primary only
  {
    "primary_viewport_id": str,
    "reason": str,
  }
```

**Updated FRAME_VIEWPORT:**
```python
FRAME_VIEWPORT = 0x80
  {
    "viewport_id": str,              # NEW (Phase 12)
    "timestamp_sent": float,
    "width": int,
    "height": int,
    # ... other fields
  }
```

**Updated VIEWPORT_START:**
```python
VIEWPORT_START = 0x20
  {
    "view_matrix": [...],
    "proj_matrix": [...],
    "resolution": [w, h],
    "samples": int,
    "viewport_id": str,              # NEW (Phase 12)
    "zoom_level": float,             # NEW (Phase 12)
    # ... optional timeline params
  }
```

---

## Integration with Existing Code

### Server-Side Frame Loop
In `server/server.py`, update viewport start handler:

```python
async def _handle_viewport_start(self, ws, data, session_id, frame_buffer):
    # Phase 12: Extract multi-viewport parameters
    viewport_id = data.get("viewport_id")
    zoom_level = data.get("zoom_level", 100.0)
    resolution = tuple(data.get("resolution", [800, 600]))

    # Register with viewport manager
    from server.viewport_manager import get_viewport_manager
    mgr = get_viewport_manager()
    vp_state = mgr.register_viewport(viewport_id, resolution, zoom_level)

    # Use target_resolution for rendering
    render_resolution = vp_state.target_resolution

    # Start render with target resolution
    await self._viewport_render_loop(
        ws, blend_path, view_matrix, proj_matrix,
        render_resolution,  # NOT original resolution
        max_samples, operation_id, session_id, frame_buffer,
        viewport_id=viewport_id  # NEW: pass viewport_id
    )
```

### Frame Delivery
When sending frames in `_viewport_render_loop`:

```python
# Encode frame to JPEG
jpeg_data = encode_frame(...)

# Include viewport_id in metadata
await self._send(ws, MsgType.FRAME_VIEWPORT, {
    "viewport_id": viewport_id,           # NEW
    "width": width,
    "height": height,
    "timestamp_sent": time.time(),
}, binary=jpeg_data)

# Track stats
mgr.record_frame_sent(viewport_id, len(jpeg_data))
```

### Viewport Cleanup
When client disconnects:

```python
# In handle_client finally block
mgr.unregister_viewport(viewport_id)
```

---

## Resolution Scaling Strategy

### Client-Side (Zoom Adjustment)
```
Zoom < 50%   →  Render at 50% resolution (1920 → 960)
Zoom 50-200% →  Render at 100% resolution
Zoom > 200%  →  Render at 200% resolution (1920 → 3840)
```

### Server-Side (Priority Adjustment)
```
Viewport 0 (Primary)   →  Target: Full resolution  (P0 priority)
Viewport 1 (Secondary) →  Target: 50% resolution   (P1 priority)
Viewport 2 (Tertiary)  →  Target: 50% resolution   (P2 priority)
Viewport 3+ (Extra)    →  Target: 33% resolution   (P3+ priority)
```

**Combined Adjustment:**
1. Client computes zoom adjustment
2. Server applies priority adjustment
3. Final resolution = base × zoom_factor × priority_factor

---

## Fallback Mode

If server can't render 4 viewports at acceptable framerate:

1. Server sends `FALLBACK_MODE` message with reason
2. Client stops rendering secondary viewports
3. Only primary (P0) viewport continues live
4. Secondary viewports show cached frame at 5 FPS (update every 200ms)

```python
if mgr.should_fallback_to_primary():
    await self._send(ws, MsgType.FALLBACK_MODE, {
        "primary_viewport_id": mgr.get_primary_viewport_id(),
        "reason": "GPU overloaded",
    })
    # Stop rendering secondary viewports
    self._cancel_secondary_viewports()
```

---

## Flow Diagram

### Multi-Viewport Startup
```
User opens 2 Blender viewports with Remote Cycles
         ↓
Viewport 1 view_draw() called
  ├─ registry.register_viewport(region, zoom=100) → VP0, priority=0
  ├─ conn.register_viewport_queue("vp0_hash")
  └─ conn.send_viewport_start(..., viewport_id="vp0_hash")
         ↓
Server receives VIEWPORT_START
  ├─ mgr.register_viewport("vp0_hash", (1920,1080), 100.0) → priority=0
  ├─ target_resolution = (1920, 1080)  # Full
  └─ Start rendering at (1920, 1080)
         ↓
Viewport 2 view_draw() called
  ├─ registry.register_viewport(region, zoom=100) → VP1, priority=1
  ├─ conn.register_viewport_queue("vp1_hash")
  └─ conn.send_viewport_start(..., viewport_id="vp1_hash")
         ↓
Server receives VIEWPORT_START (VP1)
  ├─ mgr.register_viewport("vp1_hash", (1920,1080), 100.0) → priority=1
  ├─ target_resolution = (960, 540)   # 50%
  ├─ Send VIEWPORT_PRIORITY message
  └─ Start rendering at (960, 540)
         ↓
Server rendering both viewports
  ├─ Prioritize VP0 (primary): 60 FPS
  └─ Render VP1 (secondary): 30 FPS at 50% resolution
         ↓
Client receives frames:
  ├─ VP0: frame for viewport 0 (via _viewport_queues["vp0_hash"])
  └─ VP1: frame for viewport 1 (via _viewport_queues["vp1_hash"])
```

### Viewport Zoom Handling
```
User zooms viewport to 25% (0.25x)
         ↓
view_draw() detects zoom change
  ├─ registry.get_viewport("vp0")
  ├─ vp_state.zoom_level = 25.0
  ├─ vp_state.get_adjusted_resolution() → (960, 540)  # 50% of 1920x1080
  └─ conn.send_viewport_camera(..., zoom_level=25.0)
         ↓
Server receives camera update with zoom_level=25.0
  ├─ vp_state.zoom_level = 25.0
  ├─ target_resolution = (960, 540) × 0.5 = (480, 270)
  └─ Re-render at (480, 270)
         ↓
User sees zoomed-out view at reduced resolution
(faster rendering, trade-off for large viewport)
```

---

## Testing Checklist

- [ ] Single viewport: Works as before (backward compatible)
- [ ] Two viewports open: Both render, different frame rates
- [ ] Three viewports open: P0 60FPS, P1-2 30FPS, P3 15FPS
- [ ] Four viewports open: Server prioritizes P0, reduces P1-3 resolution
- [ ] Viewport resize: Server adapts resolution
- [ ] Viewport zoom change: Resolution scales with zoom
- [ ] Viewport close: Registry cleans up, priorities reassign
- [ ] Server overload: Fallback mode triggers, primary only
- [ ] Frame buffer overflow: Oldest frames dropped per viewport
- [ ] Resolution mismatch: Warning logged, frame skipped

---

## Backward Compatibility

All changes are backward compatible:

1. **Single-viewport mode**: Works unchanged, viewport_id optional
2. **Frame queues**: If viewport_id not provided, use global buffer
3. **Protocol**: New fields are optional; server ignores if not present
4. **Registry**: Global singleton, transparent to existing code

No breaking changes to existing RenderEngine, Connection, or Server APIs.

---

## Future Enhancements

1. **Per-viewport denoiser settings**: Different denoiser per viewport
2. **Selective rendering**: Disable rendering for non-visible viewports
3. **GPU affinity**: Assign viewports to different GPU cores/threads
4. **Adaptive quality**: Reduce samples on secondary viewports
5. **Network-aware scaling**: Reduce resolution if network saturated
6. **Render target preview**: Show miniature of each viewport in UI
