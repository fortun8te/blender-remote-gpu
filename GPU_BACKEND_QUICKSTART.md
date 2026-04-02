# GPU Backend Multi-GPU Support — Quick Start

## What Was Added

NVRHI-style GPU backend abstraction enabling seamless switching between OptiX, CUDA, HIP, and Vulkan without restarting the server.

## Key Files

| File | Purpose |
|------|---------|
| `shared/gpu_backend.py` | Core backend detection & management |
| `shared/protocol.py` | New RENDER_DEVICE_SELECT message type |
| `server/renderer.py` | Integrated backend selection into renders |
| `server/gpu_device_handler.py` | Server-side device selection handler |
| `addon/preferences.py` | Blender UI for backend switching |
| `tests/test_gpu_backend.py` | Unit tests |

## How It Works

### On Server Startup
1. Server detects all available GPU backends (OptiX, CUDA, HIP, Vulkan)
2. Selects first available as default
3. Stores available backends in memory

### On Client Connection
1. Server sends STATUS message listing available backends
2. Client UI shows "GPU Backends" panel with options

### When User Selects Backend
1. Client sends `RENDER_DEVICE_SELECT` message with backend name
2. Server validates & switches backend
3. If unavailable, falls back to next available
4. Server sends updated STATUS to client
5. Next render uses selected backend

### During Render
1. Renderer script queries current backend via `GPUBackendManager`
2. Passes as priority list to Blender: `[selected, "OPTIX", "CUDA"]`
3. Blender tries backends in order, uses first available
4. Render completes with selected backend

## Supported Backends

| Backend | GPU Type | Detection Method | Priority |
|---------|----------|------------------|----------|
| **OptiX** | NVIDIA RTX (Ampere/Ada/Blackwell) | `nvidia-smi` + `nvcc` | 1 (fastest for RTX) |
| **CUDA** | NVIDIA (all modern GPUs) | `nvcc` + `nvidia-smi` | 2 (fallback) |
| **HIP** | AMD RDNA/CDNA (ROCm) | `hipcc` + `rocm-smi` | 3 |
| **Vulkan** | Multi-vendor (experimental) | `vulkaninfo` | 4 (most portable) |

## Client UI

### Render Properties → Remote GPU → GPU Backends

```
GPU Backends
┌─────────────────────────────────────────┐
│ OPTIX (active) - 1 GPU(s), 24.0GB      │
│ CUDA          [Switch]                 │
│ HIP           [Switch] (unavailable)   │
└─────────────────────────────────────────┘
```

- **Active** backend highlighted with checkmark
- Click **[Switch]** to select different backend
- Unavailable backends greyed out (missing dependencies)

## API Usage (Server-Side)

### Import & Initialize
```python
from shared.gpu_backend import GPUBackendManager

mgr = GPUBackendManager()
```

### List Available Backends
```python
backends = mgr.detect_available_backends()
# Returns: [("OPTIX", "535.0"), ("CUDA", "12.0")]

current = mgr.get_current_backend_name()
# Returns: "OPTIX"
```

### Get Backend Details
```python
info = mgr.get_backend_info("OPTIX")
# Returns: {
#   "name": "OPTIX",
#   "version": "535.0",
#   "devices": [
#     {"name": "RTX 5090", "vram_gb": 24.0, "index": 0, ...}
#   ]
# }
```

### Select Backend
```python
success, error = mgr.select_backend("CUDA")
if not success:
    print(f"Error: {error}")
    mgr.fallback_to_available()  # Auto-switch to available backend
```

### Get Blender Arg
```python
device_arg = mgr.get_current_device_arg()
# Returns: "optix", "cuda", "hip", "vulkan"
# Pass to Blender: cprefs.compute_device_type = device_arg
```

## Protocol Messages

### Client → Server: RENDER_DEVICE_SELECT
```python
from shared.protocol import MsgType, pack_websocket

msg = pack_websocket(
    MsgType.RENDER_DEVICE_SELECT,
    {
        "backend": "CUDA",
        "device_idx": 0  # First GPU of the backend
    }
)
websocket.send(msg)
```

