#!/usr/bin/env python3
"""
Persistent Blender Render Worker b36 — runs INSIDE Blender, keeps scene in GPU memory.

Launch: blender --background --python render_worker.py

b36: EXTREME DEBUG LOGGING - diagnose every step:
  - Elapsed time tracking for all operations
  - Per-thread logging with thread names
  - Memory/thread count tracking
  - File size and existence checks
  - Context window debugging
  - Full exception stack traces
  - Lock acquisition/release tracking
  - Event pump status logging
  - Per-operation timing (open_mainfile, setup_gpu, etc)
  - HTTP handler detailed logs
  - Loop state tracking
"""

import sys
import os
import traceback
from collections import deque
import signal
import platform
import atexit

# Try to import psutil for memory tracking (optional)
try:
    import psutil
except ImportError:
    psutil = None

# Add user site-packages to path (workaround for Blender's isolated Python)
user_site = os.path.expanduser(r"~\AppData\Roaming\Python\Python311\site-packages")
if os.path.exists(user_site) and user_site not in sys.path:
    sys.path.insert(0, user_site)

import bpy
import json
import base64
import tempfile
import time
import threading
import shutil
from http.server import HTTPServer, BaseHTTPRequestHandler

# Timeout constants (in seconds)
_TIMEOUT_LOAD_FILE = 60      # Max 60s for open_mainfile
_TIMEOUT_SETUP_GPU = 15      # Max 15s for GPU detection
_TIMEOUT_RENDER = 300        # Max 300s (5 min) for render.render()
_IS_WINDOWS = platform.system() == "Windows"

WORKER_PORT = int(os.environ.get("WORKER_PORT", "9880"))

_scene_loaded   = False
_scene_loading  = False
_rendering      = False
_state_lock     = threading.Lock()  # Protects: _scene_loaded, _scene_loading, _rendering, _compute_type
_render_lock    = threading.Lock()  # Legacy: kept for bpy.ops calls (Blender context)
_compute_type   = "NONE"

# Pending scene path — HTTP thread writes, main loop reads
_pending_path = None
_pending_lock = threading.Lock()

# Retry queue for failed scene loads
# Each entry: (path, attempt_count, last_retry_time)
_load_retry_queue = deque(maxlen=5)  # Max 5 retries per path
_retry_lock = threading.Lock()
_retry_backoff_seconds = [0.5, 1.0, 2.0, 4.0, 8.0]  # Exponential backoff per attempt

_start_time = time.time()

def _elapsed():
    return f"{time.time() - _start_time:.3f}s"

def _log(msg):
    elapsed = _elapsed()
    tid = threading.current_thread().name
    print(f"[{elapsed}] [Worker] [{tid}] {msg}", flush=True)

def _debug(msg):
    """Extra verbose debug logging"""
    try:
        if psutil:
            proc = psutil.Process()
            mem = proc.memory_info().rss / 1024 / 1024  # MB
            _log(f"[DEBUG] {msg} | MEM={mem:.1f}MB | Threads={threading.active_count()}")
        else:
            _log(f"[DEBUG] {msg} | Threads={threading.active_count()}")
    except:
        _log(f"[DEBUG] {msg}")


# ── Timeout Handler ───────────────────────────────────────────

def _timeout_handler(signum, frame):
    """Signal handler for timeout (Unix/Linux only)"""
    raise TimeoutError("Operation exceeded timeout")


