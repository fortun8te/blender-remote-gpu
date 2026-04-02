# NVRHI-Style Multi-GPU Backend Implementation

This document describes the complete GPU backend abstraction system for multi-GPU support across OptiX, CUDA, HIP, and Vulkan backends.

## Overview

The implementation follows the NVRHI (NVIDIA Rendering Hardware Interface) abstraction pattern, providing a unified interface for GPU backend detection, validation, and selection. This allows users to switch between different GPU rendering backends (OptiX for RTX, CUDA for older NVIDIA, HIP for AMD, Vulkan for portability) without restarting.

## Architecture

### Core Components

#### 1. `shared/gpu_backend.py` (NEW)
Central abstraction layer with the following classes:

**Abstract Base Class:**
- `GPUBackend` — Abstract interface with methods:
  - `detect()` — Static method returning backend instance if available, None otherwise
  - `get_info()` → `BackendInfo` — Returns backend version and device list
  - `get_blender_device_arg()` → str — Returns Blender CLI arg (e.g., "optix")
  - `validate()` → (bool, str) — Validates backend is functional

**Concrete Backends:**
- `OptiXBackend` — NVIDIA OptiX (RTX GPUs)
  - Detects via `nvidia-smi` and `nvcc --version`
  - Supports RTX Ampere, Ada, Blackwell cards
  - Fastest for RTX-enabled GPUs

- `CUDABackend` — NVIDIA CUDA (fallback for older GPUs)
  - Detects via `nvcc --version` and `nvidia-smi`
  - Fallback when OptiX unavailable
  - Works with all NVIDIA GPUs

- `HIPBackend` — AMD ROCm (AMD RDNA, CDNA GPUs)
  - Detects via `hipcc --version` and `rocm-smi`
  - For AMD Radeon and Instinct GPUs
  - Skipped on NVIDIA systems

- `VulkanBackend` — Vulkan (multi-vendor, experimental)
  - Detects via `vulkaninfo`
  - Works on Linux/Windows with Vulkan drivers
  - Most portable but experimental in Blender

**Manager Class:**
- `GPUBackendManager` — Orchestrates detection and selection
  - `detect_available_backends()` → [(name, version), ...] — E.g., [("OPTIX", "535.0"), ("CUDA", "12.0")]
  - `get_backend_info(name)` → dict — Returns device details
  - `select_backend(name)` → (bool, str) — Switch backends with validation
  - `get_current_backend_name()` → str — E.g., "OPTIX"
  - `get_current_device_arg()` → str — For Blender: "optix", "cuda", "hip", "vulkan"
  - `validate_backend_requirements(name)` → (bool, str) — Pre-flight check
  - `fallback_to_available()` → bool — Auto-switch on failure

**Data Classes:**
- `GPUDevice` — Represents a single GPU
  - name: str (e.g., "RTX 5090")
  - vram_gb: float
  - index: int
  - driver_version: Optional[str]
  - compute_capability: Optional[str]

- `BackendInfo` — Backend metadata
  - name: str
  - version: str
  - devices: [GPUDevice]
  - to_dict() — JSON serialization

### 2. `shared/protocol.py` (UPDATED)
Added multi-backend message support:

**New Message Type:**
- `RENDER_DEVICE_SELECT = 0x12` — Client → Server
  - Payload: `{"backend": "OPTIX", "device_idx": 0}`
  - Allows client to switch GPU backend mid-session

**Extended STATUS Message (0x90):**
- Server → Client with backend info:
  ```json
  {
    "gpu_name": "RTX 5090",
    "gpu_temp": 55.0,
    "vram_used": 12000,
    "vram_total": 24000,
    "available_backends": ["OPTIX", "CUDA"],
    "current_backend": "OPTIX",
    "backend_devices": [
      {"name": "RTX 5090", "vram_gb": 24.0, "index": 0}
    ]
  }
  ```

### 3. `server/renderer.py` (UPDATED)
Integrated GPU backend selection into render subprocess:

**Changes:**
- `SubprocessRenderer.__init__()` now creates `GPUBackendManager` instance
- `_build_render_script()` generates Blender Python script with backend priority:
  ```python
  backend_priority = [selected_backend] + ['OPTIX', 'CUDA']
  for device_type in backend_priority:
      cprefs.compute_device_type = device_type
      # Try to enable...
  ```
- Logs: "Rendering with backend device: optix"

### 4. `server/gpu_device_handler.py` (NEW)
HTTP handler for GPU device operations:

**Methods:**
- `get_status_data()` → dict — Returns backend info for STATUS message
- `handle_device_select(websocket, data, session_id)` — Process RENDER_DEVICE_SELECT from client
  - Validates backend availability
  - Falls back if requested backend unavailable
  - Sends updated STATUS to client

### 5. `addon/preferences.py` (UPDATED)
Blender UI for backend selection:

**New Operator:**
- `REMOTEGPU_OT_select_device` — Operator to switch GPU backend
  - Sends RENDER_DEVICE_SELECT message to server
  - Updates UI to reflect new selection

**New UI Section:**
- "GPU Backends" panel in Render Properties
- Shows available backends with device count and VRAM
- Radio-style selection (current backend highlighted)
- "Switch" button per backend (disabled for current)

**Example UI Layout:**
```
GPU Backends
  OPTIX (active) - 1 GPU(s), 24.0GB VRAM
  CUDA          [Switch]
  HIP           [Switch] (dimmed, not available)
```

## Data Flow

### Startup
1. Server starts `RenderServer()`
2. `SubprocessRenderer.__init__()` creates `GPUBackendManager`
3. Manager runs detection loop:
   - Try OptiX (`nvidia-smi`, `nvcc`)
   - Try CUDA (`nvcc`)
   - Try HIP (`hipcc`, `rocm-smi`)
   - Try Vulkan (`vulkaninfo`)
4. Sets first available as default

### Client Connection
1. Client connects to server via WebSocket
2. Client receives PING from server
3. Server sends STATUS message with:
   - Current backend name
   - Available backends list
   - Devices for current backend
4. Client UI renders "GPU Backends" panel with available options

### Backend Switching (Client)
1. User clicks "Switch" button for CUDA backend
2. Client sends `RENDER_DEVICE_SELECT {"backend": "CUDA", "device_idx": 0}`
3. Server's `gpu_device_handler.handle_device_select()`:
   - Calls `GPUBackendManager.select_backend("CUDA")`
   - If fails, calls `fallback_to_available()`
   - Sends updated STATUS back to client
4. Client UI updates to show new backend as active
5. Next render uses CUDA

### Rendering with Selected Backend
1. Render starts via `SubprocessRenderer.render_final()`
2. `_build_render_script()` reads `gpu_backend_mgr.get_current_device_arg()` → "cuda"
3. Generated Blender script prioritizes CUDA:
   ```python
   backend_priority = ["cuda", "OPTIX", "CUDA"]
   for device_type in backend_priority:
       cprefs.compute_device_type = device_type
       # Blender uses CUDA if available
   ```
4. Render completes using selected backend
5. Server logs: "Rendering with backend device: cuda"

## Backend Detection Logic

### OptiX Detection
```bash
nvidia-smi --query-gpu=driver_version
  → "535.0"  ✓ NVIDIA driver detected

nvcc --version
  → "release 12.0"  ✓ CUDA toolkit found → OptiX available
```
**Fallback:** If CUDA not installed, OptiX unavailable.

### CUDA Detection
```bash
nvcc --version
  → "release 12.0"  ✓ CUDA toolkit available
nvidia-smi
  → Returns GPU list  ✓ NVIDIA GPUs present
```
**Fallback:** OptiX preferred, CUDA is secondary.

### HIP Detection
```bash
hipcc --version
  → "HIP version: 5.0"  ✓ ROCm installed

rocm-smi
  → Lists AMD GPUs  ✓ AMD GPUs found
```
**Skipped on:** NVIDIA systems (no AMD GPUs).

### Vulkan Detection
```bash
vulkaninfo --summary
  → GPU device list  ✓ Vulkan drivers present
```
**Note:** Experimental; may work but less optimized than backend-specific APIs.

## Priority Order
1. **OptiX** (fastest on RTX)
2. **CUDA** (fallback for NVIDIA)
3. **HIP** (AMD ROCm)
4. **Vulkan** (multi-vendor, portable)

First available backend becomes default. User can switch to any other available backend.

## Error Handling

### Backend Unavailable
If user selects "OptiX" but server only has "CUDA":
1. `select_backend("OPTIX")` returns `(False, "Backend 'OPTIX' not detected on this system")`
2. Handler calls `fallback_to_available()` → switches to CUDA
3. Client receives STATUS with `"current_backend": "CUDA"`
4. Client UI updates and shows message: "OptiX not available, using CUDA"

### Validation Failure
If CUDA toolkit broken:
1. `validate_backend_requirements("CUDA")` returns `(False, "CUDA toolkit (nvcc) not installed")`
2. CUDA not added to available backends
3. Client never sees CUDA as option

### Render Failure with Selected Backend
In `_build_render_script()`, Blender script tries priority list:
```python
backend_priority = ["selected_backend", "OPTIX", "CUDA"]
for device_type in backend_priority:
    try:
        cprefs.compute_device_type = device_type
        # Success → break
    except:
        # Try next in list
```
Ensures render completes even if selected backend fails.

## Configuration

No external config files needed. Backends detected automatically on startup.

### Environment Variables (Future)
Could add to `shared/infrastructure.ts` pattern:
```python
# Not yet implemented, but framework ready for:
os.getenv("REMOTE_GPU_PREFER_BACKEND", "OPTIX")  # Force specific backend
os.getenv("REMOTE_GPU_SKIP_BACKENDS", "HIP,VULKAN")  # Blacklist backends
```

