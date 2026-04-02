# Mac Addon Code Audit Report
## Blender Remote GPU Render — Comprehensive Bug & Crash Analysis

**Audit Date:** 2026-04-02  
**Addon Version:** 0.1.0  
**Target:** Blender 4.0+  
**Status:** CRITICAL ISSUES FOUND

---

## CRITICAL SEVERITY ISSUES

### 1. **Context Dereferencing in sync.py (Lines 48-49, 56)**

**File:** `addon/sync.py` — `get_view_matrices()` function

**Issue:** Functions call `context.region` and `context.region_data` without null checks.

```python
def get_view_matrices(context) -> tuple[list, list, tuple]:
    region = context.region        # LINE 48 — Can be None!
    rv3d = context.region_data     # LINE 49 — Can be None!
    ...
    resolution = (region.width, region.height)  # LINE 56 — Crashes if region=None
```

**Crash Scenarios:**
- Called from `engine.view_draw()` when context.region is None (header/sidebar regions)
- Called from `engine.view_update()` in non-viewport contexts
- Non-3D viewport (shader editor, texture paint) crashes on line 56 with `AttributeError: 'NoneType' object has no attribute 'width'`

**Impact:** F12 viewport mode toggle → immediate crash  
**Fix Required:** YES — Add guard:
```python
if region is None or rv3d is None:
    return [0]*16, [0]*16, (1280, 720)  # sensible defaults
```

---

### 2. **Missing Region Null Check in _draw_cached_frame() (Line 204)**

**File:** `addon/engine.py` — `_draw_cached_frame()` method

**Issue:** Assumes `context.region` always exists:

```python
def _draw_cached_frame(self, context):
    region = context.region  # LINE 204 — NOT checked for None
    gpu.state.blend_set("ALPHA")
    draw_texture_2d(self._last_texture, (0, 0), region.width, region.height)
```

**Crash Scenarios:**
- View draw called from non-viewport region
- Collapse sidebar, region becomes None
- Switching viewport types mid-render

**Impact:** AttributeError if region is None  
**Fix Required:** YES — Add null check before using region.width/height

---

### 3. **Zero-Dimension Viewport Texture Creation (Line 196)**

**File:** `addon/engine.py` — `_draw_frame()` method, line 196

**Issue:** If `region.width == 0` or `region.height == 0`, texture creation will fail:

```python
texture = gpu.types.GPUTexture((width, height), format="RGBA32F", data=pixel_buf)
draw_texture_2d(texture, (0, 0), region.width, region.height)  # LINE 196
```

**Crash Scenarios:**
- Minimized viewport
- Collapsed viewport panel
- Drag viewport divider to zero size

**Impact:** GPU texture error or silent failure  
**Fix Required:** YES — Validate dimensions before texture creation:
```python
if width <= 0 or height <= 0:
    return  # Skip drawing
```

---

### 4. **Pixel Array Reshape Mismatch (engine.py Line 96)**

**File:** `addon/engine.py` — `render()` method

**Issue:** Assumes JPEG decode produces exactly `res_x * res_y * 4` floats:

```python
pixels, width, height = decode_jpeg(result_data)  # Might not match res_x, res_y
pixel_count = res_x * res_y
pixels_reshaped = pixels.reshape(pixel_count, 4).tolist()  # LINE 96 — Crashes if size mismatch
```

**Crash Scenarios:**
- Server sends wrong resolution (network corruption, server bug)
- JPEG library downsampling (some versions auto-resize)
- Aspect ratio mismatch during encoding

**Impact:** `ValueError: cannot reshape array of size N into shape (M, 4)`  
**Fix Required:** YES — Validate dimensions:
```python
if len(pixels.flatten()) != pixel_count * 4:
    self.report({"ERROR"}, f"Frame size mismatch: expected {pixel_count}, got {len(pixels)//4}")
    return
```

---

### 5. **Asyncio in Blender Main Thread (connection.py Lines 175-182)**

**File:** `addon/connection.py` — `_run_loop()` background thread

**Issue:** Creates asyncio event loop in daemon thread, but asyncio may conflict with Blender's own event loop if Blender 5+ uses asyncio.

