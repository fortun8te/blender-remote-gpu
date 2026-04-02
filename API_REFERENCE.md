# Animation Timeline Playback — API Reference

## Client API (Mac Addon)

### addon/sync.py

#### `get_current_frame(context: bpy.context) → int`
**Purpose:** Extract the current timeline frame number.

**Parameters:**
- `context`: Blender context (usually `bpy.context`)

**Returns:**
- `int`: Current frame number (e.g., 42)

**Example:**
```python
from addon.sync import get_current_frame
frame = get_current_frame(bpy.context)
print(f"Current frame: {frame}")  # Output: Current frame: 42
```

---

#### `get_frame_range(context: bpy.context) → tuple[int, int, int]`
**Purpose:** Extract the animation frame range and step.

**Parameters:**
- `context`: Blender context

**Returns:**
- `tuple[int, int, int]`: (frame_start, frame_end, frame_step)
  - `frame_start`: First frame of animation (e.g., 1)
  - `frame_end`: Last frame of animation (e.g., 100)
  - `frame_step`: Increment between frames (e.g., 1)

**Example:**
```python
from addon.sync import get_frame_range
start, end, step = get_frame_range(bpy.context)
print(f"Animation range: {start}-{end} (step {step})")
# Output: Animation range: 1-100 (step 1)
```

---

#### `get_playback_fps(context: bpy.context) → float`
**Purpose:** Extract the playback frames-per-second.

**Parameters:**
- `context`: Blender context

**Returns:**
- `float`: Computed FPS value (fps / fps_base)
  - Typical values: 24.0, 25.0, 30.0, 60.0

**Example:**
```python
from addon.sync import get_playback_fps
fps = get_playback_fps(bpy.context)
print(f"Playback FPS: {fps}")  # Output: Playback FPS: 24.0
```

---

### addon/connection.py

#### `Connection.send_timeline_update(frame: int, is_playing: bool) → None`
**Purpose:** Send a timeline frame change to the server (non-blocking).

**Parameters:**
- `frame`: Current frame number (int)
- `is_playing`: Whether timeline is playing (bool)

**Returns:**
- None (message queued, not sent immediately)

**Example:**
```python
from addon.connection import Connection
conn = Connection("ws://192.168.1.100:9876")
conn.connect()

# User scrubs to frame 50
conn.send_timeline_update(frame=50, is_playing=False)

# User presses play
conn.send_timeline_update(frame=50, is_playing=True)
```

---

#### `Connection.send_viewport_start(view_matrix, proj_matrix, resolution, samples=128, **timeline_params) → None`
**Purpose:** Start viewport rendering with optional timeline information.

**Parameters:**
- `view_matrix`: Camera view matrix (list of 16 floats)
- `proj_matrix`: Camera projection matrix (list of 16 floats)
- `resolution`: Render resolution (tuple of 2 ints: width, height)
- `samples`: Sample count (int, default 128)
- **Timeline parameters (optional):**
  - `current_frame`: Current frame number (int)
  - `frame_start`: Animation start frame (int)
  - `frame_end`: Animation end frame (int)
  - `frame_step`: Frame step (int)
  - `fps`: Playback FPS (float)
  - `is_playing`: Whether timeline is playing (bool)

**Returns:**
- None

**Example:**
```python
# Single frame viewport (no timeline)
conn.send_viewport_start(view_matrix, proj_matrix, (1920, 1080), samples=64)

# Viewport with animation metadata
conn.send_viewport_start(
    view_matrix, proj_matrix, (1920, 1080),
    samples=64,
    current_frame=1,
    frame_start=1,
    frame_end=100,
    frame_step=1,
    fps=24.0,
    is_playing=False,
)
```

---

## Server API (Windows Server)

### server/viewport_live.py

#### `LiveViewportRenderer.set_current_frame(frame_current: int) → None`
**Purpose:** Set the current animation frame for the next render.

**Parameters:**
- `frame_current`: Frame number to set (int)

**Returns:**
- None

**Side Effects:**
- Calls `bpy.context.scene.frame_set(frame_current)`
- Triggers evaluation of all timeline-dependent properties (IK, particles, drivers, modifiers)