### Server → Client: STATUS (extended)
```python
{
    # ... existing status fields ...
    "available_backends": ["OPTIX", "CUDA", "HIP"],
    "current_backend": "OPTIX",
    "backend_devices": [
        {
            "name": "RTX 5090",
            "vram_gb": 24.0,
            "index": 0,
            "driver_version": "535.0"
        }
    ]
}
```

## Error Scenarios

### User Selects Unavailable Backend
```
Client: RENDER_DEVICE_SELECT {"backend": "HIP"}
Server: HIP not detected on system
Server: Fallback to CUDA (next available)
Server: Sends STATUS with current_backend="CUDA"
Client UI: Shows message "HIP unavailable, using CUDA"
```

### GPU Removed (e.g., unplugged)
```
User selected: OptiX
User unplugs RTX GPU during session
Next render: OptiX fails to initialize
Blender script tries CUDA (fallback) → success
Render completes (slower on CPU, but works)
```

### Dependencies Missing
```
OptiX requires: nvidia-smi ✓, nvcc ✓
CUDA requires: nvcc ✓
HIP requires: hipcc ✗, rocm-smi ✗  → SKIPPED
Vulkan requires: vulkaninfo ✗  → SKIPPED
Available: [OptiX, CUDA]
```

## Testing

Run tests to verify backend detection:
```bash
cd /Users/mk/Downloads/blender-remote-gpu
python -m pytest tests/test_gpu_backend.py -v
```

**Test coverage:**
- OptiX detection via nvidia-smi
- CUDA detection via nvcc
- HIP detection via hipcc
- Manager backend switching
- Fallback logic
- Device arg generation

## Logs

Server logs show backend selection:
```
[INFO] remote-gpu.gpu_backend: OptiX detected (driver 535.0, 1 device(s))
[INFO] remote-gpu.gpu_backend: Backend available: OPTIX
[INFO] remote-gpu.gpu_backend: Default backend selected: OPTIX
[INFO] remote-gpu.renderer: Rendering with backend device: optix
[INFO] remote-gpu.server: [session-123] Client requesting GPU backend: CUDA
[INFO] remote-gpu.gpu_backend: Backend selected: CUDA
```

## Future Work

- [ ] Device selection UI (choose specific GPU card)
- [ ] Multi-GPU rendering (render across multiple GPUs)
- [ ] Performance benchmarking per backend
- [ ] Backend selection persistence (remember user choice)
- [ ] Hot-plug GPU detection
- [ ] Hybrid CPU/GPU fallback

## Architecture Diagram

```
┌─────────────────┐
│  Blender Client │
│   (Addon UI)    │
└────────┬────────┘
         │ RENDER_DEVICE_SELECT
         │
    ┌────▼────────────────────┐
    │  WebSocket Connection   │
    └────┬────────────────────┘
         │ STATUS (with backends)
         │
┌────────▼──────────────────────────┐
│      Render Server                 │
│  ┌────────────────────────────┐   │
│  │  GPUBackendManager         │   │
│  │  ├─ OptiXBackend           │   │
│  │  ├─ CUDABackend            │   │
│  │  ├─ HIPBackend             │   │
│  │  └─ VulkanBackend          │   │
│  └────┬───────────────────────┘   │
│       │ get_current_device_arg()  │
│  ┌────▼────────────────────────┐  │
│  │   SubprocessRenderer        │  │
│  │   _build_render_script()    │  │
│  │   → blender --cycles-device │  │
│  │      [optix|cuda|hip]       │  │
│  └────┬───────────────────────┘   │
│       │                            │
│  ┌────▼──────────────┐             │
│  │  Blender Process  │             │
│  │  (GPU Rendering)  │             │
│  └───────────────────┘             │
└────────────────────────────────────┘
```

## Summary

**Before:** Server hardcoded to try OptiX first, fallback to CUDA
**After:** Full multi-backend abstraction allowing:
- Automatic detection of all available backends
- User-selectable backend switching
- Graceful fallback if selected unavailable
- Extensible architecture for future backends

**Impact:** Users with AMD GPUs, HIP support, or specific backend preferences can now switch without server restart.