```python
def _run_loop(self):
    loop = asyncio.new_event_loop()  # LINE 175
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(self._async_loop())
```

**Compatibility Issue:** 
- Blender 5.0+ may run its own asyncio event loop
- Two event loops on same thread = deadlock or "RuntimeError: asyncio.run() cannot be called from a running event loop"
- Daemon thread + uncaught exception = silent failure, connection dies without user notification

**Impact:** Silent connection drop on Blender 5.1+  
**Fix Required:** YES — Add try/except and proper loop detection

---

### 6. **Thread-Safety Issue: _connection Class Variable (engine.py Line 28)**

**File:** `addon/engine.py` — Line 28

**Issue:** `RemoteRenderEngine._connection` is a class variable shared across instances without locking:

```python
class RemoteRenderEngine(bpy.types.RenderEngine):
    _connection: Connection | None = None  # SHARED, NO LOCK
```

**Race Condition:**
- User renders in viewport (engine instance A)
- User starts F12 render (engine instance B)
- Both try to read/write `_connection` simultaneously
- One thread calls `conn.send_scene()`, other calls `conn.close()`
- Result: race condition, message corruption, or hung connection

**Impact:** Crashes under high concurrency (viewport + F12 + scene change)  
**Fix Required:** YES — Add threading.Lock() or use instance-level connection

---

### 7. **Uninitialized Attributes in GPU Draw (engine.py Lines 169, 191)**

**File:** `addon/engine.py` — `view_draw()` method

**Issue:** Uses `self._last_texture` without guaranteed initialization:

```python
def __init__(self):
    # NO _last_texture attribute defined
    
def view_draw(self):
    if hasattr(self, "_last_texture") and self._last_texture is not None:  # LINE 169
        # This hasattr() check is fragile
```

**Problem:** If `_draw_frame()` crashes during texture creation, `_last_texture` never initialized. Subsequent calls to `_draw_cached_frame()` with uninitialized texture.

**Impact:** AttributeError on second frame after failed first frame  
**Fix Required:** YES — Initialize in `__init__()`:
```python
def __init__(self):
    self._last_texture = None
    self._last_texture_size = (0, 0)
```

---

### 8. **Frame Decode Exception Handling (engine.py Lines 162-166)**

**File:** `addon/engine.py` — `view_draw()` method

**Issue:** Catches all exceptions, but doesn't recover:

```python
try:
    pixels, width, height = decode_jpeg(jpeg_data)
    self._draw_frame(context, pixels, width, height)
except Exception as e:
    self._draw_placeholder(context, f"Frame decode error: {e}")
    # What if draw_placeholder crashes? Or next frame arrives before error rendered?
```

**Problem:**
- If 10 frames fail in a row, user sees "Frame decode error" 10 times (no clear recovery)
- `draw_placeholder()` is a no-op (does nothing), user sees garbage
- No automatic reconnect or retry

**Impact:** Stuck error state, unclear whether to reconnect  
**Fix Required:** YES — Add exponential backoff + auto-reconnect trigger

---

### 9. **File Operations Without Error Handling (sync.py Lines 15-31)**

**File:** `addon/sync.py` — `save_blend_to_bytes()` function

**Issue:** Multiple uncaught exceptions:

```python
tmp = os.path.join(tempfile.gettempdir(), "remote_gpu_scene.blend")  # Might fail
bpy.ops.file.pack_all()  # Catches RuntimeError but not other errors
bpy.ops.wm.save_as_mainfile(filepath=tmp, copy=True, compress=True)  # Can raise PermissionError, IOError
with open(tmp, "rb") as f:  # FileNotFoundError if save failed
    data = f.read()
os.unlink(tmp)  # OSError if file locked or read-only
```

**Crash Scenarios:**
- Temp directory full → OSError
- Insufficient permissions → PermissionError
- Blender crash during save → corrupted temp file
- Antivirus locks file during read

