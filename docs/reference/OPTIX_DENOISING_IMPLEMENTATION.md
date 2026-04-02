# OptiX Denoising Integration — Implementation Summary

## Overview
Complete implementation of OptiX/OIDN denoising integration across Mac addon and Windows server with real-time GPU status display.

---

## 1. Protocol Layer (`shared/protocol.py`)

### New Message Type
- **RENDER_SETTINGS (0x03)**: Client sends denoiser config before rendering
  ```python
  {
      "type": 3,
      "denoiser_type": "OFF" | "OIDN" | "OPTIX",
      "intensity": 0.0-1.0,
      "auto_aov_creation": bool,
  }
  ```

---

## 2. Mac Addon Layer

### Preferences (`addon/preferences.py`)

#### New Properties in `RemoteGPUPreferences`
```python
use_denoiser: BoolProperty (default=True)
denoiser_type: EnumProperty ("OFF", "OIDN", "OPTIX", default="OPTIX")
denoiser_intensity: FloatProperty (0.0-1.0, default=0.8)
auto_aov_creation: BoolProperty (default=True)
```

#### UI Panel — "Render Devices"
Shows in render properties when engine is "Remote Cycles":
- Denoiser dropdown (OFF/OIDN/OptiX)
- Intensity slider (0-1)
- Auto AOV creation checkbox
- Server GPU info (when connected): GPU name, VRAM, backend selection
- OptiX availability warning (if RTX GPU required)

### Connection (`addon/connection.py`)

#### New Method
```python
def send_render_settings(self, denoiser_type: str = "OFF",
                         intensity: float = 0.8,
                         auto_aov: bool = True)
```
- Sends RENDER_SETTINGS message before render starts
- Thread-safe queue dispatch

### Engine (`addon/engine.py`)

#### In `render()` method
1. Reads denoiser settings from addon prefs
2. Calls `conn.send_render_settings()` before `send_render_start()`
3. Logs denoiser type and intensity

#### Viewport Header Status
- **Connected + GPU active**: "RTX GPU Active (RTX 5090)"
- **Connected + rendering**: "Rendering on GPU..."
- **Disconnected**: "Disconnected"
- Updates via `self.update_stats(title, subtitle)` in `view_draw()`

---

## 3. Windows Server Layer

### Renderer (`server/renderer.py`)

#### Updated `render_final()` Signature
```python
async def render_final(
    self,
    blend_path: str,
    resolution: tuple[int, int],
    samples: int,
    output_format: str = "PNG",
    on_progress: callable = None,
    denoiser_type: str = "OFF",
    denoiser_intensity: float = 0.8,
    auto_aov_creation: bool = True,
) -> bytes | None
```

#### Updated `_build_render_script()` Signature
```python
def _build_render_script(
    self,
    resolution: tuple[int, int],
    samples: int,
    output_format: str,
    denoiser_type: str = "OFF",
    denoiser_intensity: float = 0.8,
    auto_aov_creation: bool = True,
) -> str
```

#### Blender Script Features
**For OptiX denoising:**
- Creates Normal + Diffuse Color AOVs if `auto_aov_creation=True`
- Enables `render.use_denoising = True`
- Sets `render.denoiser = 'OPTIX'`
- Sets `render.denoise_use_gpu = True`
- Logs: `OptiX denoising enabled (intensity=0.8)`

**For OIDN denoising:**
- Creates Normal AOV if `auto_aov_creation=True`
- Enables `render.use_denoising = True`
- Sets `render.denoiser = 'OPENIMAGEDENOISE'` (or 'OIDN')
- Logs: `OIDN denoising enabled (intensity=0.8)`

**Fallback:**
- If denoiser unavailable, render continues with denoising disabled
- Non-blocking failures logged as warnings

### Denoiser Manager (`server/denoiser.py`)

**New module** for denoiser detection and post-processing:

```python
class DenoiserManager:
    def __init__(self)
    def get_available_denoisers() -> list[str]  # ["OFF", "OIDN", "OPTIX"]
    def denoise_exr(exr_path, denoiser_type, intensity, output_path) -> (bool, str|None)
    def log_denoising_result(denoiser_type, intensity, elapsed_ms)
```

