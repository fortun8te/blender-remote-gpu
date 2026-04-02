"""WebSocket connection manager for communicating with the render server.

Runs a background thread with an asyncio event loop for non-blocking I/O.
The Blender addon (main thread) communicates via thread-safe queues.
"""

import asyncio
import threading
import queue
import time
from typing import Callable
from collections import deque

import sys
import os

# Lazy imports — only load when actually connecting
websockets = None
ws_sync = None
msgpack = None

def _ensure_imports():
    """Load websockets and msgpack on first use (lazy loading for Blender addon compatibility)."""
    global websockets, ws_sync, msgpack
    if websockets is None:
        try:
            import websockets
            import websockets.sync.client as ws_sync
            import msgpack
        except ImportError as e:
            raise ImportError(
                f"Missing dependency: {e}. Install with: pip install websockets msgpack"
            )

# Add shared module to path — works whether addon is symlinked or copied
_addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_shared_dir = os.path.join(_addon_dir, "shared")
if os.path.exists(_shared_dir):
    # Shared is a sibling of addon/ (dev layout)
    sys.path.insert(0, _addon_dir)
else:
    try:
        import bpy
        _scripts_dir = os.path.dirname(bpy.context.preferences.filepaths.script_directory)
        if os.path.exists(os.path.join(_scripts_dir, "shared")):
            sys.path.insert(0, _scripts_dir)
    except Exception:
        pass

from shared.protocol import MsgType, pack_websocket, unpack_websocket
from shared.constants import (
    HEARTBEAT_INTERVAL, MAX_MESSAGE_SIZE, FRAME_BUFFER_SIZE_MS,
    FRAME_BUFFER_MAX_FRAMES, MAX_FRAMES_TO_DROP_PER_SECOND, FRAME_STALE_THRESHOLD_MS
)

# Must match server default (--api-key); server closes with AUTH_002 if no key in first 5s
DEFAULT_SERVER_API_KEY = "sk-render-dev-2026"


class RingBuffer:
    """Bounded ring buffer for frame data with FIFO eviction and metrics.

    Maintains a circular buffer of up to FRAME_BUFFER_MAX_FRAMES with
    automatic drop-oldest-on-overflow behavior to prevent memory leaks.
    Thread-safe using queue.Queue.
    """

    def __init__(self, max_frames: int = FRAME_BUFFER_MAX_FRAMES):
        self.max_frames = max_frames
        self._lock = threading.Lock()
        self._buffer: deque = deque(maxlen=max_frames)

        # Metrics
        self.frames_received = 0
        self.frames_dropped = 0
        self.buffer_size = 0
        self.latencies: deque = deque(maxlen=100)  # Keep last 100 latencies

    def put(self, frame: tuple, timestamp_sent: float | None = None) -> bool:
        """Add a frame to the buffer. Returns True if dropped due to overflow.

        Args:
            frame: (jpeg_bytes, metadata) tuple
            timestamp_sent: Time frame was sent from server (for latency calc)

        Returns:
            True if frame was stored successfully, False if oldest was evicted (overflow).
        """
        with self._lock:
            was_full = len(self._buffer) >= self.max_frames
            self._buffer.append((frame, time.time(), timestamp_sent))
            self.frames_received += 1
            self.buffer_size = len(self._buffer)

            if was_full:
                self.frames_dropped += 1

            # Record latency if timestamp available
            if timestamp_sent is not None:
                latency_ms = (time.time() - timestamp_sent) * 1000
                self.latencies.append(latency_ms)

            return not was_full

    def get(self) -> tuple | None:
        """Get the most recent frame without removing it."""
        with self._lock:
            if self._buffer:
                frame, time_received, timestamp_sent = self._buffer[-1]
                return frame
        return None

    def drain(self) -> list:
        """Drain all frames from buffer and return them (for testing/debugging)."""
        with self._lock:
            frames = list(self._buffer)
            self._buffer.clear()
            self.buffer_size = 0
            return frames

    def get_metrics(self) -> dict:
        """Return current buffer metrics."""
        with self._lock:
            avg_latency_ms = sum(self.latencies) / len(self.latencies) if self.latencies else 0
            return {
                "buffer_size": self.buffer_size,
                "frames_received": self.frames_received,
                "frames_dropped": self.frames_dropped,
                "avg_latency_ms": avg_latency_ms,
                "max_frames": self.max_frames,
            }