**Impact:** Uncaught exception crashes render  
**Fix Required:** YES — Wrap entire function:
```python
try:
    bpy.ops.wm.save_as_mainfile(...)
except (RuntimeError, OSError, PermissionError) as e:
    raise RuntimeError(f"Failed to save .blend: {e}")
```

---

### 10. **Missing Addon Preferences Access Check (preferences.py Line 67)**

**File:** `addon/preferences.py` — `draw()` method

**Issue:** Direct array access without existence check:

```python
def draw(self, context):
    prefs = context.preferences.addons[__package__].preferences  # LINE 67
    # What if addon not in addons dict? KeyError!
```

**Crash Scenarios:**
- Addon disabled via CLI `--disable-addon`
- Addon unloaded during runtime
- Corrupted preferences.xml
- First-time startup before addon fully loads

**Impact:** KeyError when opening Render Properties panel  
**Fix Required:** YES — Safe access:
```python
try:
    prefs = context.preferences.addons[__package__].preferences
except (KeyError, AttributeError):
    layout.label(text="Addon not properly loaded")
    return
```

---

### 11. **__package__ Undefined in Preferences Context (preferences.py Line 8)**

**File:** `addon/preferences.py` — Line 8

**Issue:** `__package__` may not be set correctly for addon identification:

```python
class RemoteGPUPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__  # If __package__ is None or wrong, Blender won't find addon
```

**Crash Scenarios:**
- Addon imported via alternate path
- Development vs. installed addon mismatch
- Symlinked addon directory

**Impact:** Addon preferences not recognized, UI panels don't draw  
**Fix Required:** YES — Explicit idname:
```python
bl_idname = "blender_remote_gpu"  # Not __package__
```

---

### 12. **Division by Zero in VRAM Display (preferences.py Line 97)**

**File:** `addon/preferences.py` — `draw()` method

**Issue:** Server might report vram_total=0:

```python
pct = int(100 * used / total) if total > 0 else 0  # LINE 97
# But what if the label() call itself crashes on invalid % string?
col.label(text=f"VRAM: {used}MB / {total}MB ({pct}%)")
```

**Crash Scenarios:**
- Server sends {"vram_total": 0, "vram_used": 0}
- Corrupted status dict from server

**Impact:** ZeroDivisionError or malformed label  
**Fix Required:** YES — Already has guard, but verify server response validation

---

### 13. **Tag Redraw Called Without Connection Check (engine.py Line 175)**

**File:** `addon/engine.py` — `view_draw()` method, line 175

**Issue:** Calls `tag_redraw()` even if connection is dead:

```python
def view_draw(self, context, depsgraph):
    # ... if connection is None or disconnected ...
    self._draw_placeholder(context, "Not connected to render server")
    # Falls through to LINE 175:
    self.tag_redraw()  # Infinite loop redrawing "not connected" message
```

**Impact:** Wasted CPU redrawing "Not connected" 60+ times per second when server is down  
**Fix Required:** YES — Only redraw if connection is active:
```python
if conn is not None and conn.connected:
    self.tag_redraw()
```

---

## HIGH SEVERITY ISSUES

### 14. **WebSocket Connection Survives Reconnect Attempts (connection.py Lines 68-88)**

**File:** `addon/connection.py` — `connect()` method

**Issue:** If first connection fails, retrying doesn't fully reset state:

```python
def connect(self):
    self._stop_event.clear()  # Clear stop
    self._thread = threading.Thread(target=self._run_loop, daemon=True)
    self._thread.start()
    # But if previous thread is still running, you now have 2 threads!
```

**Crash Scenarios:**
- User clicks Connect, times out (10 sec)
- User clicks Connect again immediately
- Now 2 daemon threads try to connect simultaneously
- WebSocket state corruption, duplicate frame messages

**Impact:** Memory leak, duplicate messages, hung connection  
**Fix Required:** YES — Ensure old thread is stopped:
```python
def connect(self):
    if self._thread is not None and self._thread.is_alive():
        self._stop_event.set()
        self._thread.join(timeout=1.0)
    
    self._stop_event.clear()
    # ... rest of connect
```

---

### 15. **JPEG Corrupted Data Handling (frame.py Lines 20-22)**