**Example:**
```python
from server.viewport_live import LiveViewportRenderer
renderer = LiveViewportRenderer()
renderer.setup_scene("/path/to/scene.blend")

# Set to frame 50 before rendering
renderer.set_current_frame(50)

# Now render will use frame 50's evaluated state
jpeg_data = renderer.render_frame(samples=16)
```

---

### server/renderer.py

#### `SubprocessRenderer.render_animation(blend_path, resolution, samples, frame_start, frame_end, frame_step=1, output_format="EXR", on_progress=None) → list[tuple[bytes, dict]]`
**Purpose:** Render an animation sequence frame-by-frame.

**Parameters:**
- `blend_path`: Path to .blend file (str)
- `resolution`: Render resolution (tuple of 2 ints: width, height)
- `samples`: Sample count per frame (int)
- `frame_start`: First frame to render (int)
- `frame_end`: Last frame to render (int)
- `frame_step`: Frame increment (int, default 1). E.g., 2 renders every 2nd frame
- `output_format`: Output format (str, default "EXR"). Options: "PNG", "JPEG", "EXR"
- `on_progress`: Progress callback (callable, default None). Called with (current_frame, total_frames)

**Returns:**
- `list[tuple[bytes, dict]]`: List of (frame_data, metadata) tuples
  - `frame_data`: Bytes of rendered frame image
  - `metadata`: Dictionary with keys:
    - `"frame"`: Frame number (int)
    - `"frame_start"`: Start frame (int)
    - `"frame_end"`: End frame (int)
    - `"total_frames"`: Total frame count (int)
    - `"format"`: Output format (str)

**Raises:**
- Returns empty list on error or timeout

**Example:**
```python
from server.renderer import SubprocessRenderer
renderer = SubprocessRenderer(blender_path="/usr/bin/blender")

# Render frames 1-50 every frame
frames = await renderer.render_animation(
    blend_path="/path/to/scene.blend",
    resolution=(1920, 1080),
    samples=128,
    frame_start=1,
    frame_end=50,
    frame_step=1,
    output_format="EXR",
    on_progress=lambda cur, tot: print(f"Frame {cur}/{tot}"),
)

# frames is now a list of 50 (bytes, dict) tuples
for frame_data, metadata in frames:
    frame_num = metadata["frame"]
    print(f"Rendered frame {frame_num}: {len(frame_data)} bytes")
```

---

#### `SubprocessRenderer._build_animation_script(...) → str`
**Purpose:** Generate a Blender Python script for rendering an animation sequence.

**Parameters:**
- `resolution`: Tuple of (width, height)
- `samples`: Sample count
- `output_format`: Output format ("PNG", "JPEG", "EXR")
- `frame_start`: First frame to render
- `frame_end`: Last frame to render
- `frame_step`: Frame step
- `output_dir`: Directory to save frame files

**Returns:**
- `str`: Blender Python script code

**Example:**
```python
script = renderer._build_animation_script(
    resolution=(1920, 1080),
    samples=64,
    output_format="EXR",
    frame_start=1,
    frame_end=100,
    frame_step=1,
    output_dir="/tmp/render",
)

# Script loops through frames 1-100 and renders each to /tmp/render/frame_0001.exr, etc.
print(script)  # View generated script
```

---

## Protocol Messages

### TIMELINE_UPDATE (0x23)
**Direction:** Client → Server

**Schema:**
```python
{
    "type": 0x23,  # MsgType.TIMELINE_UPDATE
    "frame": int,  # Current frame number
    "is_playing": bool,  # Is timeline playing?
}
```

**Example:**
```python
{
    "type": 0x23,
    "frame": 42,
    "is_playing": True,
}
```

---

### VIEWPORT_START (0x20, extended)
**Direction:** Client → Server

**Schema:**
```python
{
    "type": 0x20,  # MsgType.VIEWPORT_START
    "view_matrix": [16 floats],  # Camera view matrix
    "proj_matrix": [16 floats],  # Camera projection matrix
    "resolution": [int, int],  # [width, height]
    "samples": int,  # Sample count

    # Optional timeline fields:
    "current_frame": int,  # Current frame (optional)
    "frame_start": int,    # Animation start (optional)
    "frame_end": int,      # Animation end (optional)
    "frame_step": int,     # Frame step (optional)
    "fps": float,          # Playback FPS (optional)
    "is_playing": bool,    # Is playing? (optional)
}
```

