# Quick Fix Guide — Code Locations & Patches

## Issue #1: Final Render Format Mismatch (15 min)

**File:** `addon/engine.py`

**Line 63 (PROBLEM):**
```python
conn.send_render_start(
    resolution=(res_x, res_y),
    samples=settings.get("samples", 128),
    output_format="PNG",  # ← SENDS PNG
)
```

**Line 88 (PROBLEM):**
```python
pixels, width, height = decode_jpeg(result_data)  # ← TRIES TO DECODE JPEG
```

**FIX:** Choose one format and use consistently.

**Option A: Use PNG for both (simpler)**
```python
# Line 88 - Update decoder
from .frame import decode_png
pixels, width, height = decode_png(result_data)
```
Then add `decode_png()` function to `addon/frame.py`.

**Option B: Use EXR for final render (recommended, lossless)**
```python
# Line 63
output_format="EXR",

# Line 88
from .frame import decode_exr
pixels, width, height = decode_exr(result_data)
```

**Option C: Update server to use JPEG**
```python
# Line 63
output_format="JPEG",
```
Then ensure server encodes to JPEG.

**Recommendation:** Option B (EXR for final) + PNG/JPEG for viewport is most professional.

---

## Issue #2: Addon Path Resolution (20 min)

**File:** `addon/connection.py` lines 25-36

**PROBLEM:**
```python
_addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_shared_dir = os.path.join(_addon_dir, "shared")
if os.path.exists(_shared_dir):
    sys.path.insert(0, _addon_dir)
else:
    # This fallback doesn't work for copied installations
    import bpy
    _scripts_dir = os.path.dirname(bpy.context.preferences.filepaths.script_directory)
    if os.path.exists(os.path.join(_scripts_dir, "shared")):
        sys.path.insert(0, _scripts_dir)
```

**FIX:**
```python
_addon_file = os.path.abspath(__file__)
_addon_dir = os.path.dirname(_addon_file)  # e.g., ~/.../remote_gpu_render

# Case 1: shared/ is sibling to addon/ (copied into addons/)
_scripts_dir = os.path.dirname(_addon_dir)  # e.g., ~/.../scripts/addons
_shared_dir = os.path.join(_scripts_dir, "shared")
if os.path.exists(_shared_dir):
    sys.path.insert(0, _scripts_dir)
else:
    # Case 2: shared/ is parent of addon/ (dev layout)
    _parent_dir = os.path.dirname(_scripts_dir)  # Go up one more level
    _shared_dir = os.path.join(_parent_dir, "shared")
    if os.path.exists(_shared_dir):
        sys.path.insert(0, _parent_dir)
    else:
        raise ImportError(
            f"Cannot find 'shared' module. Expected at:\n"
            f"  {os.path.join(_scripts_dir, 'shared')} (copied)\n"
            f"  {os.path.join(_parent_dir, 'shared')} (dev)\n"
            f"Check SETUP.md for installation instructions."
        )

from shared.protocol import MsgType, pack_websocket, unpack_websocket
from shared.constants import HEARTBEAT_INTERVAL, MAX_MESSAGE_SIZE
```

**Also update SETUP.md:**
```markdown
### Option B: Copy

After downloading the repo:

1. Create directory: ~/.config/blender/5.0/scripts/addons/remote_gpu_render/
2. Copy addon/ contents into remote_gpu_render/
3. Copy shared/ into ~/.config/blender/5.0/scripts/addons/ (as sibling)

Final structure:
~/.config/blender/5.0/scripts/addons/
  ├── remote_gpu_render/
  │   ├── __init__.py
  │   ├── engine.py
  │   ├── connection.py
  │   └── ...
  └── shared/
      ├── protocol.py
      ├── constants.py
      └── __init__.py
```

---

## Issue #3: Texture Packing Validation (30 min - quick fix)

**File:** `addon/sync.py` lines 18-25

**PROBLEM:**
```python
try:
    bpy.ops.file.pack_all()
except RuntimeError:
    # Silent failure!
    pass
```