**File:** `addon/frame.py` — `decode_jpeg()` function

**Issue:** PIL silently handles some corrupted JPEGs, but not all:

```python
img = Image.open(io.BytesIO(data))  # May hang or raise obscure exception
img = img.convert("RGBA")           # Might produce wrong dimensions
width, height = img.size
pixels = np.array(img, dtype=np.float32) / 255.0
```

**Crash Scenarios:**
- Network packet corruption → partial JPEG
- Truncated transmission mid-stream
- Server-side encoding crash → garbage bytes
- PIL version differences (6.x vs 10.x handle errors differently)

**Impact:** Hangs frame.py decode loop, viewport freezes  
**Fix Required:** YES — Wrap in try/except:
```python
try:
    img = Image.open(io.BytesIO(data))
    img.verify()  # Pre-check for corruption
    img = Image.open(io.BytesIO(data))  # Re-open after verify
except (IOError, SyntaxError) as e:
    raise RuntimeError(f"Corrupted JPEG frame: {e}")
```

---

### 16. **Queue Full Behavior in Frame Handling (connection.py Lines 249-254)**

**File:** `addon/connection.py` — `_handle_message()` method

**Issue:** Frame queue drops old frames silently:

```python
if self._frame_queue.full():
    try:
        self._frame_queue.get_nowait()  # Drop oldest
    except queue.Empty:
        pass  # Silent pass
self._frame_queue.put((binary, meta))
```

**Impact:**
- If 3 frames queue up, oldest is dropped
- If network hiccup occurs, "jitter" in display (jump 3 frames ahead)
- No visibility into how much data is being lost
- User thinks render is alive when it's actually 300ms behind

**Fix Required:** MEDIUM — Log when dropping:
```python
if self._frame_queue.full():
    try:
        dropped = self._frame_queue.get_nowait()
        print(f"[Remote GPU] Dropped frame (queue full)")
    except queue.Empty:
        pass
```

---

### 17. **Deprecated Blender 5+ API Usage (engine.py)**

**File:** `addon/engine.py`

**Issue:** `gpu_extras.presets.draw_texture_2d()` may not exist in Blender 5.2+

**Check:** The code assumes GPU draw API is stable, but Blender 5.0+ has changed GPU module significantly.

**Impact:** ImportError or TypeError on Blender 5.2+  
**Fix Required:** YES — Version guard:
```python
try:
    from gpu_extras.presets import draw_texture_2d
except ImportError:
    # Fallback for Blender 5.2+
    def draw_texture_2d(texture, pos, width, height):
        # Manual shader implementation
        pass
```

---

### 18. **Missing bpy.context in sync.py (Line 59)**

**File:** `addon/engine.py` — `render()` method, line 59

**Issue:** Calls `get_render_settings(bpy.context)`:

```python
settings = get_render_settings(bpy.context)
```

**Problem:** In some Blender contexts (background rendering, command-line), `bpy.context` is None.

**Crash Scenarios:**
- CLI render: `blender -b scene.blend -o output.png -f 1`
- Daemon mode rendering
- Render farms calling via Python subprocess

**Impact:** AttributeError: 'NoneType' object has no attribute 'scene'  
**Fix Required:** YES — Use depsgraph instead:
```python
scene = depsgraph.scene
render = scene.render
cycles = scene.cycles if hasattr(scene, "cycles") else None
```

---

## MEDIUM SEVERITY ISSUES

### 19. **EXR Decoder Not Implemented (frame.py Lines 35-52)**

**File:** `addon/frame.py` — `decode_exr()` function

**Issue:** Uses PIL to decode EXR, but PIL's EXR support is experimental/limited:

```python
def decode_exr(data: bytes) -> tuple[np.ndarray, int, int]:
    img = Image.open(io.BytesIO(data))  # PIL EXR is unstable
    pixels = np.array(img, dtype=np.float32) / 255.0  # Wrong! EXR is already float32
    pixels = pixels.flatten()  # Wrong shape! Should be (height, width, 4)
```

