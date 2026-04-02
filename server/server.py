"""Render server — WebSocket server that receives scenes and renders on GPU.

Run on the Windows machine with the RTX GPU:
    python server.py [--port 9876] [--blender /path/to/blender]
"""

import argparse
import asyncio
import logging
import os
import sys
import time
import signal
import uuid
from collections import deque

# Add parent dir to path for shared module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets
import msgpack

from shared.protocol import MsgType, pack_websocket, unpack_websocket
from shared.constants import (
    DEFAULT_PORT, MAX_MESSAGE_SIZE, HEARTBEAT_INTERVAL,
    FRAME_BUFFER_MAX_FRAMES, MAX_FRAMES_TO_DROP_PER_SECOND
)
from shared.logging_config import setup_logging, get_logger, LogContext, log_error_with_code, log_performance
from shared.error_codes import ErrorCodes
from shared.gpu_backend import GPUBackendManager
from server.scene_manager import SceneManager
from server.renderer import SubprocessRenderer, ViewportRenderer
from server.viewport_live import LiveViewportRenderer
from server.denoiser import DenoiserManager
from server.gpu_device_handler import create_device_handler

# Set up logging (can be overridden with --log-file)
setup_logging(
    name="remote-gpu.server",
    log_file=None,  # Set in main()
    log_level="INFO",
    use_json=False,
)
logger = get_logger("server")


class ClientFrameBuffer:
    """Per-client frame buffer with overflow tracking and status reporting.

    Tracks frames queued for a single client connection. If buffer exceeds
    FRAME_BUFFER_MAX_FRAMES, drops oldest frames (FIFO) and logs overflow.
    """

    def __init__(self, client_addr: str, max_frames: int = FRAME_BUFFER_MAX_FRAMES):
        self.client_addr = client_addr
        self.max_frames = max_frames
        self._buffer: deque = deque(maxlen=max_frames)
        self._lock = asyncio.Lock()

        # Metrics
        self.frames_queued = 0
        self.frames_dropped = 0
        self.last_drop_time = time.time()

    async def put(self, frame_data: bytes, metadata: dict) -> bool:
        """Queue a frame for this client. Returns True if dropped due to overflow.

        Args:
            frame_data: JPEG or image bytes
            metadata: Frame metadata dict

        Returns:
            True if added successfully, False if dropped
        """
        async with self._lock:
            was_full = len(self._buffer) >= self.max_frames
            self._buffer.append((frame_data, metadata, time.time()))
            self.frames_queued += 1

            if was_full:
                self.frames_dropped += 1
                return False

            return True

    async def get_metrics(self) -> dict:
        """Return current buffer metrics."""
        async with self._lock:
            return {
                "client_addr": self.client_addr,
                "buffer_size": len(self._buffer),
                "frames_queued": self.frames_queued,
                "frames_dropped": self.frames_dropped,
                "max_frames": self.max_frames,
            }

    def should_report_overflow(self) -> bool:
        """Check if overflow should be logged (rate-limited)."""
        now = time.time()
        elapsed = now - self.last_drop_time
        should_log = elapsed >= (1.0 / MAX_FRAMES_TO_DROP_PER_SECOND)
        if should_log:
            self.last_drop_time = now
        return should_log