def run_with_timeout(func, timeout_sec, operation_name):
    """
    Execute func with a timeout.
    - Unix/Linux: Uses signal.alarm() (cannot timeout blocking Blender ops reliably)
    - Windows: Uses threading.Timer with best-effort cleanup

    Args:
        func: Callable to execute
        timeout_sec: Timeout in seconds
        operation_name: Human-readable name for logging

    Returns:
        Result of func() if successful

    Raises:
        TimeoutError: If operation exceeds timeout_sec
    """
    _log(f"[TIMEOUT] Starting '{operation_name}' with {timeout_sec}s timeout")

    if _IS_WINDOWS:
        # Windows: Use threading.Timer (signal.alarm not available on Windows)
        result = [None]
        exception = [None]
        timer = [None]

        def wrapper():
            try:
                result[0] = func()
            except Exception as e:
                exception[0] = e

        def on_timeout():
            _log(f"[TIMEOUT] '{operation_name}' exceeded {timeout_sec}s timeout on Windows")
            exception[0] = TimeoutError(f"'{operation_name}' exceeded {timeout_sec}s")

        thread = threading.Thread(target=wrapper, daemon=True)
        thread.start()
        timer[0] = threading.Timer(timeout_sec, on_timeout)
        timer[0].daemon = True
        timer[0].start()

        thread.join(timeout=timeout_sec + 1)  # Wait thread + 1s buffer

        if timer[0].is_alive():
            timer[0].cancel()

        if exception[0]:
            raise exception[0]
        return result[0]

    else:
        # Unix/Linux: Use signal.alarm()
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_sec)
        try:
            result = func()
            signal.alarm(0)  # Cancel alarm
            _log(f"[TIMEOUT_OK] '{operation_name}' completed within {timeout_sec}s")
            return result
        except TimeoutError as e:
            _log(f"[TIMEOUT] '{operation_name}' exceeded {timeout_sec}s: {e}")
            raise
        finally:
            signal.alarm(0)  # Ensure alarm is cancelled
            signal.signal(signal.SIGALRM, old_handler)


def _should_retry_now(last_retry_time, attempt):
    """
    Check if enough time has passed for the next retry attempt.
    attempt is 0-indexed (0 = first retry, 1 = second retry, etc.)
    """
    if attempt >= len(_retry_backoff_seconds):
        return False  # Max retries exceeded
    backoff = _retry_backoff_seconds[attempt]
    elapsed = time.time() - last_retry_time
    return elapsed >= backoff


# ── GPU setup ─────────────────────────────────────────────────

def _setup_gpu_impl():
    """Internal GPU setup implementation (to be wrapped with timeout)"""
    global _compute_type
    try:
        prefs = bpy.context.preferences
        cycles_addon = prefs.addons.get("cycles")
        if cycles_addon:
            for device_type in ("OPTIX", "CUDA"):
                try:
                    cycles_addon.preferences.compute_device_type = device_type
                    cycles_addon.preferences.get_devices()
                    devices = list(cycles_addon.preferences.devices)
                    if devices:
                        for d in devices:
                            d.use = True
                            _log(f"Device: {d.name} ({device_type})")
                        _compute_type = device_type
                        _log(f"Compute: {device_type}")
                        break
                except Exception as e:
                    _log(f"{device_type} unavailable: {e}")
        bpy.context.scene.render.engine = "CYCLES"
        bpy.context.scene.cycles.device = "GPU"
    except Exception as e:
        _log(f"GPU setup warning: {e}")


def setup_gpu():
    """Setup GPU with timeout protection"""
    try:
        run_with_timeout(_setup_gpu_impl, _TIMEOUT_SETUP_GPU, "setup_gpu")
    except TimeoutError as e:
        _log(f"[TIMEOUT_ERROR] setup_gpu failed: {e}")
        _log(f"[FALLBACK] Falling back to CPU rendering")
        global _compute_type
        _compute_type = "CPU"
    except Exception as e:
        _log(f"setup_gpu error: {e}")


def _enable_denoiser(scene):
    scene.cycles.use_denoising = True
    try:
        scene.cycles.denoiser = "OPTIX" if _compute_type == "OPTIX" else "OPENIMAGEDENOISE"
    except Exception:
        pass


# ── Camera helper ─────────────────────────────────────────────

def set_camera_from_matrix(view_matrix_flat):
    import mathutils
    cam_data = bpy.data.cameras.get("_remote_cam") or bpy.data.cameras.new("_remote_cam")
    cam_obj  = bpy.data.objects.get("_remote_cam_obj")
    if not cam_obj:
        cam_obj = bpy.data.objects.new("_remote_cam_obj", cam_data)
        bpy.context.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    m = mathutils.Matrix((
        view_matrix_flat[0:4],
        view_matrix_flat[4:8],
        view_matrix_flat[8:12],
        view_matrix_flat[12:16],
    ))
    cam_obj.matrix_world = m.inverted()


# ── Render ────────────────────────────────────────────────────

def _render_frame_impl(scene, output_path):
    """Internal render implementation (to be wrapped with timeout)"""
    bpy.ops.render.render(write_still=True)
    return os.path.isfile(output_path)


