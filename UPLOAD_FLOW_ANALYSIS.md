# HTTP Scene Upload Flow Analysis

## Executive Summary
**Status**: CRITICAL ISSUE FOUND - Race condition and missing state update in main loop

The upload flow has a synchronization gap where `scene_loaded` flag may not be set to `True` after load completes if an exception occurs, causing persistent "scene not loaded" errors.

---

## 1. How Scene File is Received and Stored

### Server (server.py) - Lines 228-241
```
Request arrives at HTTP handler
↓
Handler decodes base64 blend_data from JSON body
↓
Creates temp file: scene_<uuid>.blend
↓
Writes decoded binary data to disk
↓
Returns path for worker to load
```

**Flow Details:**
- Client sends: `{"type": "scene_upload", "blend_data": "<base64 string>"}`
- Server.py saves to: `C:\Users\{user}\AppData\Local\Temp\scene_<8-char-uuid>.blend`
- File size is logged: `log.info(f"Scene saved to {blend_path} ({size_mb:.1f} MB)")`

---

## 2. How _pending_path is Set

### Two Entry Points in render_worker.py

#### Path A: `load_scene_path` (Lines 171-179)
- HTTP handler receives path directly from client
- **Lock acquired** (line 176): `with _pending_lock:`
- **Path set** (line 177): `_pending_path = blend_path`
- Returns `{"type": "scene_loading"}` response

#### Path B: `load_scene` (Lines 181-193) - Legacy base64 handler
- Decodes base64 directly in worker
- Creates temp file: `tempfile.NamedTemporaryFile(suffix=".blend", delete=False)`
- **Lock acquired** (line 190): `with _pending_lock:`
- **Path set** (line 191): `_pending_path = tmp.name`
- Returns `{"type": "scene_loading"}` response

**Critical Detail**: Line 151 uses `global _pending_path` declaration — **this is MANDATORY** because without it, the assignment would be local-only and the main loop would never see the change.

---

## 3. Communication Flow from Upload to Main Loop

```
┌─────────────────────────────────────────────────────────────┐
│ CLIENT                                                      │
│ POST /upload {"type":"scene_upload", "blend_data":"..."}   │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ SERVER.PY (HTTP Handler Thread)                             │
│ 1. Decode base64 → temp file (scene_XXXX.blend)             │
│ 2. Log: "Scene saved to ..."                                │
│ 3. Call send_to_worker() with path                          │
│    → POST to localhost:9880                                 │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ RENDER_WORKER.PY (HTTP Handler Thread - DAEMON)             │
│ msg_type = "load_scene_path"                                │
│ 1. Lock _pending_lock                                       │
│ 2. Set _pending_path = blend_path                           │
│ 3. Log: "Queued scene load: ..."                            │
│ 4. Return {"type": "scene_loading"}                         │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ SERVER.PY (Main Thread - Polling)                           │
│ Loop: send_to_worker({"type": "ping"}) every 0.5s           │
│ Check response.get("scene_loaded") == True                  │
│ Timeout: 120 seconds (240 attempts × 0.5s)                 │
│ Early exit if: was_loading=true AND                         │
│               scene_loading=false AND scene_loaded=false    │
│               → Return error "Worker failed to load"         │
└──────────────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ RENDER_WORKER.PY (Main Loop - CRITICAL SECTION)             │
│ while True:                                                 │
│   1. Lock _pending_lock                                     │
│   2. Read path = _pending_path                              │
│   3. Set _pending_path = None  ← RESET                      │
│   4. Release lock                                           │
│                                                             │
│   if path:                                                  │
│     5. Set _scene_loading = True                            │
│     6. Set _scene_loaded = False                            │
│     7. Call bpy.ops.wm.open_mainfile(filepath=path)         │
│     8. Set _scene_loaded = True  ← SUCCESS FLAG             │
│     9. (OR caught exception)                                │
│     10. Set _scene_loading = False                          │
│   11. Sleep 0.1s                                            │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. Lock/Synchronization Analysis

### Locks Present
1. **_pending_lock** (threading.Lock): Guards `_pending_path` variable
   - Acquired by HTTP handler (lines 176, 190)
   - Acquired by main loop (line 312)
   - **Correctly protects** reads/writes to the path variable

2. **_render_lock** (threading.Lock): Guards render operations
   - Used for camera updates and render calls
   - **NOT used** for scene loading state variables

### Critical State Variables WITHOUT Locks
```python
_scene_loaded   = False      ← READ by HTTP handlers (lines 165, 197, 209, 233)
_scene_loading  = False      ← READ by HTTP handlers (lines 166, 259-261)
_rendering      = False      ← READ by HTTP handlers (line 167)
```

**Issue**: These flags are read by HTTP handler threads WITHOUT locks, but written by main thread:
- Line 318: `_scene_loaded = False` (main thread)
- Line 323: `_scene_loaded = True` (main thread)
- Lines 317, 328: `_scene_loading` (main thread)

This creates **potential data races** (though Python's GIL may hide issues in practice).

---

## 5. Missing State Update Issue - THE CRITICAL BUG

### Problem Location: Lines 316-328 in render_worker.py

```python
if path:
    _scene_loading = True
    _scene_loaded  = False
    _log(f"Main thread loading: {path}")
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
        setup_gpu()
        _scene_loaded = True            # ← SUCCESS PATH
        _log(f"Scene ready: {len(bpy.data.objects)} objects, compute={_compute_type}")
    except Exception as e:
        _log(f"open_mainfile failed: {e}")
    finally:
        _scene_loading = False