class RenderServer:
    """WebSocket server that accepts connections from Blender clients."""

    def __init__(self, port: int = DEFAULT_PORT, blender_path: str = "blender"):
        self.port = port
        self.scene_manager = SceneManager()
        self.final_renderer = SubprocessRenderer(blender_path)
        self.viewport_renderer = ViewportRenderer(blender_path)
        self.live_viewport = LiveViewportRenderer()  # Fast live rendering
        self.denoiser_manager = DenoiserManager()
        self._viewport_task: asyncio.Task | None = None

        # GPU backend management (Agent 4 — GPU Backend Abstraction)
        try:
            self.gpu_backend_mgr = GPUBackendManager()
            logger.info(f"GPU Backend Manager initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize GPU backend manager: {e}")
            self.gpu_backend_mgr = None

        self.gpu_device_handler = create_device_handler(self.gpu_backend_mgr)

        # Per-session render settings (denoiser config)
        self._render_settings: dict = {
            "denoiser_type": "OFF",
            "intensity": 0.8,
            "auto_aov_creation": True,
        }

        # Per-session timeline state (Agent 3 — Animation Playback)
        self._timeline_state: dict = {
            "current_frame": 0,
            "is_playing": False,
        }

    async def handle_client(self, websocket):
        """Handle a single client connection."""
        client_addr = websocket.remote_address
        session_id = str(uuid.uuid4())[:8]

        # Per-client frame buffer (Phase 11 — Bounded Ring Buffer)
        frame_buffer = ClientFrameBuffer(str(client_addr), max_frames=FRAME_BUFFER_MAX_FRAMES)

        with LogContext("client_session", logger, session_id) as ctx:
            ctx.log_info(f"Client connected: {client_addr}")

            # API key validation (Issue #1)
            api_key_valid = False
            try:
                # Check headers first (WebSocket upgrade headers)
                headers = dict(websocket.request_headers) if hasattr(websocket, 'request_headers') else {}
                auth_header = headers.get('Authorization', '')
                if auth_header.startswith('Bearer '):
                    api_key_valid = auth_header[7:] == "sk-render-dev-2026"

                # If not in headers, check first message
                if not api_key_valid:
                    first_msg = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    if isinstance(first_msg, bytes):
                        msg_type, data = unpack_websocket(first_msg)
                        received_key = data.get("api_key", "")
                        api_key_valid = received_key == "sk-render-dev-2026"
                        if api_key_valid:
                            ctx.log_info("API key validated via first message")
                        else:
                            ctx.log_error("API key validation failed")
                            await self._send_error(websocket, "AUTH_001", "Invalid API key")
                            return
                    else:
                        # Non-first-msg, continue processing
                        await self._handle_message(websocket, msg_type, data, None, session_id, frame_buffer)
            except asyncio.TimeoutError:
                ctx.log_error("No message received within 5s timeout")
                await self._send_error(websocket, "AUTH_002", "Connection timeout")
                return
            except Exception as e:
                ctx.log_error(f"API key validation error: {e}")

            try:
                async for raw in websocket:
                    if isinstance(raw, bytes):
                        try:
                            msg_type, data = unpack_websocket(raw)
                            ctx.log_debug(f"Received {msg_type.name}")

                            # Read binary follow-up if indicated
                            binary = None
                            if data.get("has_binary"):
                                binary = await websocket.recv()
                                ctx.log_debug(f"Binary payload: {len(binary)} bytes")

                            await self._handle_message(websocket, msg_type, data, binary, session_id, frame_buffer)
                        except Exception as e:
                            ctx.log_error(f"Message handling error: {e}")
                            logger.exception("Message handling exception")
                            await self._send_error(websocket, "PROTO_002", "Invalid message format")

            except websockets.exceptions.ConnectionClosed:
                ctx.log_info(f"Client disconnected: {client_addr}")
            except Exception as e:
                ctx.log_error(f"Connection error: {e}")
                logger.exception("Connection exception")
            finally:
                # Cancel any running viewport render
                self._cancel_viewport()
                # Log final buffer metrics
                metrics = await frame_buffer.get_metrics()
                if metrics["frames_dropped"] > 0:
                    logger.info(f"Client {client_addr} session summary: {metrics}")
                ctx.log_info(f"Client session ended: {client_addr}")

    async def _handle_message(self, ws, msg_type: MsgType, data: dict, binary: bytes | None, session_id: str, frame_buffer: ClientFrameBuffer = None):
        """Route incoming messages to handlers."""

        if msg_type == MsgType.SCENE_FULL:
            await self._handle_scene_full(ws, data, binary, session_id)

        elif msg_type == MsgType.RENDER_SETTINGS:
            await self._handle_render_settings(ws, data, session_id)

        elif msg_type == MsgType.RENDER_START:
            await self._handle_render_start(ws, data, session_id)

        elif msg_type == MsgType.RENDER_CANCEL:
            logger.info(f"[{session_id}] Final render cancelled by client")
            self.final_renderer.cancel()

        elif msg_type == MsgType.RENDER_DEVICE_SELECT:
            await self._handle_device_select(ws, data, session_id)

        elif msg_type == MsgType.VIEWPORT_START:
            await self._handle_viewport_start(ws, data, session_id, frame_buffer)

        elif msg_type == MsgType.VIEWPORT_CAMERA:
            await self._handle_viewport_camera(ws, data, session_id, frame_buffer)

        elif msg_type == MsgType.TIMELINE_UPDATE:
            await self._handle_timeline_update(ws, data, session_id)

        elif msg_type == MsgType.VIEWPORT_STOP:
            logger.info(f"[{session_id}] Viewport stop requested")
            self._cancel_viewport()

        elif msg_type == MsgType.SCENE_DELTA:
            await self._handle_scene_delta(ws, data, session_id)

        elif msg_type == MsgType.PING:
            logger.debug(f"[{session_id}] Ping received")
            await self._send(ws, MsgType.PONG, self._get_status())

        else:
            logger.warning(f"[{session_id}] Unknown message type: {msg_type}")

    async def _handle_scene_full(self, ws, data: dict, binary: bytes | None, session_id: str):
        """Handle full .blend file upload."""
        operation_id = str(uuid.uuid4())[:12]

        with LogContext("scene_upload", logger, operation_id) as ctx:
            ctx.log_info(f"Scene upload: {len(binary) / 1024 / 1024:.1f} MB")

            if binary is None:
                ctx.log_error("No .blend data received")
                log_error_with_code(logger, operation_id, "SYNC_001", "No scene data in binary")
                await self._send_error(ws, "SYNC_001", "No .blend data received")
                return

            start = time.time()
            try:
                blend_path = self.scene_manager.receive_full_scene(binary)
                save_elapsed = time.time() - start
                ctx.log_info(f"Scene saved: {save_elapsed:.2f}s")
            except Exception as e:
                ctx.log_error(f"Scene save failed: {e}")
                log_error_with_code(logger, operation_id, "SYNC_001", str(e), e)
                await self._send_error(ws, "SYNC_001", f"Scene save failed: {e}")
                return

            # Load into bpy for live rendering
            load_start = time.time()
            try:
                self.live_viewport.setup_scene(blend_path)
                load_elapsed = time.time() - load_start
                ctx.log_info(f"Scene loaded: {load_elapsed:.2f}s")
            except Exception as e:
                ctx.log_error(f"Scene load failed: {e}")
                log_error_with_code(logger, operation_id, "RENDER_001", str(e), e)
                await self._send_error(ws, "RENDER_001", f"Scene load failed: {e}")
                return

            log_performance(logger, "scene_upload", (save_elapsed + load_elapsed) * 1000, True, {
                "size_mb": len(binary) / 1024 / 1024,
                "save_ms": save_elapsed * 1000,
                "load_ms": load_elapsed * 1000,
            })

            await self._send(ws, MsgType.SCENE_ACK, {
                "path": blend_path,
                "size": len(binary),
                "save_time": save_elapsed,
                "load_time": load_elapsed,
            })

    async def _handle_render_settings(self, ws, data: dict, session_id: str):
        """Handle render settings (denoiser config) before render starts."""
        denoiser_type = data.get("denoiser_type", "OFF").upper()
        intensity = float(data.get("intensity", 0.8))
        auto_aov = bool(data.get("auto_aov_creation", True))

        # Validate denoiser type
        if denoiser_type not in ("OFF", "OIDN", "OPTIX"):
            logger.warning(f"[{session_id}] Unknown denoiser type: {denoiser_type}, using OFF")
            denoiser_type = "OFF"

        # Validate OptiX availability
        if denoiser_type == "OPTIX" and not self.denoiser_manager.optix_available:
            logger.warning(f"[{session_id}] OptiX requested but not available, falling back to OIDN")
            denoiser_type = "OIDN"

        # Store for next render
        self._render_settings = {
            "denoiser_type": denoiser_type,
            "intensity": max(0.0, min(1.0, intensity)),  # Clamp to [0, 1]
            "auto_aov_creation": auto_aov,
        }

        logger.info(f"[{session_id}] Render settings updated: {denoiser_type} denoising (intensity={self._render_settings['intensity']})")

    async def _handle_render_start(self, ws, data: dict, session_id: str):
        """Handle final render request."""
        operation_id = str(uuid.uuid4())[:12]

        with LogContext("final_render", logger, operation_id) as ctx:
            blend_path = self.scene_manager.get_blend_path()
            if blend_path is None:
                ctx.log_error("No scene loaded")
                log_error_with_code(logger, operation_id, "SYNC_004", "Scene not loaded")
                await self._send_error(ws, "SYNC_004", "No scene loaded — send SCENE_FULL first")
                return

            resolution = tuple(data.get("resolution", [1920, 1080]))
            samples = data.get("samples", 128)
            output_format = data.get("format", "PNG")

            # Validate settings
            if resolution[0] < 1 or resolution[1] < 1:
                ctx.log_error(f"Invalid resolution: {resolution}")
                await self._send_error(ws, "RENDER_005", f"Invalid resolution: {resolution}")
                return

            if samples < 1:
                ctx.log_error(f"Invalid samples: {samples}")
                await self._send_error(ws, "RENDER_005", f"Invalid samples: {samples}")
                return

            ctx.log_info(f"Starting final render: {resolution[0]}x{resolution[1]}, {samples} samples, {output_format}")

            # Send progress
            await self._send(ws, MsgType.FRAME_PROGRESS, {
                "stage": "rendering",
                "samples_done": 0,
                "samples_total": samples,
            })

            # Render
            start = time.time()
            try:
                image_data = await self.final_renderer.render_final(
                    blend_path,
                    resolution,
                    samples,
                    output_format,
                    denoiser_type=self._render_settings["denoiser_type"],
                    denoiser_intensity=self._render_settings["intensity"],
                    auto_aov_creation=self._render_settings["auto_aov_creation"],
                )
                elapsed = time.time() - start

                # Log denoising result
                if self._render_settings["denoiser_type"] != "OFF":
                    self.denoiser_manager.log_denoising_result(
                        self._render_settings["denoiser_type"],
                        self._render_settings["intensity"],
                        elapsed * 1000,
                    )

                if image_data is None:
                    ctx.log_error("Render returned no data")
                    log_error_with_code(logger, operation_id, "RENDER_001", "Renderer returned None")
                    await self._send_error(ws, "RENDER_001", "Render failed")
                    return

                ctx.log_info(f"Render complete: {len(image_data) / 1024:.1f} KB in {elapsed:.2f}s")
                log_performance(logger, "final_render", elapsed * 1000, True, {
                    "resolution": f"{resolution[0]}x{resolution[1]}",
                    "samples": samples,
                    "output_size_kb": len(image_data) / 1024,
                })

                # Send result
                await self._send(ws, MsgType.FRAME_FINAL, {
                    "width": resolution[0],
                    "height": resolution[1],
                    "format": output_format,
                    "size": len(image_data),
                }, binary=image_data)

            except Exception as e:
                elapsed = time.time() - start
                ctx.log_error(f"Render failed: {e}")
                log_error_with_code(logger, operation_id, "RENDER_001", str(e), e)
                log_performance(logger, "final_render", elapsed * 1000, False)
                await self._send_error(ws, "RENDER_001", f"Render failed: {e}")

    async def _handle_viewport_start(self, ws, data: dict, session_id: str, frame_buffer: ClientFrameBuffer = None):
        """Handle viewport render request — starts progressive rendering."""
        operation_id = str(uuid.uuid4())[:12]
        logger.info(f"[{session_id}] Viewport start: op={operation_id}")

        blend_path = self.scene_manager.get_blend_path()
        if blend_path is None:
            logger.error(f"[{session_id}] No scene loaded for viewport")
            log_error_with_code(logger, operation_id, "SYNC_004", "Scene not loaded for viewport")
            await self._send_error(ws, "SYNC_004", "No scene loaded")
            return

        # Cancel any existing viewport render
        self._cancel_viewport()

        view_matrix = data.get("view_matrix")
        proj_matrix = data.get("proj_matrix")
        resolution = tuple(data.get("resolution", [800, 600]))
        max_samples = data.get("samples", 128)

        logger.info(f"[{session_id}] Viewport render: {resolution[0]}x{resolution[1]}, max {max_samples} samples")

        # Start progressive rendering in background
        self._viewport_task = asyncio.create_task(
            self._viewport_render_loop(ws, blend_path, view_matrix, proj_matrix, resolution, max_samples, operation_id, session_id, frame_buffer)
        )

    async def _handle_viewport_camera(self, ws, data: dict, session_id: str, frame_buffer: ClientFrameBuffer = None):
        """Handle fast camera-only update — restarts viewport render."""
        logger.debug(f"[{session_id}] Camera update (viewport restart)")
        await self._handle_viewport_start(ws, data, session_id, frame_buffer)

    async def _handle_scene_delta(self, ws, data: dict, session_id: str):
        """Handle incremental scene update."""
        operation_id = str(uuid.uuid4())[:12]
        logger.info(f"[{session_id}] Scene delta received: {list(data.keys())}")
        logger.debug(f"Delta keys: {list(data.keys())}")
        # For now, deltas require a full scene reload on the server
        # TODO: Apply deltas directly when using bpy module mode
        await self._send(ws, MsgType.SCENE_ACK, {"delta": True})

    async def _viewport_render_loop(
        self, ws, blend_path: str, view_matrix: list, proj_matrix: list,
        resolution: tuple, max_samples: int, operation_id: str, session_id: str,
        frame_buffer: ClientFrameBuffer = None
    ):
        """Progressive viewport rendering loop using live bpy rendering.

        Fast path: Blender stays loaded, we just render progressively.
        Latency: ~30-50ms per frame instead of ~2000ms with subprocess.

        Frames are queued to per-client frame buffer. If buffer is full,
        oldest frames are dropped (FIFO) and a BUFFER_FULL status is sent.
        """
        # Set camera and resolution
        self.live_viewport.set_camera_from_matrix(view_matrix, proj_matrix)
        self.live_viewport.set_resolution(resolution)

        # Progressive sample counts: fast first frame, then refine
        sample_steps = [1, 4, 16, 64, max_samples]
        sample_steps = [s for s in sample_steps if s <= max_samples]
        if sample_steps[-1] != max_samples:
            sample_steps.append(max_samples)

        for samples in sample_steps:
            if self.live_viewport.cancelled:
                logger.info("Viewport render cancelled")
                return

            try:
                start = time.time()
                jpeg_data = self.live_viewport.render_frame(samples=samples)
                elapsed = time.time() - start

                if jpeg_data is None:
                    break

                logger.info(f"Viewport frame: {samples} samples, {elapsed:.3f}s, {len(jpeg_data) / 1024:.0f} KB")

                # Add timestamp for latency tracking
                metadata = {
                    "width": resolution[0],
                    "height": resolution[1],
                    "samples": samples,
                    "render_time": elapsed,
                    "timestamp_sent": time.time(),  # For latency calc on client
                }

                # Track frame in per-client buffer
                if frame_buffer is not None:
                    dropped = not await frame_buffer.put(jpeg_data, metadata)
                    if dropped and frame_buffer.should_report_overflow():
                        metrics = await frame_buffer.get_metrics()
                        logger.warning(
                            f"[{session_id}] Frame drop: buffer overflow "
                            f"(dropped={metrics['frames_dropped']}, "
                            f"queued={metrics['frames_queued']}, "
                            f"capacity={metrics['max_frames']})"
                        )
                        # Send BUFFER_FULL status to client
                        await self._send(ws, MsgType.STATUS, {
                            "buffer_status": "BUFFER_FULL",
                            "frames_dropped": metrics["frames_dropped"],
                            "buffer_size": metrics["buffer_size"],
                        })

                await self._send(ws, MsgType.FRAME_VIEWPORT, metadata, binary=jpeg_data)

                # Allow other tasks to run
                await asyncio.sleep(0.01)

            except asyncio.CancelledError:
                logger.info("Viewport render cancelled")
                return
            except Exception as e:
                logger.error(f"Viewport render error: {e}", exc_info=True)
                break

    def _cancel_viewport(self):
        """Cancel any running viewport render."""
        if self._viewport_task is not None and not self._viewport_task.done():
            self._viewport_task.cancel()
            self.viewport_renderer.cancel()
            self._viewport_task = None

    async def _send(self, ws, msg_type: MsgType, data: dict = None, binary: bytes = None):
        """Send a message to the client."""
        messages = pack_websocket(msg_type, data, binary)
        for msg in messages:
            await ws.send(msg)

    async def _send_error(self, ws, error_code: str, message: str):
        """Send an error message to the client with error code."""
        error = ErrorCodes.by_code(error_code)
        if error:
            logger.error(f"Sending error {error_code}: {error.message}")
        else:
            logger.error(f"Sending error with unknown code {error_code}: {message}")

        await self._send(ws, MsgType.ERROR, {
            "error_code": error_code,
            "message": message,
            "user_message": error.user_message if error else message,
        })

    def _get_status(self) -> dict:
        """Get current server status."""
        status = {"version": "0.1.0"}

        # Try to get GPU info (requires pynvml) — Issue #2: Test GPU access
        try:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            status["gpu_name"] = pynvml.nvmlDeviceGetName(handle)
            status["vram_total"] = info.total // (1024 * 1024)
            status["vram_used"] = info.used // (1024 * 1024)
            status["vram_free"] = info.free // (1024 * 1024)
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            status["gpu_temp"] = temp
            status["gpu_access"] = True
            logger.info(f"GPU access OK: {status['gpu_name']} ({status['vram_free']}MB free)")
            pynvml.nvmlShutdown()
        except ImportError:
            status["gpu_name"] = "Unknown (install pynvml for GPU info)"
            status["gpu_access"] = False
            logger.warning("pynvml not installed — GPU monitoring disabled")
        except Exception as e:
            status["gpu_name"] = "Unknown"
            status["gpu_access"] = False
            logger.error(f"GPU access test failed: {e}")

        # Add GPU backend information (Agent 4 — GPU Backend Abstraction)
        if self.gpu_device_handler:
            gpu_status = self.gpu_device_handler.get_status_data()
            status.update(gpu_status)

        return status

    async def _handle_device_select(self, ws, data: dict, session_id: str):
        """Handle RENDER_DEVICE_SELECT message from client.

        Agent 4 — GPU Backend Abstraction
        Allows client to select which GPU backend (OptiX, CUDA, HIP, Vulkan) to use.
        """
        if self.gpu_device_handler:
            await self.gpu_device_handler.handle_device_select(ws, data, session_id)
        else:
            logger.warning(f"[{session_id}] Device select requested but handler not available")

    async def _handle_timeline_update(self, ws, data: dict, session_id: str):
        """Handle TIMELINE_UPDATE message from client.

        Agent 3 — Animation Playback
        Tracks timeline frame number and play/pause state for animation rendering.
        """
        current_frame = data.get("frame", 0)
        is_playing = data.get("is_playing", False)

        # Store timeline state for use during viewport rendering
        self._timeline_state = {
            "current_frame": current_frame,
            "is_playing": is_playing,
        }

        logger.debug(f"[{session_id}] Timeline update: frame={current_frame}, playing={is_playing}")

    async def start(self):
        """Start the WebSocket server."""
        logger.info(f"Starting render server on port {self.port}...")
        logger.info(f"Blender: {self.final_renderer.blender_path}")
        logger.info(f"Max message size: {MAX_MESSAGE_SIZE} bytes")
        logger.info(f"Heartbeat interval: {HEARTBEAT_INTERVAL}s")
        logger.info(f"Waiting for client connection...")

        # TLS/SSL support (dev: self-signed, prod: proper certs) — Issue #3: TLS fallback
        ssl_context = None
        ssl_keyfile = "/tmp/key.pem"
        ssl_certfile = "/tmp/cert.pem"
        if os.path.exists(ssl_keyfile) and os.path.exists(ssl_certfile):
            import ssl
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(ssl_certfile, ssl_keyfile)
            logger.info("TLS enabled (self-signed cert)")
        else:
            # Try to generate self-signed cert if openssl is available
            try:
                import subprocess
                import ssl as ssl_module
                logger.info("Generating self-signed TLS cert...")
                subprocess.run([
                    "openssl", "req", "-x509", "-newkey", "rsa:4096",
                    "-nodes", "-out", ssl_certfile, "-keyout", ssl_keyfile,
                    "-days", "365", "-subj", "/CN=localhost"
                ], check=True, capture_output=True)
                ssl_context = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_SERVER)
                ssl_context.load_cert_chain(ssl_certfile, ssl_keyfile)
                logger.info("TLS enabled (auto-generated self-signed cert)")
            except Exception as e:
                logger.warning(f"TLS cert generation failed ({e}). TLS disabled. To enable, run: openssl req -x509 -newkey rsa:4096 -nodes -out /tmp/cert.pem -keyout /tmp/key.pem -days 365")

        async with websockets.serve(
            self.handle_client,
            "0.0.0.0",  # Listen on all interfaces (Tailscale handles filtering)
            self.port,
            max_size=MAX_MESSAGE_SIZE,
            ping_interval=HEARTBEAT_INTERVAL,
            ping_timeout=HEARTBEAT_INTERVAL * 3,
            ssl=ssl_context,
        ):
            await asyncio.Future()  # Run forever