**QUICK FIX (report error instead of silent fail):**
```python
def save_blend_to_bytes() -> bytes:
    """Save the current .blend file to a temporary file and return its bytes."""
    import bpy

    tmp = os.path.join(tempfile.gettempdir(), "remote_gpu_scene.blend")

    # Try to pack external data
    try:
        bpy.ops.file.pack_all()
        # Verify packing succeeded by checking if external images still exist
        external_count = len([img for img in bpy.data.images
                             if img.filepath and not img.packed_file])
        if external_count > 0:
            raise RuntimeError(f"pack_all() left {external_count} unpacked external images")
    except RuntimeError as e:
        # Instead of silent fail, return None to signal error upstream
        import traceback
        print(f"Warning: Could not pack textures: {e}")
        print("Scene will be synced with external texture references.")
        print("If remote render fails, try: File > External Data > Pack All (Blender UI)")
        # Let it continue, but warn the user

    bpy.ops.wm.save_as_mainfile(filepath=tmp, copy=True, compress=True)

    with open(tmp, "rb") as f:
        data = f.read()

    try:
        os.unlink(tmp)
    except OSError:
        pass

    return data
```

**BETTER FIX (manual texture handling - 2 hours):**
This would involve:
1. Finding all external image files
2. Copying them to a temp directory
3. Relinking image.filepath to relative paths
4. Saving the modified .blend
5. Sending both .blend and textures (or re-packing them)

This is more robust but requires more code.

---

## Issue #4: Update Latency Claims (5 min)

**File 1:** `server/viewport_live.py` line 4

**CHANGE:**
```python
# FROM:
"""Renders Cycles frames as fast as possible by keeping Blender loaded.
Much faster than subprocess mode (~30ms vs 2000ms per frame).
"""

# TO:
"""Renders Cycles frames progressively by keeping Blender loaded.
Faster than subprocess mode but slower than real-time.
Typical: ~1-2 seconds per progressive frame (progressive refinement).
"""
```

**File 2:** `SETUP.md` lines 94-99

**CHANGE:**
```markdown
# FROM:
## Tailscale Setup (for remote/WAN access)

### 1. Install Tailscale on both machines
- Mac: https://tailscale.com/download/mac
- Windows: https://tailscale.com/download/windows

### 2. Use the Tailscale IP instead of LAN IP
- Find your Windows Tailscale IP: `tailscale ip` (usually 100.x.x.x)
- Set that as the server IP in Blender's addon preferences

### 3. Latency expectations
- LAN (Ethernet): ~1-5ms round trip
- Tailscale (same city): ~10-30ms
- Tailscale (cross-country): ~50-100ms
- Tailscale (international): ~100-200ms

# TO:
## Tailscale Setup (for remote/WAN access)

[same as above for setup]

### 3. Rendering latency expectations
Network latency (round-trip):
- LAN (Ethernet): ~1-5ms
- Tailscale (same city): ~10-30ms
- Tailscale (cross-country): ~50-100ms
- Tailscale (international): ~100-200ms

**Viewport render time per frame:**
- First frame (1 sample): ~500-800ms
- Progressive refinement (4→16→64→128 samples): ~500-800ms each
- **Total for full quality**: ~2-3 seconds per orbit update
- This is acceptable for viewport feedback, not "real-time"

**Final render (F12):**
- Time depends on sample count and scene complexity
- RTX 5090: ~5-30 seconds for 128-512 samples (1920x1080)
```

---

## Issue #5: Binary Receive Timeout (10 min)

**File:** `server/server.py` line 57

**CHANGE FROM:**
```python
if data.get("has_binary"):
    binary = await websocket.recv()
```

**CHANGE TO:**
```python
if data.get("has_binary"):
    try:
        binary = await asyncio.wait_for(websocket.recv(), timeout=300.0)
    except asyncio.TimeoutError:
        await self._send_error(ws, "Binary upload timeout (> 5 minutes). Check network connection.")
        return
```

---

## Issue #6: GPU Setup Error Reporting (15 min)

**File:** `server/viewport_live.py` lines 65-69

**CHANGE FROM:**
```python
logger.warning("GPU setup failed — will use CPU (slow)")
```

**CHANGE TO:**
```python
# Add tracking variable
if not gpu_available:
    logger.error("GPU setup failed: No OPTIX/CUDA devices detected. Falling back to CPU.")
    # Notify client
    await self._send_error(ws,
        "GPU not available. Rendering will use CPU (60x slower). "
        "Check NVIDIA drivers and Blender Cycles preferences.")
    cycles.device = "CPU"
```

---

## Issue #7: Protocol Validation (15 min)

**File:** `server/server.py` line 57

**CHANGE FROM:**
```python
if data.get("has_binary"):
    binary = await websocket.recv()
```

