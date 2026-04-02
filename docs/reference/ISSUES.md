# Critical Issues - Blender Remote GPU Render

## 🔴 CRITICAL (Blocks Release)

### Issue #1: Final Render Format Mismatch
**File:** `addon/engine.py` line 63 vs line 88
**Severity:** BLOCKER
**Status:** UNFIXED

Addon sends final render as PNG:
```python
output_format="PNG"  # line 63
```

But tries to decode result as JPEG:
```python
pixels, width, height = decode_jpeg(result_data)  # line 88
```

**Impact:** F12 (final render) will CRASH when result arrives as PNG.

**Fix:** Either:
- Change both to same format (PNG recommended for final quality)
- Update decoder to handle both formats
- Use EXR for final render (lossless, recommended)

**Estimated effort:** 15 minutes

---

### Issue #2: Addon Shared Module Path Resolution Fails
**File:** `addon/connection.py` lines 26-36
**Severity:** BLOCKER
**Status:** UNFIXED

When user downloads addon from GitHub and copies files:
- `~/.config/blender/5.0/scripts/addons/remote_gpu_render/addon/*`
- `~/.config/blender/5.0/scripts/addons/remote_gpu_render/shared/*`

The fallback logic looks for `~/.config/blender/5.0/scripts/shared/` (wrong location).

**Impact:** ImportError when loading addon. Addon will not register.

**Fix:** Update fallback to check addon directory first:
```python
_shared_dir = os.path.join(_addon_dir, "shared")
if not os.path.exists(_shared_dir):
    # addon was copied into ~/.../addons/remote_gpu_render/
    # so check if shared is a sibling
    _parent = os.path.dirname(_addon_dir)
    if os.path.exists(os.path.join(_parent, "shared")):
        sys.path.insert(0, _parent)
```

Also update SETUP.md to be explicit:
```
Copy folder structure to:
  ~/.config/blender/5.0/scripts/addons/
    ├── remote_gpu_render/  (this is addon/)
    └── shared/             (this is shared/)
```

**Estimated effort:** 20 minutes

---

### Issue #3: External Texture Packing Fails Silently
**File:** `addon/sync.py` lines 21-25
**Severity:** CRITICAL
**Status:** UNFIXED

```python
try:
    bpy.ops.file.pack_all()
except RuntimeError:
    pass  # Silent failure!
```

If `pack_all()` fails (which it often does in Blender 5), external textures are NOT packed.

**Impact:** Renders on Windows fail with "Image file not found" because textures reference Mac paths like `/Users/mk/texture.jpg`.

**Fix:** Validate that packing succeeded, or fail with error to user:
```python
try:
    bpy.ops.file.pack_all()
    # Verify: check that images were actually packed
    if bpy.ops.file.pack_all.__dict__.get('result') != {'FINISHED'}:
        raise RuntimeError("pack_all() did not succeed")
except RuntimeError:
    self.report({"ERROR"}, "Could not pack textures. Please pack manually: File > External Data > Pack All")
    return None
```

Or implement manual texture packing:
```python
def pack_external_textures(blend_path):
    """Manually copy external textures and relink paths."""
    # Find all Image datablocks
    for img in bpy.data.images:
        if img.filepath and not img.packed_file:
            # Copy file to temp directory
            # Relink image.filepath to relative path
            # This is complex but reliable
```

**Estimated effort:** 1-2 hours (manual approach) or 30 minutes (better error message)

---

### Issue #4: Viewport Latency Claim is False
**File:** `server/viewport_live.py` line 4, `SETUP.md` line 95
**Severity:** HIGH (misleading, not blocking)
**Status:** UNFIXED

Code claims: "Much faster than subprocess mode (~30ms vs 2000ms per frame)"

**Reality:**
- bpy.ops.render.render() is BLOCKING and takes 800ms-1500ms for 16 samples
- JPEG encoding adds 50-100ms
- Network transfer adds 10-50ms
- **Actual total:** 870ms-1700ms per frame (NOT 30ms)

**Impact:** Users expect responsive viewport, get sluggish 1-2 second updates. Creates false expectations.

**Fix:**
1. Update comment to realistic numbers
2. Update SETUP.md expectations
3. Consider alternative: use persistent frame buffer + incremental sampling (complex, future work)

**Updated claim:**
```python
# Fast progressive rendering by keeping Blender loaded.
# Latency: ~1-2s per frame (vs ~2s subprocess + overhead)
```

**Estimated effort:** 10 minutes (document reality)

---

## 🟠 HIGH PRIORITY (Should fix before release)

### Issue #5: No Timeout on Binary WebSocket Receive
**File:** `server/server.py` line 57
**Severity:** HIGH
**Status:** UNFIXED

When receiving binary data (`.blend` file upload), no timeout:
```python
if data.get("has_binary"):
    binary = await websocket.recv()  # Can wait forever!
```

**Impact:** If network drops during upload, server hangs indefinitely.

**Fix:**
```python
if data.get("has_binary"):
    try:
        binary = await asyncio.wait_for(websocket.recv(), timeout=300.0)
    except asyncio.TimeoutError:
        await self._send_error(ws, "Binary upload timeout (> 5 minutes)")
        return
```

**Estimated effort:** 10 minutes

---

### Issue #6: GPU Setup Falls Back to CPU Silently
**File:** `server/viewport_live.py` lines 65-69
**Severity:** HIGH
**Status:** UNFIXED

If GPU setup fails, code logs warning but continues with CPU:
```python
logger.warning("GPU setup failed — will use CPU (slow)")
```

**Impact:** Renders take 60x longer (CPU vs RTX 5090), user doesn't know why system is "broken".