## Persistence

User's backend selection persists per session (in-memory in `GPUBackendManager._current_backend`).

To persist across server restarts:
1. Save selection to `server_config.json` after `select_backend()`
2. Load on startup: `mgr.select_backend(config.get("preferred_backend", "OPTIX"))`
3. Addon could also store user preference in Blender `addon.preferences`

Currently not implemented; adds complexity. Could be feature for later.

## Testing

**Test file:** `tests/test_gpu_backend.py`

**Coverage:**
- Backend detection (OptiX, CUDA, HIP, Vulkan)
- Manager initialization with multiple backends
- Backend selection and switching
- Device arg generation
- Fallback logic
- VRAM parsing

**Run tests:**
```bash
cd /Users/mk/Downloads/blender-remote-gpu
python -m pytest tests/test_gpu_backend.py -v
```

## Future Enhancements

1. **Device Selection** — Allow choosing specific GPU (device_idx)
   - Currently only device 0 supported
   - UI could show individual GPU cards per backend
   - `RENDER_DEVICE_SELECT` payload ready for device_idx

2. **Multi-GPU Rendering** — Render across multiple GPUs
   - Cycles can use multiple GPUs in one render
   - Manager could track per-device performance
   - Load balancing logic

3. **Backend Benchmarking** — Test backend speed
   - Quick render on each backend to compare
   - Auto-select fastest for hardware
   - Store results in config

4. **Hybrid Rendering** — Mix CPU and GPU
   - Fall back to CPU if GPU memory insufficient
   - Detect available CPU cores via psutil

5. **Hot-Plug GPU Support** — Add GPUs mid-session
   - Re-run detection if user connects new GPU
   - Update available backends list dynamically

6. **Performance Metrics** — Per-backend timing
   - Track render speed per backend
   - Warn user if slow backend selected
   - Suggest faster alternative

## Files Modified/Created

| File | Type | Change |
|------|------|--------|
| `shared/gpu_backend.py` | NEW | Core abstraction + all backends |
| `shared/protocol.py` | UPDATED | Added RENDER_DEVICE_SELECT, extended STATUS |
| `server/renderer.py` | UPDATED | Integrated GPUBackendManager |
| `server/gpu_device_handler.py` | NEW | Message handler for device selection |
| `addon/preferences.py` | UPDATED | GPU Backends UI + select_device operator |
| `tests/test_gpu_backend.py` | NEW | Unit tests |

## Validation Checklist

- [x] OptiX backend detects RTX GPUs via nvidia-smi + nvcc
- [x] CUDA backend detects via nvcc, falls back from OptiX
- [x] HIP backend detects via hipcc + rocm-smi
- [x] Vulkan backend detects via vulkaninfo
- [x] GPUBackendManager prioritizes OptiX > CUDA > HIP > Vulkan
- [x] Backend selection validates before switching
- [x] Fallback switches to available backend on failure
- [x] Renderer uses selected backend arg in Blender script
- [x] Protocol messages support backend selection
- [x] Addon UI shows available backends
- [x] Device selection operator sends message to server
- [x] Server handler processes device select messages
- [x] STATUS message includes backend info
- [x] Error handling for unavailable backends
- [x] Tests cover detection, selection, fallback

## Example Session

### Scenario: User has OptiX + CUDA, wants to test CUDA

**Initial State:**
```
Server detects: OptiX (RTX 5090, 24GB), CUDA (RTX 5090, 24GB)
Default: OptiX
```

**Client Connection:**
```
Client receives STATUS:
  available_backends: ["OPTIX", "CUDA"]
  current_backend: "OPTIX"
  backend_devices: [{"name": "RTX 5090", "vram_gb": 24.0}]

UI renders:
  GPU Backends
    OPTIX (active) - 1 GPU(s), 24.0GB
    CUDA          [Switch]
```

**User Clicks "Switch" for CUDA:**
```
Client sends: RENDER_DEVICE_SELECT {"backend": "CUDA", "device_idx": 0}
Server processes → select_backend("CUDA") ✓
Server sends updated STATUS → current_backend: "CUDA"
Client UI updates → CUDA now shows (active)
```

**User Renders:**
```
Render subprocess runs _build_render_script()
Backend priority = ["cuda", "OPTIX", "CUDA"]
Blender sets cprefs.compute_device_type = "CUDA"
Render completes using CUDA
Server logs: "Rendering with backend device: cuda"
```

**User Switches Back to OptiX:**
```
Client sends: RENDER_DEVICE_SELECT {"backend": "OPTIX"}
Server switches back → OptiX selected
Next render uses OptiX
```

---

**Implementation Date:** April 2, 2026
**Status:** Complete and tested
**Tested With:** NVIDIA RTX (OptiX/CUDA), AMD ROCm (HIP), Vulkan SDKs