**CHANGE TO:**
```python
if data.get("has_binary"):
    try:
        binary = await asyncio.wait_for(websocket.recv(), timeout=30.0)
    except asyncio.TimeoutError:
        await self._send_error(ws, "Binary data timeout. Upload too slow.")
        return

    # Validate that we got binary data, not another message header
    if isinstance(binary, bytes):
        # Good, it's binary
        pass
    else:
        await self._send_error(ws, "Protocol error: expected binary data, got message header")
        return
```

---

## Issue #8: Viewport Race Condition (5 min)

**File:** `server/server.py` line 188-192

**CHANGE FROM:**
```python
async def _handle_viewport_camera(self, ws, data: dict):
    """Handle fast camera-only update — restarts viewport render."""
    # Same as viewport_start but optimized path
    await self._handle_viewport_start(ws, data)
```

**CHANGE TO:**
```python
async def _handle_viewport_camera(self, ws, data: dict):
    """Handle fast camera-only update — restarts viewport render."""
    self._cancel_viewport()  # IMPORTANT: Cancel old task first!
    await self._handle_viewport_start(ws, data)
```

---

## Issue #9: Render Result Check (5 min)

**File:** `server/viewport_live.py` line 126

**CHANGE FROM:**
```python
pixels = bpy.data.images["Render Result"].pixels[:]
```

**CHANGE TO:**
```python
if "Render Result" not in bpy.data.images:
    logger.error("Render failed: No image in buffer")
    return None

pixels = bpy.data.images["Render Result"].pixels[:]
```

---

## Issue #10: Update SETUP.md (10 min)

**File:** `SETUP.md` line 70

**CHANGE FROM:**
```
Note: Also copy the `shared/` directory next to the addon, or install the shared module.
```

**CHANGE TO:**
```markdown
### 2. Install the addon

**Option A: Symlink (for development)**
```bash
ln -s /path/to/blender-remote-gpu/addon \
  ~/.config/blender/5.0/scripts/addons/remote_gpu_render
ln -s /path/to/blender-remote-gpu/shared \
  ~/.config/blender/5.0/scripts/addons/shared
```

**Option B: Copy (for distribution)**
```bash
cp -r /path/to/blender-remote-gpu/addon \
  ~/.config/blender/5.0/scripts/addons/remote_gpu_render
cp -r /path/to/blender-remote-gpu/shared \
  ~/.config/blender/5.0/scripts/addons/shared
```

**Either way, the final structure must be:**
```
~/.config/blender/5.0/scripts/addons/
├── remote_gpu_render/    (addon files here)
│   ├── __init__.py
│   ├── engine.py
│   ├── connection.py
│   ├── sync.py
│   ├── preferences.py
│   └── frame.py
└── shared/               (shared files here - MUST be sibling)
    ├── __init__.py
    ├── protocol.py
    └── constants.py
```
```

---

## QUICK FIX CHECKLIST

Copy-paste this and track progress:

```
CRITICAL (Required for basic functionality):
[ ] Issue #1: Final render format (15 min)
[ ] Issue #2: Addon path resolution (20 min)
[ ] Issue #3: Texture packing (30 min quick fix)

HIGH (Stability):
[ ] Issue #5: Binary timeout (10 min)
[ ] Issue #6: GPU error reporting (15 min)
[ ] Issue #7: Protocol validation (15 min)

MEDIUM (Polish):
[ ] Issue #4: Latency claims (5 min)
[ ] Issue #8: Viewport race condition (5 min)
[ ] Issue #9: Render result check (5 min)
[ ] Issue #10: SETUP.md clarity (10 min)

TOTAL ESTIMATED TIME:
Critical: 1 hour
High: 40 minutes
Medium: 30 minutes
────────────────────
TOTAL: 2 hours 10 minutes

TESTING:
[ ] Test addon installation from ZIP
[ ] Test viewport rendering
[ ] Test F12 final render
[ ] Test external texture
[ ] Test network drop recovery
```

---

## IMPLEMENTATION ORDER

1. **Start with Issue #1** (final render format) — blocks most important user workflow
2. **Then Issue #2** (addon path) — blocks installation, do ASAP
3. **Then Issue #3** (texture packing) — common user issue
4. **Then Issues #5-7** (stability) — prevent hangs and crashes
5. **Then Issues #4, 8-10** (polish) — documentation and edge cases

Each fix is independent and can be tested separately.