**Example:**
```python
{
    "type": 0x20,
    "view_matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -10, 1],
    "proj_matrix": [...],
    "resolution": [1920, 1080],
    "samples": 128,
    "current_frame": 1,
    "frame_start": 1,
    "frame_end": 100,
    "frame_step": 1,
    "fps": 24.0,
    "is_playing": False,
}
```

---

### FRAME_FINAL (0x81, extended)
**Direction:** Server → Client
**Binary:** Yes (image data)

**Schema:**
```python
{
    "type": 0x81,  # MsgType.FRAME_FINAL
    "format": str,  # "EXR", "PNG", "JPEG"
    "frame": int,   # Frame number (for animations)
    "frame_start": int,  # Animation start frame
    "frame_end": int,    # Animation end frame
    "total_frames": int, # Total frame count
    # ... binary image data follows ...
}
```

**Example:**
```python
{
    "type": 0x81,
    "format": "EXR",
    "frame": 42,
    "frame_start": 1,
    "frame_end": 100,
    "total_frames": 100,
    # 8MB of EXR image data in binary section
}
```

---

## Constants

### server/renderer.py
```python
ANIMATION_RENDER_TIMEOUT = 3600  # Seconds (1 hour max per animation sequence)
```

---

## Type Signatures

### addon/sync.py
```python
def get_current_frame(context) -> int: ...
def get_frame_range(context) -> tuple[int, int, int]: ...
def get_playback_fps(context) -> float: ...
```

### addon/connection.py
```python
def send_timeline_update(frame: int, is_playing: bool) -> None: ...
def send_viewport_start(
    view_matrix: list,
    proj_matrix: list,
    resolution: tuple,
    samples: int = 128,
    current_frame: int | None = None,
    frame_start: int | None = None,
    frame_end: int | None = None,
    frame_step: int | None = None,
    fps: float | None = None,
    is_playing: bool | None = None,
) -> None: ...
```

### server/viewport_live.py
```python
def set_current_frame(frame_current: int) -> None: ...
```

### server/renderer.py
```python
async def render_animation(
    blend_path: str,
    resolution: tuple[int, int],
    samples: int,
    frame_start: int,
    frame_end: int,
    frame_step: int = 1,
    output_format: str = "EXR",
    on_progress: callable = None,
) -> list[tuple[bytes, dict]]: ...

def _build_animation_script(
    resolution: tuple[int, int],
    samples: int,
    output_format: str,
    frame_start: int,
    frame_end: int,
    frame_step: int,
    output_dir: str,
) -> str: ...
```

---

## Error Handling

All functions handle errors gracefully:

- **Sync functions** (get_current_frame, get_frame_range, get_playback_fps):
  - Raise exceptions only if Blender context invalid
  - Safe to call every frame

- **Connection functions** (send_timeline_update, send_viewport_start):
  - Queue messages non-blocking
  - Never raise exceptions
  - Return immediately

- **Viewport functions** (set_current_frame):
  - Logs errors if scene not loaded
  - Safe to call even if scene is None

- **Renderer functions** (render_animation):
  - Returns empty list on error
  - Enforces ANIMATION_RENDER_TIMEOUT (3600s)
  - Cleans up temp files on failure

---

## Thread Safety

- **Client functions:** All safe to call from Blender's main thread
- **Server functions:** All async-safe for use with asyncio
- **Connection:** Thread-safe queue-based messaging

---

## Performance Notes

- **get_current_frame():** O(1), negligible overhead
- **send_timeline_update():** Queued, non-blocking (~1μs)
- **set_current_frame():** O(1), calls Blender depsgraph update (~1-10ms)
- **render_animation():** O(frame_count), ~30s-5min per 100 frames

---

## Backward Compatibility

- All timeline parameters are optional
- Servers without TIMELINE_UPDATE support can safely ignore 0x23 messages
- VIEWPORT_START fields with None/missing values are safe to skip
- FRAME_FINAL metadata is optional (single-frame renders omit it)

---

## Testing Utilities

Test the implementation with:

```bash
python -m pytest tests/test_animation_timeline.py -v
```

See `ANIMATION_INTEGRATION_GUIDE.md` for integration testing procedures.