def render_frame(width, height, samples=1, quality=75):
    global _rendering

    scene = bpy.context.scene
    scene.render.resolution_x         = width
    scene.render.resolution_y         = height
    scene.render.resolution_percentage = 100
    scene.cycles.samples               = samples
    _enable_denoiser(scene)
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality     = quality

    output_dir  = None
    output_path = None
    start       = time.time()
    # ATOMIC: Set _rendering under lock
    with _state_lock:
        _rendering = True
    try:
        output_dir  = tempfile.mkdtemp(prefix="wrkr_")
        output_path = os.path.join(output_dir, "frame.jpg")
        scene.render.filepath = output_path

        # Run render with timeout protection
        try:
            render_ok = run_with_timeout(
                lambda: _render_frame_impl(scene, output_path),
                _TIMEOUT_RENDER,
                f"render_frame({width}x{height}@{samples}spp)"
            )
        except TimeoutError as e:
            _log(f"[TIMEOUT_ERROR] Render operation exceeded {_TIMEOUT_RENDER}s: {e}")
            return ""

        if render_ok:
            with open(output_path, "rb") as f:
                jpg_data = f.read()
            elapsed_ms = int((time.time() - start) * 1000)
            result_b64 = base64.b64encode(jpg_data).decode("ascii")
            denoiser = getattr(scene.cycles, "denoiser", "?")
            _log(f"{width}x{height} @{samples}spp+{denoiser} {elapsed_ms}ms {len(jpg_data)//1024}KB")
            return result_b64
        _log("Render produced no output")
        return ""
    except TimeoutError as e:
        _log(f"[TIMEOUT] Render timeout: {e}")
        return ""
    except Exception as e:
        _log(f"Render error: {e}")
        return ""
    finally:
        # ATOMIC: Clear _rendering under lock
        with _state_lock:
            _rendering = False
        # Guaranteed cleanup of temp directory
        if output_dir and os.path.exists(output_dir):
            try:
                shutil.rmtree(output_dir)
            except Exception as cleanup_err:
                _log(f"Could not clean {output_dir}: {cleanup_err}")


# ── HTTP handler (daemon thread — NO bpy.ops here) ───────────

class WorkerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        global _pending_path   # CRITICAL — without this, assignment is local-only

        try:
            length   = int(self.headers.get("Content-Length", 0))
            body     = self.rfile.read(length)
            data     = json.loads(body.decode("utf-8"))
            msg_type = data.get("type", "")
            response = {}

            if msg_type == "ping":
                # Read all state atomically under _state_lock
                with _state_lock:
                    loaded = _scene_loaded
                    loading = _scene_loading
                    rendering = _rendering
                    compute = _compute_type

                response = {
                    "type":          "pong",
                    "worker":        True,
                    "build":         "b36",
                    "scene_loaded":  loaded,
                    "scene_loading": loading,
                    "rendering":     rendering,
                    "compute":       compute,
                }
                # Detailed logging for scene load polling (helps server diagnose hangs)
                if loading or loaded:
                    _log(f"[PING] scene_loading={loading}, scene_loaded={loaded}, rendering={rendering}")

            elif msg_type == "load_scene_path":
                blend_path = data.get("path", "")
                _debug(f"[HTTP] load_scene_path request: {blend_path}")
                if not blend_path or not os.path.isfile(blend_path):
                    _debug(f"[HTTP] File check failed - exists: {os.path.isfile(blend_path) if blend_path else False}")
                    response = {"type": "error", "message": f"File not found: {blend_path}"}
                else:
                    _debug(f"[HTTP] File validated, acquiring lock...")
                    with _pending_lock:
                        _debug(f"[HTTP] Lock acquired, setting _pending_path")
                        _pending_path = blend_path
                    _debug(f"[HTTP] Lock released, pending_path is now set")
                    _log(f"[HTTP] Queued scene load: {blend_path}")
                    response = {"type": "scene_loading"}

            elif msg_type == "load_scene":
                # Legacy: base64 in body
                blend_b64 = data.get("blend_data", "")
                if not blend_b64:
                    response = {"type": "error", "message": "No blend_data"}
                else:
                    tmp_file = None
                    try:
                        tmp_file = tempfile.NamedTemporaryFile(suffix=".blend", delete=False)
                        tmp_file.write(base64.b64decode(blend_b64))
                        tmp_file.close()
                        with _pending_lock:
                            _pending_path = tmp_file.name
                        _log(f"Queued scene load (from b64): {tmp_file.name}")
                        response = {"type": "scene_loading"}
                    except Exception as e:
                        _log(f"Error writing temp scene file: {e}")
                        response = {"type": "error", "message": "Failed to load scene data"}
                    finally:
                        # Cleanup temp file if exception occurs before _pending_path is set
                        if tmp_file and tmp_file.name:
                            if tmp_file not in [_pending_path] and os.path.exists(tmp_file.name):
                                try:
                                    os.remove(tmp_file.name)
                                except Exception as cleanup_err:
                                    _log(f"Could not clean {tmp_file.name}: {cleanup_err}")

            elif msg_type == "update_camera":
                view_matrix = data.get("view_matrix")
                # ATOMIC: Check scene_loaded under lock
                with _state_lock:
                    scene_loaded = _scene_loaded
                if not scene_loaded:
                    response = {"type": "error", "message": "No scene loaded"}
                elif view_matrix:
                    flat = ([x for row in view_matrix for x in row]
                            if isinstance(view_matrix[0], list) else view_matrix)
                    with _render_lock:
                        set_camera_from_matrix(flat)
                    response = {"type": "camera_updated"}
                else:
                    response = {"type": "error", "message": "No view_matrix"}

            elif msg_type == "render_frame":
                # ATOMIC: Check scene_loaded under lock
                with _state_lock:
                    scene_loaded = _scene_loaded
                    compute = _compute_type
                if not scene_loaded:
                    response = {"type": "error", "message": "No scene loaded"}
                else:
                    width       = data.get("width", 640)
                    height      = data.get("height", 360)
                    samples     = data.get("samples", 1)
                    quality     = data.get("quality", 75)
                    view_matrix = data.get("view_matrix")
                    with _render_lock:
                        if view_matrix:
                            flat = ([x for row in view_matrix for x in row]
                                    if isinstance(view_matrix[0], list) else view_matrix)
                            set_camera_from_matrix(flat)
                        jpg_b64 = render_frame(width, height, samples, quality)
                    response = {
                        "type":    "frame_result",
                        "jpg_b64": jpg_b64,
                        "width":   width,
                        "height":  height,
                        "samples": samples,
                        "compute": compute,
                    }

            elif msg_type == "render_final":
                # ATOMIC: Check scene_loaded under lock
                with _state_lock:
                    scene_loaded = _scene_loaded
                if not scene_loaded:
                    response = {"type": "error", "message": "No scene loaded"}
                else:
                    width   = data.get("width", 1920)
                    height  = data.get("height", 1080)
                    samples = data.get("samples", 128)
                    output_dir = None
                    try:
                        with _render_lock:
                            scene = bpy.context.scene
                            scene.render.resolution_x         = width
                            scene.render.resolution_y         = height
                            scene.render.resolution_percentage = 100
                            scene.cycles.samples               = samples
                            _enable_denoiser(scene)
                            scene.render.image_settings.file_format = "PNG"
                            output_dir  = tempfile.mkdtemp(prefix="final_")
                            output_path = os.path.join(output_dir, "final.png")
                            scene.render.filepath = output_path
                            start = time.time()

                            # Run final render with timeout protection
                            try:
                                render_ok = run_with_timeout(
                                    lambda: _render_frame_impl(scene, output_path),
                                    _TIMEOUT_RENDER,
                                    f"render_final({width}x{height}@{samples}spp)"
                                )
                            except TimeoutError as e:
                                _log(f"[TIMEOUT_ERROR] Final render exceeded {_TIMEOUT_RENDER}s: {e}")
                                response = {"type": "error", "message": f"Render timeout: {e}"}
                                render_ok = False

                            if render_ok:
                                elapsed_ms = int((time.time() - start) * 1000)
                                if os.path.isfile(output_path):
                                    with open(output_path, "rb") as f:
                                        png_data = f.read()
                                    _log(f"Final {width}x{height}@{samples}spp {elapsed_ms}ms {len(png_data)//1024}KB")
                                    response = {
                                        "type":       "render_result",
                                        "png_b64":    base64.b64encode(png_data).decode("ascii"),
                                        "width":      width,
                                        "height":     height,
                                        "elapsed_ms": elapsed_ms,
                                    }
                                else:
                                    response = {"type": "error", "message": "No output produced"}
                            else:
                                response = {"type": "error", "message": "Render failed"}
                    except TimeoutError as e:
                        _log(f"[TIMEOUT] Final render timeout: {e}")
                        response = {"type": "error", "message": f"Render timeout: {e}"}
                    except Exception as e:
                        _log(f"Render final error: {e}")
                        response = {"type": "error", "message": str(e)}
                    finally:
                        # Guaranteed cleanup of temp directory
                        if output_dir and os.path.exists(output_dir):
                            try:
                                shutil.rmtree(output_dir)
                            except Exception as cleanup_err:
                                _log(f"Could not clean {output_dir}: {cleanup_err}")
            else:
                response = {"type": "error", "message": f"Unknown: {msg_type}"}

            resp_bytes = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)

        except Exception as e:
            _log(f"Handler error: {e}")
            err = json.dumps({"type": "error", "message": str(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def log_message(self, format, *args):
        pass


# ── Graceful Shutdown ─────────────────────────────────────────

def _cleanup_on_exit():
    """Cleanup resources on shutdown — called by atexit or KeyboardInterrupt."""
    _log("[SHUTDOWN] Starting graceful shutdown sequence...")

    # Clear retry queue to free memory
    try:
        with _retry_lock:
            _log(f"[SHUTDOWN] Clearing retry queue ({len(_load_retry_queue)} items)")
            _load_retry_queue.clear()
    except Exception as e:
        _log(f"[SHUTDOWN] Error clearing retry queue: {e}")

    # Close any open HTTP server
    global _http_server
    if '_http_server' in globals():
        try:
            _log("[SHUTDOWN] Closing HTTP server...")
            _http_server.shutdown()
            _log("[SHUTDOWN] HTTP server closed")
        except Exception as e:
            _log(f"[SHUTDOWN] HTTP server shutdown error: {e}")

    _log("[SHUTDOWN] Cleanup complete — worker exiting")


# ── Main ──────────────────────────────────────────────────────

_http_server = None  # Global reference for cleanup

def main():
    global _pending_path, _scene_loaded, _scene_loading, _http_server

    # Register cleanup handler for graceful shutdown on exit
    atexit.register(_cleanup_on_exit)

    setup_gpu()

    _log("=" * 55)
    _log(f"Blender Render Worker b36 on port {WORKER_PORT}")
    _log(f"Blender {bpy.app.version_string}")
    _log(f"Compute: {_compute_type}")
    _log("=" * 55)
    _debug(f"Starting with extreme debug logging enabled")

    _http_server = HTTPServer(("0.0.0.0", WORKER_PORT), WorkerHandler)
    http_thread = threading.Thread(target=_http_server.serve_forever, daemon=True)
    http_thread.start()
    _log(f"HTTP on port {WORKER_PORT} — ready")

    try:
        while True:
            path_to_load = None
            retry_info = None

            # Priority 1: Check retry queue for paths that need retry
            with _retry_lock:
                if _load_retry_queue:
                    queued_path, attempt, last_retry_time = _load_retry_queue[0]
                    if _should_retry_now(last_retry_time, attempt):
                        retry_info = _load_retry_queue.popleft()
                        path_to_load = queued_path
                        _log(f"[RETRY_{attempt + 1}/5] Retrying load: {queued_path} (backoff elapsed)")

            # Priority 2: Check for fresh pending path (HTTP handler queued)
            if not path_to_load:
                with _pending_lock:
                    path_to_load = _pending_path
                    _pending_path = None

            if path_to_load:
                is_retry = retry_info is not None
                attempt_num = retry_info[1] if retry_info else 0

                _log(f"[LOAD_START] Path: {path_to_load}" + (f" [RETRY_{attempt_num + 1}/5]" if is_retry else ""))
                _debug(f"File exists: {os.path.isfile(path_to_load)}, Size: {os.path.getsize(path_to_load) if os.path.isfile(path_to_load) else 'N/A'} bytes")

                with _state_lock:
                    # Set state ATOMICALLY: both flags together
                    _scene_loading = True
                    _scene_loaded  = False
                _log(f"[LOAD_STATE] scene_loading=True, scene_loaded=False")
                _debug(f"Lock acquired, state set")

                load_start_time = time.time()
                try:
                    _log(f"[LOAD_MAINFILE_START] Calling open_mainfile")
                    _debug(f"Context windows available: {len(bpy.context.window_manager.windows)}")
                    _debug(f"Current window: {bpy.context.window}")

                    # Override context to main window
                    target_window = bpy.context.window or (bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None)
                    _debug(f"Target window for override: {target_window}")

                    def _open_mainfile_impl():
                        with bpy.context.temp_override(window=target_window):
                            _debug(f"Context override entered, calling open_mainfile...")
                            bpy.ops.wm.open_mainfile(filepath=path_to_load)

                    try:
                        open_start = time.time()
                        run_with_timeout(
                            _open_mainfile_impl,
                            _TIMEOUT_LOAD_FILE,
                            f"open_mainfile({os.path.basename(path_to_load)})"
                        )
                        open_elapsed = time.time() - open_start
                        _log(f"[LOAD_MAINFILE_OK] File opened successfully ({open_elapsed:.2f}s)")
                    except TimeoutError as e:
                        _log(f"[TIMEOUT_ERROR] open_mainfile exceeded {_TIMEOUT_LOAD_FILE}s: {e}")
                        raise

                    _debug(f"Context override exited")

                    _log(f"[LOAD_GPU_SETUP_START] Re-initializing GPU")
                    gpu_start = time.time()
                    setup_gpu()
                    gpu_elapsed = time.time() - gpu_start
                    _log(f"[LOAD_GPU_SETUP_OK] Compute: {_compute_type} ({gpu_elapsed:.2f}s)")

                    _debug(f"Counting objects...")
                    obj_count = len(bpy.data.objects)
                    _debug(f"Object count: {obj_count}")
                    _log(f"[LOAD_OBJECTS] Loaded {obj_count} objects")

                    # Set scene_loaded ONLY after all steps succeed
                    with _state_lock:
                        _scene_loaded = True
                    load_elapsed = time.time() - load_start_time
                    _log(f"[LOAD_COMPLETE] Scene ready: {obj_count} objects, compute={_compute_type} (total: {load_elapsed:.2f}s)")
                    _debug(f"Final lock released")

                except Exception as e:
                    _log(f"[LOAD_ERROR] open_mainfile failed: {type(e).__name__}: {e}")
                    _debug(f"Full traceback:\n{traceback.format_exc()}")
                    with _state_lock:
                        _scene_loaded = False
                    _debug(f"Error state set")

                    # Push to retry queue if under max attempts
                    with _retry_lock:
                        next_attempt = attempt_num + 1
                        if next_attempt < 5:
                            _load_retry_queue.append((path_to_load, next_attempt, time.time()))
                            _log(f"[RETRY_QUEUED] Attempt {next_attempt}/5 scheduled for {_retry_backoff_seconds[next_attempt]:.1f}s")
                        else:
                            _log(f"[RETRY_EXHAUSTED] Failed after 5 attempts, giving up: {path_to_load}")

                finally:
                    with _state_lock:
                        _scene_loading = False
                    _log(f"[LOAD_DONE] scene_loading=False")
                    _debug(f"Finally block completed")

                # Restart HTTP if open_mainfile killed the thread
                if not http_thread.is_alive():
                    _log("[HTTP_RESTART] HTTP thread died — restarting")
                    try:
                        if _http_server:
                            _http_server.shutdown()
                    except Exception:
                        pass
                    _http_server = HTTPServer(("0.0.0.0", WORKER_PORT), WorkerHandler)
                    http_thread = threading.Thread(target=_http_server.serve_forever, daemon=True)
                    http_thread.start()
                    _log("[HTTP_RESTART_OK] HTTP restarted")

            # Pump Blender window events (non-blocking)
            try:
                for i in range(10):  # Process up to 10 pending events
                    bpy.ops.wm.redraw_timer_execute()
                _debug(f"[EVENT_PUMP] Processed 10 events")
            except Exception as e:
                _debug(f"[EVENT_PUMP] Error: {e}")

            # Get current state under lock for logging
            with _state_lock:
                current_loading = _scene_loading
                current_loaded = _scene_loaded
                current_rendering = _rendering

            if current_loading or current_loaded or current_rendering:
                _debug(f"[LOOP] scene_loading={current_loading}, scene_loaded={current_loaded}, rendering={current_rendering}")

            time.sleep(0.05)  # Reduced sleep for more responsive event pumping

    except KeyboardInterrupt:
        _log("[SHUTDOWN] Received KeyboardInterrupt (Ctrl+C)")
        _cleanup_on_exit()


if __name__ == "__main__":
    main()