**Fix:** Send error to client AND notify in UI:
```python
if not gpu_available:
    await self._send_error(ws, "GPU not available. No OPTIX/CUDA devices detected. Using CPU (slow).")
    # Also relay to client for UI display
```

**Estimated effort:** 15 minutes

---

### Issue #7: Concurrent SCENE_FULL Messages Cause Protocol Desync
**File:** `server/server.py` lines 55-58
**Severity:** MEDIUM
**Status:** UNFIXED

If two SCENE_FULL messages arrive rapidly:
```python
if data.get("has_binary"):
    binary = await websocket.recv()  # Assumes next msg is binary
```

Server doesn't check if received message is actually binary or another header.

**Fix:** Validate binary message type:
```python
if data.get("has_binary"):
    binary = await asyncio.wait_for(websocket.recv(), timeout=30.0)
    if isinstance(binary, dict):  # Got another header, not binary!
        await self._send_error(ws, "Protocol error: expected binary, got message header")
        return
```

**Estimated effort:** 15 minutes

---

### Issue #8: Race Condition on Viewport Camera Update
**File:** `server/server.py` lines 175-186
**Severity:** MEDIUM
**Status:** UNFIXED

When user orbits camera rapidly, old render task may not be cancelled before new one starts.

**Fix:**
```python
async def _handle_viewport_start(self, ws, data: dict):
    self._cancel_viewport()  # Cancel old task FIRST
    # ... rest of method
```

**Estimated effort:** 5 minutes

---

### Issue #9: Missing Render Result Image Check
**File:** `server/viewport_live.py` line 126
**Severity:** MEDIUM
**Status:** UNFIXED

```python
pixels = bpy.data.images["Render Result"].pixels[:]
```

If render fails, this key doesn't exist → crashes.

**Fix:**
```python
if "Render Result" not in bpy.data.images:
    logger.error("Render failed — no image in buffer")
    return None
pixels = bpy.data.images["Render Result"].pixels[:]
```

**Estimated effort:** 5 minutes

---

## 🟡 MEDIUM PRIORITY (Polish & stability)

### Issue #10: SETUP.md Ambiguous about shared/ Location
**File:** `SETUP.md` line 70
**Severity:** MEDIUM
**Status:** UNFIXED

"Also copy the `shared/` directory next to the addon, or install the shared module."

This is unclear. Does "next to" mean:
- `~/.config/blender/5.0/scripts/shared/` ?
- `~/.config/blender/5.0/scripts/addons/shared/` ?

**Fix:** Be explicit with full paths and visual tree:
```
On Mac, copy:
  blender-remote-gpu/addon → ~/.config/blender/5.0/scripts/addons/remote_gpu_render
  blender-remote-gpu/shared → ~/.config/blender/5.0/scripts/addons/shared

Final structure:
  ~/.config/blender/5.0/scripts/addons/
    ├── remote_gpu_render/  (addon files: __init__.py, engine.py, etc.)
    └── shared/             (shared files: protocol.py, constants.py)
```

**Estimated effort:** 10 minutes

---

### Issue #11: Missing Error Handling on bpy.ops.render.render()
**File:** `server/viewport_live.py` line 123
**Severity:** MEDIUM
**Status:** UNFIXED

The operator call `bpy.ops.render.render()` may fail in headless context.

**Fix:** Try alternative approaches:
```python
try:
    bpy.ops.render.render(write_still=False, use_viewport=False)
except RuntimeError as e:
    logger.error(f"Operator failed (headless mode?): {e}. Trying alternative...")
    # Fallback: use subprocess mode or alternative rendering
```

**Estimated effort:** 20 minutes (depends on fallback strategy)

---

### Issue #12: Addon Should Validate Scene Before Sync
**File:** `addon/engine.py` render() method
**Severity:** LOW
**Status:** UNFIXED

Before syncing, should check:
- Are there external textures?
- Are they packed?
- Is the scene saved?

Provide warnings:
```python
# Check for external textures
external_images = [img for img in bpy.data.images if img.filepath and not img.packed_file]
if external_images:
    self.report({"WARNING"}, f"Scene has {len(external_images)} external texture(s). They may not render on remote GPU.")
```

**Estimated effort:** 20 minutes

---

## TEST CASES (To verify fixes)

### ✅ Test: External Texture Rendering
```
1. Create scene.blend with external /Users/mk/textures/wood.jpg
2. Mac: viewport orbit → should render with texture
3. Mac: F12 final render → should render with texture
Expected: Both show correct texture
```

### ✅ Test: Addon Installation
```
1. Download repo as ZIP
2. Extract addon/ and shared/ to ~/.config/blender/5.0/scripts/addons/
3. Open Blender, Edit > Preferences > Add-ons > Search "Remote GPU"
Expected: Addon appears and loads without error
```

### ✅ Test: Final Render
```
1. Connect to server
2. Set resolution 1920x1080, samples 128
3. Press F12
Expected: Render completes, image displays in Blender
```

### ✅ Test: Viewport Latency
```
1. Connect to server, orbit camera in viewport
2. Check time between camera movement and frame update
Expected: <2 seconds (realistic), NOT <50ms (false claim)
```

### ✅ Test: Network Drop During Upload
```
1. Setup: script that kills network after 50MB sent
2. User renders large scene (>100MB)
3. Network drops mid-upload
Expected: Server sends error "Upload timeout", not hang
```

---

## SUMMARY

| Priority | Count | Status |
|----------|-------|--------|
| 🔴 CRITICAL | 4 | All unfixed |
| 🟠 HIGH | 4 | All unfixed |
| 🟡 MEDIUM | 3 | All unfixed |
| 🟢 LOW | 1 | Unfixed |
| **TOTAL** | **12** | **0 fixed** |

**Recommendation:** Fix all CRITICAL issues before GitHub release. They are the difference between "code that compiles" and "code that works".