class Connection:
    """Thread-safe WebSocket connection to the render server with TLS + latency tracking.

    Usage:
        conn = Connection("wss://100.74.135.83:9876")  # TLS
        conn.connect()
        conn.send_scene(blend_bytes)
        frame = conn.get_frame()  # Non-blocking, returns None if no frame
        latency_ms = conn.get_current_latency()  # Real-time latency in ms
        conn.close()
    """

    def __init__(self, url: str, api_key: str | None = None, use_tls: bool = True):
        # Convert ws:// to wss:// if TLS enabled
        if use_tls and url.startswith("ws://"):
            url = "wss://" + url[5:]

        self.url = url
        self.api_key = api_key
        self.use_tls = use_tls
        self.connected = False
        self.server_status: dict = {}

        # Thread-safe queues
        self._send_queue: queue.Queue = queue.Queue()
        # Per-viewport frame queues (Phase 12 — Multi-Viewport)
        self._viewport_queues: dict = {}  # viewport_id -> RingBuffer
        self._viewport_queues_lock = threading.Lock()
        # Legacy global frame buffer for backwards compatibility
        self._frame_buffer = RingBuffer(max_frames=FRAME_BUFFER_MAX_FRAMES)
        self._event_queue: queue.Queue = queue.Queue()

        # Network resilience
        self._reconnect_attempts = 0
        self._reconnect_backoff_ms = 500
        self._max_reconnect_attempts = 10

        # Background thread
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ws = None
        self._last_drop_log_time = time.time()
        self._current_latency_ms = 0.0
        self.last_error: str | None = None

    def get_current_latency(self) -> float:
        """Get real-time latency in milliseconds."""
        return self._current_latency_ms

    def connect(self):
        """Connect to the server with automatic retry (blocks until connected or raises)."""
        # Issue #11: Check if old thread is still alive and stop it before connecting again
        if self._thread is not None and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=5.0)
            self._thread = None

        self.last_error = None
        self._stop_event.clear()
        self._reconnect_attempts = 0
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # Wait for TCP+TLS+auth (Tailscale / slow TLS can exceed 10s)
        deadline = time.time() + 45.0
        while not self.connected and time.time() < deadline:
            time.sleep(0.1)
            while True:
                try:
                    event = self._event_queue.get_nowait()
                    if event.get("error"):
                        raise ConnectionError(event["error"])
                except queue.Empty:
                    break

        if not self.connected:
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=5.0)
            raise ConnectionError(f"Timed out connecting to {self.url}")

    def close(self):
        """Disconnect and stop background thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self.connected = False

    def send(self, msg_type: MsgType, data: dict | None = None, binary: bytes | None = None):
        """Queue a message to send to the server (non-blocking)."""
        self._send_queue.put((msg_type, data or {}, binary))

    def send_scene(self, blend_data: bytes):
        """Send a full .blend file to the server."""
        self.send(MsgType.SCENE_FULL, {"size": len(blend_data)}, blend_data)

    def send_viewport_camera(self, view_matrix: list, proj_matrix: list, resolution: tuple, viewport_id: str | None = None):
        """Send viewport camera update (fast path for orbiting).

        Phase 12 — optionally specify viewport_id for multi-viewport rendering.
        """
        data = {
            "view_matrix": view_matrix,
            "proj_matrix": proj_matrix,
            "resolution": list(resolution),
        }
        if viewport_id:
            data["viewport_id"] = viewport_id
        self.send(MsgType.VIEWPORT_CAMERA, data)

    def send_viewport_start(self, view_matrix: list, proj_matrix: list, resolution: tuple, samples: int = 128,
                           current_frame: int | None = None, frame_start: int | None = None,
                           frame_end: int | None = None, frame_step: int | None = None,
                           fps: float | None = None, is_playing: bool | None = None,
                           viewport_id: str | None = None, zoom_level: float | None = None):
        """Request viewport render start.

        Optional timeline parameters for animation playback:
        - current_frame: Current frame number (default 1)
        - frame_start: Start frame of animation
        - frame_end: End frame of animation
        - frame_step: Frame step increment
        - fps: Playback FPS
        - is_playing: Whether timeline is playing

        Phase 12 — Multi-Viewport Parameters:
        - viewport_id: Unique viewport identifier
        - zoom_level: Zoom percentage (100 = 1x, 50 = 0.5x, 200 = 2x)
        """
        data = {
            "view_matrix": view_matrix,
            "proj_matrix": proj_matrix,
            "resolution": list(resolution),
            "samples": samples,
        }

        # Add optional timeline parameters
        if current_frame is not None:
            data["current_frame"] = current_frame
        if frame_start is not None:
            data["frame_start"] = frame_start
        if frame_end is not None:
            data["frame_end"] = frame_end
        if frame_step is not None:
            data["frame_step"] = frame_step
        if fps is not None:
            data["fps"] = fps
        if is_playing is not None:
            data["is_playing"] = is_playing

        # Phase 12 — Multi-viewport parameters
        if viewport_id is not None:
            data["viewport_id"] = viewport_id
        if zoom_level is not None:
            data["zoom_level"] = zoom_level

        self.send(MsgType.VIEWPORT_START, data)

    def send_viewport_stop(self):
        """Stop current viewport render."""
        self.send(MsgType.VIEWPORT_STOP)

    def send_timeline_update(self, frame: int, is_playing: bool):
        """Send timeline frame and playback state change.

        Called when user scrubs timeline or presses play/pause.
        """
        self.send(MsgType.TIMELINE_UPDATE, {
            "frame": frame,
            "is_playing": is_playing,
        })

    def send_render_settings(self, denoiser_type: str = "OFF", intensity: float = 0.8, auto_aov: bool = True):
        """Send render settings (denoiser config) before render starts."""
        self.send(MsgType.RENDER_SETTINGS, {
            "denoiser_type": denoiser_type,
            "intensity": intensity,
            "auto_aov_creation": auto_aov,
        })

    def send_render_start(self, resolution: tuple, samples: int, output_format: str = "EXR"):
        """Start a final render on the server."""
        self.send(MsgType.RENDER_START, {
            "resolution": list(resolution),
            "samples": samples,
            "format": output_format,
        })

    def send_render_cancel(self):
        """Cancel current final render."""
        self.send(MsgType.RENDER_CANCEL)

    def get_frame(self, viewport_id: str | None = None) -> tuple[bytes, dict] | None:
        """Get the latest viewport frame (non-blocking).

        Args:
            viewport_id: If provided, get frame for specific viewport.
                        If None, use global fallback buffer.

        Returns (jpeg_bytes, metadata) or None.
        Only returns the most recent frame in the buffer.
        Internally the ring buffer drops oldest frames on overflow.
        """
        if viewport_id is not None:
            with self._viewport_queues_lock:
                if viewport_id in self._viewport_queues:
                    return self._viewport_queues[viewport_id].get()
            return None
        # Fallback to global buffer
        return self._frame_buffer.get()

    def register_viewport_queue(self, viewport_id: str, max_frames: int = FRAME_BUFFER_MAX_FRAMES) -> RingBuffer:
        """Register a new per-viewport frame queue.

        Args:
            viewport_id: Unique viewport identifier
            max_frames: Maximum frames to buffer for this viewport

        Returns:
            RingBuffer object for this viewport
        """
        with self._viewport_queues_lock:
            if viewport_id not in self._viewport_queues:
                self._viewport_queues[viewport_id] = RingBuffer(max_frames=max_frames)
                import logging
                logger = logging.getLogger("connection")
                logger.info(f"Registered viewport queue: {viewport_id} (capacity={max_frames})")
            return self._viewport_queues[viewport_id]

    def unregister_viewport_queue(self, viewport_id: str):
        """Unregister a viewport frame queue."""
        with self._viewport_queues_lock:
            if viewport_id in self._viewport_queues:
                metrics = self._viewport_queues[viewport_id].get_metrics()
                self._viewport_queues.pop(viewport_id)
                import logging
                logger = logging.getLogger("connection")
                logger.info(f"Unregistered viewport queue: {viewport_id}, metrics={metrics}")

    def get_viewport_queue(self, viewport_id: str) -> RingBuffer | None:
        """Get the frame queue for a specific viewport."""
        with self._viewport_queues_lock:
            return self._viewport_queues.get(viewport_id)

    def get_final_frame(self, timeout: float = 300.0) -> tuple[bytes, dict] | None:
        """Wait for a final render frame (blocking with timeout)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                event = self._event_queue.get(timeout=0.5)
                if event.get("type") == "final_frame":
                    self.last_error = None
                    return event["data"], event["meta"]
                if event.get("type") == "progress":
                    pass
                if event.get("error"):
                    self.last_error = str(event["error"])
                    return None
            except queue.Empty:
                continue
        self.last_error = "Timed out waiting for final frame"
        return None

    # --- Background thread ---

    def _run_loop(self):
        """Background thread: manages async WebSocket I/O."""
        _ensure_imports()  # Load websockets and msgpack on first use
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_loop())
        except Exception as e:
            self._event_queue.put({"error": str(e)})
        finally:
            loop.close()
            self.connected = False

    async def _async_loop(self):
        """Async WebSocket loop with auto-reconnect on failure."""
        while not self._stop_event.is_set():
            try:
                # TLS context (disable cert verification for self-signed in dev)
                ssl_context = None
                if self.use_tls:
                    import ssl
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE

                async with websockets.connect(
                    self.url,
                    max_size=MAX_MESSAGE_SIZE,
                    ping_interval=HEARTBEAT_INTERVAL,
                    ping_timeout=HEARTBEAT_INTERVAL * 3,
                    open_timeout=30,
                    ssl=ssl_context,
                ) as ws:
                    self._ws = ws
                    # Server waits up to 5s for first binary frame with api_key (server.py AUTH)
                    key = self.api_key or DEFAULT_SERVER_API_KEY
                    auth_msgs = pack_websocket(MsgType.PING, {"api_key": key})
                    for part in auth_msgs:
                        await ws.send(part)
                    self.last_error = None
                    self.connected = True
                    self._reconnect_attempts = 0
                    self._event_queue.put({"connected": True})

                    # Run send and receive concurrently
                    await asyncio.gather(
                        self._send_loop(ws),
                        self._recv_loop(ws),
                    )
            except websockets.exceptions.ConnectionClosed:
                self.connected = False
                await self._async_attempt_reconnect()
            except Exception as e:
                self.connected = False
                self._event_queue.put({"error": str(e)})
                await self._async_attempt_reconnect()

    async def _async_attempt_reconnect(self):
        """Exponential backoff — must use asyncio.sleep (not time.sleep) in this loop."""
        if self._reconnect_attempts < self._max_reconnect_attempts:
            self._reconnect_attempts += 1
            backoff = self._reconnect_backoff_ms * (2 ** (self._reconnect_attempts - 1))
            backoff = min(backoff, 30000)
            import logging
            logger = logging.getLogger("connection")
            logger.warning(
                f"Connection lost. Reconnecting in {backoff}ms "
                f"(attempt {self._reconnect_attempts}/{self._max_reconnect_attempts})"
            )
            await asyncio.sleep(backoff / 1000.0)

    async def _send_loop(self, ws):
        """Send queued messages to server."""
        while not self._stop_event.is_set():
            try:
                msg_type, data, binary = self._send_queue.get_nowait()
                messages = pack_websocket(msg_type, data, binary)
                for msg in messages:
                    await ws.send(msg)
            except queue.Empty:
                await asyncio.sleep(0.01)  # 10ms poll

    async def _recv_loop(self, ws):
        """Receive messages from server and track latency."""
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                break

            if isinstance(raw, bytes):
                msg_type, data = unpack_websocket(raw)

                # Track latency from server timestamp
                if "timestamp_sent" in data:
                    latency = (time.time() - data["timestamp_sent"]) * 1000
                    self._current_latency_ms = latency

                # Handle binary follow-up if needed
                binary = None
                if data.get("has_binary"):
                    try:
                        binary = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue

                self._handle_message(msg_type, data, binary)

    def _handle_message(self, msg_type: MsgType, data: dict, binary: bytes | None):
        """Route incoming messages to appropriate queues."""
        if msg_type == MsgType.FRAME_VIEWPORT:
            # Viewport frame — route by viewport_id (Phase 12 — Multi-Viewport)
            meta = {k: v for k, v in data.items() if k not in ("type", "has_binary", "binary_len")}
            timestamp_sent = data.get("timestamp_sent")
            viewport_id = data.get("viewport_id")  # Server provides this for multi-viewport

            # Try viewport-specific queue first, then fallback to global
            dropped = False
            if viewport_id:
                with self._viewport_queues_lock:
                    if viewport_id in self._viewport_queues:
                        dropped = not self._viewport_queues[viewport_id].put(
                            (binary, meta), timestamp_sent=timestamp_sent
                        )
                    else:
                        # Queue not registered yet — use global buffer
                        dropped = not self._frame_buffer.put((binary, meta), timestamp_sent=timestamp_sent)
            else:
                # No viewport_id in frame — use global buffer (single viewport mode)
                dropped = not self._frame_buffer.put((binary, meta), timestamp_sent=timestamp_sent)

            # Rate-limited logging of buffer overflow
            if dropped:
                now = time.time()
                if now - self._last_drop_log_time >= (1.0 / MAX_FRAMES_TO_DROP_PER_SECOND):
                    import logging
                    logger = logging.getLogger("connection")
                    msg = f"Frame buffer overflow"
                    if viewport_id:
                        vp_queue = self.get_viewport_queue(viewport_id)
                        if vp_queue:
                            metrics = vp_queue.get_metrics()
                            msg += f" (viewport {viewport_id}): {metrics}"
                    else:
                        metrics = self._frame_buffer.get_metrics()
                        msg += f" (global): {metrics}"
                    logger.warning(msg)
                    self._last_drop_log_time = now

        elif msg_type == MsgType.FRAME_FINAL:
            meta = {k: v for k, v in data.items() if k not in ("type", "has_binary", "binary_len")}
            self._event_queue.put({"type": "final_frame", "data": binary, "meta": meta})

        elif msg_type == MsgType.FRAME_PROGRESS:
            self._event_queue.put({"type": "progress", "meta": data})

        elif msg_type == MsgType.STATUS:
            self.server_status = data

        elif msg_type == MsgType.SCENE_ACK:
            self._event_queue.put({"type": "scene_ack", "meta": data})

        elif msg_type == MsgType.ERROR:
            self._event_queue.put({"error": data.get("message", "Unknown server error")})