**Features:**
- Detects OptiX availability (NVIDIA GPU + driver)
- Detects OIDN availability (bundled with Blender)
- Logs denoising performance: `OptiX denoising applied (intensity=0.8, took 234ms)`
- Graceful fallback if denoiser unavailable

### Server (`server/server.py`)

#### New Handler
```python
async def _handle_render_settings(self, ws, data: dict, session_id: str)
```
- Receives RENDER_SETTINGS message from client
- Validates denoiser type
- Falls back OPTIX→OIDN if OPTIX unavailable
- Stores settings in `self._render_settings` for next render
- Logs: `[session_id] Render settings updated: OPTIX denoising (intensity=0.8)`

#### Updated `_handle_render_start()`
1. Extracts denoiser settings from stored config
2. Passes to `final_renderer.render_final(..., denoiser_type, denoiser_intensity, auto_aov_creation)`
3. Logs denoising result after render:
   - `OptiX denoising applied (intensity=0.8, took 234ms)`
   - Only logs if denoiser != "OFF"

#### Server Initialization
```python
self.denoiser_manager = DenoiserManager()
self._render_settings = {
    "denoiser_type": "OFF",
    "intensity": 0.8,
    "auto_aov_creation": True,
}
```

---

## 4. Validation & Error Handling

### Client-Side Validation
- Denoiser type enum only accepts: OFF, OIDN, OPTIX
- Intensity clamped to [0.0, 1.0]
- OptiX availability checked at server (fallback to OIDN)

### Server-Side Validation
- Logs warning if unknown denoiser type requested
- Gracefully falls back OPTIX → OIDN if not available
- Non-blocking denoiser initialization (render succeeds without denoising if unavailable)

### Logging Format
```
[render_settings_handler]
  → "Render settings updated: OPTIX denoising (intensity=0.8)"

[render_phase]
  → "Using OptiX with 1 devices"
  → "OptiX denoising enabled (intensity=0.8)"

[post_render]
  → "OptiX denoising applied (intensity=0.8, took 234ms)"
```

---

## 5. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ CLIENT (Mac Addon)                                          │
├─────────────────────────────────────────────────────────────┤
│ 1. User sets denoiser in Render Properties panel            │
│    ├─ Denoiser dropdown (OFF/OIDN/OptiX)                   │
│    ├─ Intensity slider (0-1)                               │
│    └─ Auto AOV creation toggle                             │
│ 2. Click F12 (render)                                       │
│ 3. engine.render() reads prefs.denoiser_type/intensity     │
│ 4. conn.send_render_settings(OPTIX, 0.8, True)            │
│ 5. conn.send_render_start(resolution, samples)             │
│ 6. Viewport shows GPU status badge                         │
└──────────────────┬──────────────────────────────────────────┘
                   │ WebSocket
                   │ RENDER_SETTINGS (0x03)
                   │ RENDER_START (0x10)
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ SERVER (Windows)                                            │
├─────────────────────────────────────────────────────────────┤
│ 1. _handle_render_settings(data)                            │
│    └─ Store in self._render_settings                       │
│ 2. _handle_render_start(data)                              │
│    └─ final_renderer.render_final(..., denoiser_type=...)  │
│ 3. Blender subprocess renders with config:                 │
│    ├─ Create Normal/Albedo AOVs (if auto_aov_creation)     │
│    ├─ Enable render.use_denoising = True                   │
│    ├─ Set render.denoiser = 'OPTIX'                        │
│    └─ Render outputs denoised image                        │
│ 4. denoiser_manager.log_denoising_result()                 │
│ 5. Send FRAME_FINAL back to client                         │
└──────────────────┬──────────────────────────────────────────┘
                   │ WebSocket
                   │ FRAME_FINAL (0x81) + image_bytes
                   ↓