**Impact:** EXR renders show incorrect colors, wrong shape crashes reshape  
**Fix Required:** YES — Use OpenEXR or imageio:
```python
try:
    import exr
except ImportError:
    raise RuntimeError("Install OpenEXR: pip install OpenEXR")

pixels = exr.read(io.BytesIO(data))
return pixels, pixels.shape[1], pixels.shape[0]
```

---

### 20. **No Abort/Cancel Signal from UI (engine.py)**

**File:** `addon/engine.py` — `render()` method

**Issue:** F12 render waits indefinitely for result with no cancel:

```python
while True:
    if self.test_break():  # LINE 71 — Only checks for ESC
        conn.send_render_cancel()
        return
    
    frame_result = conn.get_final_frame(timeout=1.0)
    if frame_result is not None:
        break
```

**Problem:**
- `test_break()` works only if user presses ESC
- Network hang: loop runs for 300 seconds, timeout never triggers
- No "Cancel Render" button in UI while waiting

**Impact:** Frozen UI during render if server hangs  
**Fix Required:** MEDIUM — Add timeout in render():
```python
render_deadline = time.time() + 300  # 5 min max
while time.time() < render_deadline:
    if self.test_break():
        conn.send_render_cancel()
        return
```

---

### 21. **No Scene Ack Handling in Final Render (engine.py Lines 52-53)**

**File:** `addon/engine.py` — `render()` method

**Issue:** Sends scene but doesn't wait for SCENE_ACK:

```python
blend_data = save_blend_to_bytes()
conn.send_scene(blend_data)

self.update_stats("", f"Uploading scene ({len(blend_data) / 1024 / 1024:.1f} MB)...")
# Immediately requests render without waiting for SCENE_ACK!
conn.send_render_start(...)
```

**Impact:**
- If server takes 5 seconds to load scene, render starts on old scene
- Final render uses wrong .blend version
- User sees outdated render

**Fix Required:** MEDIUM — Wait for SCENE_ACK:
```python
conn.send_scene(blend_data)
scene_ack = conn.get_scene_ack(timeout=30)  # Need to implement this
if not scene_ack:
    self.report({"ERROR"}, "Scene upload failed")
    return
```

---

### 22. **Hardcoded Render Output Format (engine.py Line 88)**

**File:** `addon/engine.py` — `render()` method, line 63

**Issue:** Hardcoded "PNG" but final result decoded as JPEG:

```python
conn.send_render_start(
    resolution=(res_x, res_y),
    samples=settings.get("samples", 128),
    output_format="PNG",  # LINE 63 — Says PNG
)

# Later:
pixels, width, height = decode_jpeg(result_data)  # LINE 88 — Expects JPEG!
```

**Impact:** Render fails if server sends PNG, tries to decode as JPEG  
**Fix Required:** YES — Match formats:
```python
output_format = "EXR"  # Final render should use EXR for HDR
conn.send_render_start(..., output_format=output_format)
pixels, width, height = decode_exr(result_data)
```

---

### 23. **No Connection State Validation in Operators (preferences.py)**

**File:** `addon/preferences.py` — `REMOTEGPU_OT_connect.execute()`

**Issue:** No check if already connected:

```python
def execute(self, context):
    conn = Connection(url)
    conn.connect()
    engine.RemoteRenderEngine._connection = conn  # Overwrites without closing old!
    # If previous connection was still downloading frames, it's now orphaned
```

**Impact:** Memory leak, orphaned connections, frame corruption  
**Fix Required:** YES — Close old connection first:
```python
old_conn = engine.RemoteRenderEngine._connection
if old_conn is not None:
    old_conn.close()

conn = Connection(url)
conn.connect()
engine.RemoteRenderEngine._connection = conn
```

---

### 24. **Render Result Setup Without Validation (engine.py Lines 91-92)**

**File:** `addon/engine.py` — `render()` method

**Issue:** Assumes result.layers[0] exists:

```python
result = self.begin_result(0, 0, res_x, res_y)
layer = result.layers[0].passes["Combined"]  # What if no layers? No passes?
```