```

### THE BUG
If `bpy.ops.wm.open_mainfile()` raises an exception (lines 325-326), the code:
1. Logs the error
2. **Skips line 323**: `_scene_loaded = True` is NOT executed
3. Jumps to `finally` block (line 327)
4. Sets `_scene_loading = False` (line 328)

**Result**: The worker is left in state:
```
_scene_loading = False
_scene_loaded = False    ← STILL FALSE!
```

### Server.py's Detection (Lines 259-263)
The server DOES detect this failure case:
```python
if ping and ping.get("scene_loading"):
    was_loading = True
elif was_loading and ping and not ping.get("scene_loading") and not ping.get("scene_loaded"):
    log.error("Worker load failed (scene_loading went false without scene_loaded)")
    return {"type": "error", "message": "Worker failed to load scene — check Blender console"}
```

This correctly identifies the failure and returns error to client.

### BUT THERE'S A TIMING RACE
Between the server's error detection and any subsequent requests:
- **Gap exists** where a ping might be sent right before the main loop clears `_pending_path`
- The main loop is NOT atomic: it takes time to acquire lock, read path, release lock
- If exception occurs AND new ping is sent, server correctly detects failure
- However: **No attempt to recover or retry is built in**

### Missing Recovery Path
If the scene fails to load, the main loop:
1. Sets `_scene_loading = False`
2. Sleeps 0.1s (line 342)
3. **Loops back to check _pending_path** — but it's already been reset to None!
4. Never tries to load again
5. **Worker is permanently broken** for that scene

---

## 6. Scene Loaded Flag Verification

### Where `scene_loaded` is Set to True
**File**: render_worker.py, Line 323 (ONLY location)
```python
bpy.ops.wm.open_mainfile(filepath=path)
setup_gpu()
_scene_loaded = True    ← ONLY here
```

### Conditional: Must NOT throw exception
- If ANY exception occurs between `open_mainfile()` call and this line, `_scene_loaded` stays False
- The `finally` block does NOT set it to False (it's already False from line 318)
- There's NO automatic fallback

### What Clears It
- Line 318: Explicitly set to False before load attempt
- That's it — no other code path clears it (except initial module load at line 33)

### Verification in HTTP Handler
```python
Line 165: "scene_loaded": _scene_loaded
```
This flag is read directly (no lock) and returned in ping response.

---

## Summary of Issues

| Issue | Severity | Location | Impact |
|-------|----------|----------|--------|
| **Missing lock on state flags** | HIGH | Lines 33-37 | Data race: `_scene_loaded`, `_scene_loading`, `_rendering` read by threads without lock |
| **Exception swallows success flag** | CRITICAL | Lines 325-326 | If load fails, `_scene_loaded` never set to True; worker permanently broken |
| **No recovery mechanism** | HIGH | Lines 310-342 | Failed loads are never retried; broken state persists |
| **Exception not re-raised** | MEDIUM | Lines 325-326 | Exceptions logged but swallowed; server's error detection depends on timing |
| **Race window between attempts** | MEDIUM | Lines 313-314 | Small window where path could be read while HTTP handler is still writing |

---

## Recommendations

1. **Add lock for state flags** (render_worker.py):
   ```python
   _state_lock = threading.Lock()

   # Before: _scene_loaded = ...
   # After: with _state_lock: _scene_loaded = ...
   ```

2. **Ensure success flag is set** (render_worker.py):
   - Move `_scene_loaded = True` BEFORE any post-load setup
   - OR move setup_gpu() into try/except separately

3. **Add retry mechanism** (render_worker.py):
   - Don't reset `_pending_path` until load succeeds
   - OR queue retry on next loop if exception occurs

4. **Make error detection atomic** (server.py):
   - Implement timeout earlier
   - Don't rely on timing-based state machine

5. **Log more state transitions** (render_worker.py):
   - Log when entering try block
   - Log when setup_gpu() completes
   - Log exact exception type/line

---

## Test Cases to Verify Fix

1. Load valid .blend file → Should set `scene_loaded = True`
2. Load invalid/corrupted .blend file → Should detect error, NOT hang
3. Two consecutive uploads → Second should succeed even if first failed
4. Ping during load → Should return `scene_loading = True` (NOT False during transition)
5. Ping after failed load → Should return `scene_loading = False` AND `scene_loaded = False` (and NOT timeout)