┌──────────────────────────────────────────────────────────────┐
│ CLIENT (Display)                                             │
├──────────────────────────────────────────────────────────────┤
│ 1. engine.render() receives FRAME_FINAL                     │
│ 2. Decode image + display in viewport                       │
│ 3. Show "Render complete: denoised" in status bar           │
└──────────────────────────────────────────────────────────────┘
```

---

## 6. Key Implementation Details

### OptiX Requirements
- NVIDIA GPU with OptiX support (RTX series recommended)
- NVIDIA driver with OptiX runtime
- Blender 3.3+ with OptiX support
- Automatic AOV creation (Normal + Diffuse Color)

### OIDN Requirements
- Intel Open Image Denoise (bundled with Blender)
- CPU-based, works with any GPU
- Fallback when OptiX unavailable

### AOV Auto-Creation
When `auto_aov_creation=True`:
- Creates `use_pass_normal = True` (all denoisers)
- Creates `use_pass_diffuse_color = True` (OptiX only)
- Non-blocking if AOV creation fails (render proceeds)

### Performance
- Denoising happens **during render** in Blender
- No post-processing overhead
- Smaller output payload (denoising is lossless)
- Logs include timing: `took 234ms`

---

## 7. Testing Checklist

- [ ] Mac: Preferences panel shows denoiser options
- [ ] Mac: Intensity slider works (0-1 range)
- [ ] Mac: Auto AOV creation toggle visible
- [ ] Mac: OptiX warning shows when RTX GPU detected
- [ ] Mac: Server GPU status displays in Render Properties
- [ ] Server: RENDER_SETTINGS handler receives message
- [ ] Server: Denoiser type stored correctly
- [ ] Server: Blender script creates AOVs for OptiX
- [ ] Server: Render includes denoising
- [ ] Server: Logs show denoising result (timing)
- [ ] Server: OptiX→OIDN fallback works
- [ ] Viewport: GPU status badge updates during render
- [ ] Viewport: Denoised image displays correctly

---

## 8. Files Modified

| File | Changes |
|------|---------|
| `shared/protocol.py` | + RENDER_SETTINGS (0x03) message type |
| `addon/preferences.py` | + Denoiser settings properties + Render Devices panel |
| `addon/connection.py` | + `send_render_settings()` method |
| `addon/engine.py` | + Denoiser config in `render()` + GPU status in `view_draw()` |
| `server/renderer.py` | + Denoiser params to `render_final()` + Denoiser config in Blender script |
| `server/server.py` | + `_handle_render_settings()` + Denoiser manager integration |
| **NEW** `server/denoiser.py` | DenoiserManager class for detection + logging |

---

## 9. Configuration Examples

### Example 1: OptiX with High Quality
```python
# Client sends:
conn.send_render_settings("OPTIX", 0.9, True)

# Server logs:
# [session_id] Render settings updated: OPTIX denoising (intensity=0.9)
# Using OptiX with 1 devices
# OptiX denoising enabled (intensity=0.9)
# OptiX denoising applied (intensity=0.9, took 187ms)
```

### Example 2: OIDN with Moderate Intensity
```python
# Client sends:
conn.send_render_settings("OIDN", 0.6, True)

# Server logs:
# [session_id] Render settings updated: OIDN denoising (intensity=0.6)
# OIDN denoising enabled (intensity=0.6)
# OIDN denoising applied (intensity=0.6, took 142ms)
```

### Example 3: No Denoising
```python
# Client sends:
conn.send_render_settings("OFF", 0.0, False)

# Server logs:
# [session_id] Render settings updated: OFF denoising (intensity=0.0)
# (no denoising logs in render phase)
```

---

## 10. Future Enhancements

1. **Compositor Denoising**: Post-render compositing node for advanced filtering
2. **Denoiser Presets**: "Fast", "Balanced", "High Quality" presets
3. **Progressive Denoising**: Apply denoising to viewport samples
4. **Multi-GPU Support**: Distribute denoising across multiple GPUs
5. **Denoiser Benchmarking**: Performance metrics UI in preferences
6. **Custom AOV Inputs**: Allow users to specify which AOVs for denoising

---

## 11. Validation Results

✅ All Python files compile without syntax errors
✅ Import statements resolve correctly
✅ Message type enums are backward-compatible
✅ Denoiser logic is non-blocking (graceful fallback)
✅ GPU status display integrates with existing `update_stats()` API