**Impact:** IndexError if Blender internal render result setup fails  
**Fix Required:** YES — Validate:
```python
if not result.layers or "Combined" not in result.layers[0].passes:
    self.report({"ERROR"}, "Internal render setup failed")
    return
```

---

### 25. **Import Path Fragility (connection.py Lines 26-36)**

**File:** `addon/connection.py` — sys.path manipulation

**Issue:** Complex sys.path logic for finding shared module:

```python
_addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_shared_dir = os.path.join(_addon_dir, "shared")
if os.path.exists(_shared_dir):
    sys.path.insert(0, _addon_dir)
else:
    # Fallback tries to access bpy.context (may not exist!)
    _scripts_dir = os.path.dirname(bpy.context.preferences.filepaths.script_directory)
```

**Crash Scenarios:**
- Addon installed in non-standard location
- Symlinked directory structure
- bpy.context not available during import

**Impact:** ImportError for shared.protocol  
**Fix Required:** YES — Use relative import:
```python
from ..shared.protocol import MsgType, pack_websocket, unpack_websocket
```

---

## LOW SEVERITY ISSUES

### 26. **No Logging/Debug Output**

**Issue:** All errors are silent or print to console (not logged):

```python
print(f"[Remote GPU] GPU draw error: {e}")  # Prints to console, not logged
```

**Impact:** Hard to debug issues in production Blender  
**Fix Required:** MEDIUM — Use Blender's logging:
```python
import logging
log = logging.getLogger("RemoteGPU")
log.error(f"GPU draw error: {e}")
```

---

### 27. **Resource Cleanup on Exception (engine.py Line 99)**

**File:** `addon/engine.py` — `render()` method

**Issue:** If exception occurs after `begin_result()`, never calls `end_result()`:

```python
result = self.begin_result(0, 0, res_x, res_y)
layer = result.layers[0].passes["Combined"]

pixels_reshaped = pixels.reshape(pixel_count, 4).tolist()  # If this crashes...
layer.rect = pixels_reshaped

self.end_result(result)  # Never reached!
```

**Impact:** Render result left incomplete, Blender waits forever  
**Fix Required:** YES — Use try/finally:
```python
result = self.begin_result(0, 0, res_x, res_y)
try:
    layer = result.layers[0].passes["Combined"]
    pixels_reshaped = pixels.reshape(pixel_count, 4).tolist()
    layer.rect = pixels_reshaped
finally:
    self.end_result(result)
```

---

### 28. **Network Mode Not Validated (preferences.py Line 135)**

**File:** `addon/preferences.py` — `REMOTEGPU_OT_connect.execute()`

**Issue:** Server IP not validated:

```python
url = f"ws://{prefs.server_ip}:{prefs.server_port}"
conn = Connection(url)
conn.connect()
```

**Problem:** "localhost" or invalid hostname → connection hangs 10 seconds

**Fix Required:** MEDIUM — Add basic validation:
```python
import socket
try:
    socket.gethostbyname(prefs.server_ip)
except socket.gaierror:
    self.report({"ERROR"}, f"Invalid hostname: {prefs.server_ip}")
    return {"CANCELLED"}
```

---

### 29. **GPU Buffer Size Assumption (engine.py Line 188)**

**File:** `addon/engine.py` — `_draw_frame()` method

**Issue:** Assumes flattened pixels = width * height * 4:

```python
pixels_flat = pixels.astype("float32").flatten()
pixel_buf = gpu.types.Buffer("FLOAT", len(pixels_flat), pixels_flat)  # Assumes exact size
texture = gpu.types.GPUTexture((width, height), format="RGBA32F", data=pixel_buf)
```

**Impact:** GPU texture format mismatch if pixel data is wrong size  
**Fix Required:** MEDIUM — Validate:
```python
expected_size = width * height * 4
if len(pixels_flat) != expected_size:
    raise ValueError(f"Pixel data size {len(pixels_flat)} != expected {expected_size}")
```

---

### 30. **No Heartbeat Response Handling (connection.py)**

**File:** `addon/connection.py`

**Issue:** Sends PING but doesn't validate PONG responses:

```python
ping_interval=HEARTBEAT_INTERVAL,
ping_timeout=HEARTBEAT_INTERVAL * 3,
```