def main():
    parser = argparse.ArgumentParser(description="Remote GPU Render Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--blender", type=str, default="blender", help="Path to Blender executable")
    parser.add_argument("--log-file", type=str, default=None, help="Log file path (default: console only)")
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level")
    parser.add_argument("--json-logs", action="store_true", help="Use JSON log format")
    parser.add_argument("--use-tls", action="store_true", help="Enable TLS (requires cert at /tmp/cert.pem)")
    parser.add_argument("--api-key", type=str, default="sk-render-dev-2026", help="API key for authentication")
    args = parser.parse_args()

    # Reconfigure logging with command-line options
    if args.log_file or args.json_logs or args.log_level != "INFO":
        setup_logging(
            name="remote-gpu.server",
            log_file=args.log_file,
            log_level=args.log_level,
            use_json=args.json_logs,
        )

    logger.info("=== Remote GPU Render Server ===")
    logger.info(f"Port: {args.port}")
    logger.info(f"Blender: {args.blender}")
    logger.info(f"Log file: {args.log_file or 'console only'}")
    logger.info(f"Log format: {'JSON' if args.json_logs else 'text'}")

    try:
        server = RenderServer(port=args.port, blender_path=args.blender)
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
    except Exception as e:
        logger.critical(f"Server error: {e}", exc_info=True)


if __name__ == "__main__":
    main()