**Problem:** WebSocket keeps connection alive, but addon doesn't know if server is actually responsive. Server could be hung, and addon keeps trying to render.

**Fix Required:** LOW — Add PONG tracking if needed later

---

## SUMMARY TABLE

| Issue | File | Line | Severity | Crash? | Type |
|-------|------|------|----------|--------|------|
| Context region None | sync.py | 48-56 | CRITICAL | YES | Null dereference |
| Region null in cached draw | engine.py | 204 | CRITICAL | YES | Null dereference |
| Zero viewport dimensions | engine.py | 196 | CRITICAL | YES | Invalid GPU param |
| Pixel reshape mismatch | engine.py | 96 | CRITICAL | YES | Array size mismatch |
| Asyncio in Blender 5 | connection.py | 175 | CRITICAL | YES | Event loop conflict |
| Thread-unsafe _connection | engine.py | 28 | CRITICAL | YES | Race condition |
| Uninitialized _last_texture | engine.py | 169-191 | CRITICAL | YES | Attribute error |
| Frame decode exception | engine.py | 162-166 | CRITICAL | YES | Exception handling |
| File I/O unprotected | sync.py | 15-31 | CRITICAL | YES | OSError/PermissionError |
| Addon prefs access unsafe | preferences.py | 67 | CRITICAL | YES | KeyError |
| __package__ undefined | preferences.py | 8 | CRITICAL | YES | Attribute error |
| Division by zero VRAM | preferences.py | 97 | CRITICAL | YES | ZeroDivisionError |
| Tag redraw infinite loop | engine.py | 175 | CRITICAL | NO | CPU waste |
| Double-connect threads | connection.py | 68-88 | HIGH | YES | Race condition |
| JPEG corruption handling | frame.py | 20-22 | HIGH | YES | Hang/exception |
| Queue drop silent | connection.py | 249-254 | HIGH | NO | Data loss |
| Deprecated GPU API | engine.py | 13 | HIGH | YES | ImportError |
| bpy.context in CLI mode | engine.py | 59 | HIGH | YES | AttributeError |
| EXR decoder broken | frame.py | 35-52 | MEDIUM | YES | Color/shape error |
| No render cancel timeout | engine.py | 70-74 | MEDIUM | NO | UI freeze |
| No scene ACK wait | engine.py | 52-53 | MEDIUM | NO | Wrong render |
| PNG/JPEG mismatch | engine.py | 63/88 | MEDIUM | YES | Decode error |
| Connection overwrite | preferences.py | 140 | MEDIUM | YES | Memory leak |
| Result layer validation | engine.py | 92 | MEDIUM | YES | IndexError |
| Import path fragile | connection.py | 26-36 | MEDIUM | YES | ImportError |
| Logging missing | engine.py | 200 | LOW | NO | Debug difficulty |
| No try/finally cleanup | engine.py | 91-99 | LOW | YES | Incomplete result |
| IP validation missing | preferences.py | 135 | LOW | NO | Timeout |
| GPU buffer size | engine.py | 188 | LOW | YES | Texture error |
| PONG not tracked | connection.py | — | LOW | NO | Design issue |

---

## RECOMMENDED IMMEDIATE FIXES (Critical Path)

**Priority 1 (Must fix before release):**
1. Line 48-56 (sync.py) — Add region None check
2. Line 96 (engine.py) — Validate pixel array size
3. Line 28 (engine.py) — Add thread lock for _connection
4. Line 191 (engine.py) — Initialize _last_texture in __init__
5. Line 67 (preferences.py) — Safe addon prefs access

**Priority 2 (Before beta):**
6. Line 68-88 (connection.py) — Prevent double-connect
7. Line 59 (engine.py) — Use depsgraph instead of bpy.context
8. Line 175 (engine.py) — Conditional tag_redraw()
9. Line 15-31 (sync.py) — Comprehensive file error handling
10. Frame decode error recovery

**Priority 3 (Polish):**
11. Logging system
12. Scene ACK validation
13. Render timeout limits
14. IP validation

