#!/usr/bin/env python3
"""
End-to-End Render Cycle Simulation

Simulates a complete remote GPU rendering workflow including:
1. Client/Server connection handshake
2. Scene synchronization (.blend file transfer)
3. Viewport rendering with streaming frames
4. Pass streaming (beauty → passes → denoising)
5. Multi-viewport support
6. Animation timeline management
7. GPU backend selection
8. OptiX denoising integration

This test requires NO actual Blender or GPU hardware. It creates mock
objects and measures the full pipeline latency and bandwidth.

Run with:
    python3 e2e_simulation.py [--verbose] [--save-report]
"""

import os
import sys
import time
import json
import tempfile
import base64
import io
import struct
import threading
import queue
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any
from pathlib import Path
from collections import defaultdict
import hashlib

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# Define message types directly (Python 3.9 compatible)
class MsgType:
    HANDSHAKE = "HANDSHAKE"
    SCENE_SYNC = "SCENE_SYNC"
    VIEWPORT_START = "VIEWPORT_START"
    VIEWPORT_UPDATE = "VIEWPORT_UPDATE"
    VIEWPORT_FRAME = "VIEWPORT_FRAME"
    TIMELINE_UPDATE = "TIMELINE_UPDATE"
    RENDER_START = "RENDER_START"
    RENDER_COMPLETE = "RENDER_COMPLETE"
    PASS_START = "PASS_START"
    PASS_DATA = "PASS_DATA"
    STATUS = "STATUS"
    ERROR = "ERROR"

HEARTBEAT_INTERVAL = 30
MAX_MESSAGE_SIZE = 100_000_000  # 100 MB
FRAME_BUFFER_SIZE_MS = 1000
FRAME_BUFFER_MAX_FRAMES = 60


# ============================================================================
# MEASUREMENT & REPORTING
# ============================================================================

@dataclass
class PhaseMetrics:
    """Metrics for a single phase."""
    name: str
    status: str  # "pending", "in_progress", "complete", "failed", "skipped"
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration_ms: float = 0.0
    data_sent_bytes: int = 0
    data_received_bytes: int = 0
    frames_processed: int = 0
    errors: List[str] = None
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.details is None:
            self.details = {}

    def start(self):
        """Mark phase as started."""
        self.status = "in_progress"
        self.start_time = time.time()

    def complete(self):
        """Mark phase as complete."""
        self.status = "complete"
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000

    def fail(self, error_msg: str):
        """Mark phase as failed."""
        self.status = "failed"
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        self.errors.append(error_msg)

    def get_report(self) -> str:
        """Generate a report for this phase."""
        lines = [
            f"{'=' * 70}",
            f"Phase: {self.name}",
            f"Status: {self.status.upper()}",
            f"Duration: {self.duration_ms:.1f}ms",
            f"Data Sent: {self.data_sent_bytes:,} bytes",
            f"Data Received: {self.data_received_bytes:,} bytes",
        ]
        if self.frames_processed > 0:
            lines.append(f"Frames Processed: {self.frames_processed}")
        if self.details:
            lines.append("Details:")
            for key, value in self.details.items():
                if isinstance(value, float):
                    lines.append(f"  {key}: {value:.2f}")
                else:
                    lines.append(f"  {key}: {value}")
        if self.errors:
            lines.append("Errors:")
            for error in self.errors:
                lines.append(f"  - {error}")
        lines.append("")
        return "\n".join(lines)


class SimulationReport:
    """Aggregates metrics from all phases."""

    def __init__(self):
        self.phases: Dict[str, PhaseMetrics] = {}
        self.start_time = time.time()
        self.end_time = None
        self.latencies: List[float] = []  # Per-frame latencies
        self.bandwidth: Dict[str, float] = {}  # Phase -> avg bandwidth

    def create_phase(self, name: str) -> PhaseMetrics:
        """Create a new phase metric."""
        phase = PhaseMetrics(name=name, status="pending")
        self.phases[name] = phase
        return phase

    def record_latency(self, latency_ms: float):
        """Record a frame latency."""
        self.latencies.append(latency_ms)

    def finalize(self):
        """Finalize the report."""
        self.end_time = time.time()
        total_time = (self.end_time - self.start_time) * 1000

        # Calculate bandwidth per phase
        for name, phase in self.phases.items():
            if phase.duration_ms > 0:
                total_bytes = phase.data_sent_bytes + phase.data_received_bytes
                self.bandwidth[name] = (total_bytes / (phase.duration_ms / 1000)) / (1024 * 1024)

    def get_summary(self) -> str:
        """Generate summary report."""
        lines = [
            "=" * 70,
            "END-TO-END RENDER CYCLE SIMULATION REPORT",
            "=" * 70,
            f"Timestamp: {datetime.now().isoformat()}",
            "",
            "PHASE BREAKDOWN",
            "=" * 70,
        ]

        # Phase details
        for phase in self.phases.values():
            lines.append(phase.get_report())

        # Summary statistics
        lines.extend([
            "=" * 70,
            "SUMMARY STATISTICS",
            "=" * 70,
        ])

        total_time = (self.end_time - self.start_time) * 1000
        total_sent = sum(p.data_sent_bytes for p in self.phases.values())
        total_received = sum(p.data_received_bytes for p in self.phases.values())

        lines.append(f"Total Duration: {total_time:.1f}ms")
        lines.append(f"Total Data Sent: {total_sent:,} bytes ({total_sent / (1024*1024):.1f} MB)")
        lines.append(f"Total Data Received: {total_received:,} bytes ({total_received / (1024*1024):.1f} MB)")

        if self.latencies:
            avg_latency = sum(self.latencies) / len(self.latencies)
            min_latency = min(self.latencies)
            max_latency = max(self.latencies)
            lines.extend([
                "",
                "FRAME LATENCIES",
                "=" * 70,
                f"Frames Processed: {len(self.latencies)}",
                f"Average Latency: {avg_latency:.1f}ms",
                f"Min Latency: {min_latency:.1f}ms",
                f"Max Latency: {max_latency:.1f}ms",
            ])

        if self.bandwidth:
            lines.extend([
                "",
                "BANDWIDTH ANALYSIS",
                "=" * 70,
            ])
            for phase_name, bw in self.bandwidth.items():
                lines.append(f"{phase_name}: {bw:.1f} MB/s")

        lines.append("")
        lines.append("=" * 70)

        return "\n".join(lines)


# ============================================================================
# MOCK OBJECTS
# ============================================================================

class MockBlenderContext:
    """Mock Blender context with scene, region, and render data."""

    def __init__(self):
        self.scene = MockScene()
        self.region = MockRegion(1920, 1080)
        self.region_data = MockRegionData()


class MockScene:
    """Mock Blender scene."""

    def __init__(self):
        self.name = "TestScene"
        self.frame_current = 1
        self.frame_start = 1
        self.frame_end = 240
        self.render = MockRender()
        self.cycles = MockCyclesSettings()
        self.objects = [MockObject("Cube")]
        self.camera = MockCamera()


class MockRender:
    """Mock Blender render settings."""

    def __init__(self):
        self.resolution_x = 1920
        self.resolution_y = 1080
        self.resolution_percentage = 100
        self.film_transparent = False
        self.use_motion_blur = False
        self.motion_blur_samples = 16
        self.pixel_aspect_x = 1.0
        self.pixel_aspect_y = 1.0
        self.engine = "CYCLES"


class MockCyclesSettings:
    """Mock Cycles render settings."""

    def __init__(self):
        self.samples = 256
        self.use_denoising = True
        self.denoiser = "OPTIX"
        self.denoising_input_passes = "RGB_ALBEDO_NORMAL"


class MockRegion:
    """Mock viewport region."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height


class MockRegionData:
    """Mock viewport region data with matrices."""

    def __init__(self):
        # Simple identity matrices for testing
        self.view_matrix = [
            [1, 0, 0, 0],
            [0, 1, 0, 0],
            [0, 0, 1, -10],
            [0, 0, 0, 1],
        ]
        self.window_matrix = [
            [1.732, 0, 0, 0],
            [0, 1.732, 0, 0],
            [0, 0, -1.001, -0.2],
            [0, 0, -1, 0],
        ]


class MockObject:
    """Mock Blender object."""

    def __init__(self, name: str):
        self.name = name
        self.type = "MESH"


class MockCamera:
    """Mock Blender camera."""

    def __init__(self):
        self.name = "Camera"
        self.type = "CAMERA"


# ============================================================================
# MOCK EXR FILE GENERATOR
# ============================================================================

def create_mock_exr_file(width: int, height: int, passes: List[str]) -> bytes:
    """Create a minimal mock EXR file with passes.

    Real EXR is complex; this creates a valid binary structure
    with minimal headers for testing decompression.
    """
    # EXR magic number
    data = b'\x76\x2f\x31\x01'  # EXR v1.1

    # Minimal header (simplified for testing)
    header = b'channels\x00'

    for pass_name in passes:
        header += pass_name.encode() + b'\x00'

    # Frame buffer info (dummy)
    frame_info = f"{width}x{height}".encode()

    # Combine: magic + header + size info + dummy pixel data
    exr_bytes = data + header + frame_info + (b'\x00' * (width * height * 4))

    return exr_bytes


# ============================================================================
# MOCK JPEG GENERATOR
# ============================================================================

def create_mock_jpeg(width: int, height: int, seed: int = 0) -> bytes:
    """Create a minimal mock JPEG file.

    Real JPEG is complex; this creates a valid JPEG structure
    with minimal data for testing streaming and decompression.
    """
    # JPEG SOI marker
    data = b'\xff\xd8'

    # APP0 marker
    data += b'\xff\xe0'
    data += b'\x00\x10'  # Length
    data += b'JFIF\x00'
    data += b'\x01\x01'  # Version 1.1
    data += b'\x00'      # Units
    data += b'\x00\x01\x00\x01'  # X/Y density
    data += b'\x00\x00'  # Thumbnail

    # Start of frame marker (simplified)
    data += b'\xff\xc0'
    data += struct.pack('>H', 17)  # Length
    data += bytes([8])  # Precision
    data += struct.pack('>HH', height, width)  # Dimensions
    data += bytes([3])  # Components

    # Component specs
    for i in range(3):
        data += bytes([i+1, 0x11, i])  # ID, sampling, table

    # Dummy scan data (just repeat pattern)
    data += b'\xff\xda'
    data += b'\x00\x0c'  # Length
    data += bytes([3])   # Components
    data += b'\x01\x00\x02\x11\x03\x11\x00\x3f\x00'

    # Add some pseudo-random pixel data based on seed
    for i in range(width * height // 16):
        data += bytes([((seed + i) ^ (seed >> 8)) % 256 for _ in range(16)])

    # EOI marker
    data += b'\xff\xd9'

    return data


# ============================================================================
# MOCK SERVER (WebSocket simulation)
# ============================================================================

class MockRenderServer:
    """Simulates a remote render server handling WebSocket messages."""

    def __init__(self):
        self.clients: Dict[str, 'MockServerConnection'] = {}
        self.render_queue: queue.Queue = queue.Queue()
        self.viewport_states: Dict[str, Dict[str, Any]] = {}  # viewport_id -> state
        self.current_frame = 1
        self.available_backends = ["CPU", "CUDA", "OPTIX"]
        self.active_backend = "CUDA"
        self.render_passes = ["beauty", "normal", "depth", "albedo"]
        self.denoiser_type = "OPTIX"
        self.metrics = {
            "messages_received": 0,
            "messages_sent": 0,
            "total_bytes_sent": 0,
            "total_bytes_received": 0,
        }

    def handle_message(self, client_id: str, msg_type: str, payload: Dict) -> Dict:
        """Process an incoming message from client."""
        self.metrics["messages_received"] += 1

        if msg_type == MsgType.HANDSHAKE:
            return self._handle_handshake(client_id)
        elif msg_type == MsgType.SCENE_SYNC:
            return self._handle_scene_sync(payload)
        elif msg_type == MsgType.VIEWPORT_START:
            return self._handle_viewport_start(client_id, payload)
        elif msg_type == MsgType.VIEWPORT_UPDATE:
            return self._handle_viewport_update(payload)
        elif msg_type == MsgType.TIMELINE_UPDATE:
            return self._handle_timeline_update(payload)
        elif msg_type == "RENDER_DEVICE_SELECT":
            return self._handle_gpu_backend_select(payload)
        elif msg_type == "RENDER_SETTINGS":
            return self._handle_render_settings(payload)
        elif msg_type == MsgType.STATUS:
            return self._handle_status_request()
        else:
            return {"status": "error", "message": f"Unknown message type: {msg_type}"}

    def _handle_handshake(self, client_id: str) -> Dict:
        """Server responds to client handshake."""
        response = {
            "status": "connected",
            "server_version": "1.0.0",
            "client_id": client_id,
            "capabilities": {
                "viewport_rendering": True,
                "multi_viewport": True,
                "pass_streaming": True,
                "animation": True,
                "gpu_backends": self.available_backends,
                "denoising": ["OPTIX", "OIDN"],
            }
        }
        return response

    def _handle_scene_sync(self, payload: Dict) -> Dict:
        """Server receives scene data."""
        blend_data = payload.get("blend_data", b"")
        scene_hash = hashlib.md5(blend_data).hexdigest()[:8]

        # Simulate decompression time
        time.sleep(0.05)

        return {
            "status": "synced",
            "scene_hash": scene_hash,
            "objects_loaded": 5,
            "materials_loaded": 2,
        }

    def _handle_viewport_start(self, client_id: str, payload: Dict) -> Dict:
        """Server starts viewport rendering."""
        viewport_id = payload.get("viewport_id")
        width = payload.get("width", 1920)
        height = payload.get("height", 1080)

        self.viewport_states[viewport_id] = {
            "active": True,
            "width": width,
            "height": height,
            "frame_number": 1,
            "total_frames_rendered": 0,
        }

        # Queue first frame for rendering
        self.render_queue.put(("render_viewport_frame", viewport_id, 1, width, height))

        return {"status": "viewport_active", "viewport_id": viewport_id}

    def _handle_viewport_update(self, payload: Dict) -> Dict:
        """Server receives viewport camera update."""
        viewport_id = payload.get("viewport_id")
        if viewport_id in self.viewport_states:
            self.viewport_states[viewport_id].update({
                "view_matrix": payload.get("view_matrix"),
                "projection_matrix": payload.get("projection_matrix"),
            })
        return {"status": "updated"}

    def _handle_timeline_update(self, payload: Dict) -> Dict:
        """Server receives frame number update."""
        self.current_frame = payload.get("frame_number", 1)
        return {"status": "timeline_updated", "frame": self.current_frame}

    def _handle_gpu_backend_select(self, payload: Dict) -> Dict:
        """Server switches GPU backend."""
        backend = payload.get("backend", "CUDA")

        if backend not in self.available_backends:
            return {"status": "error", "message": f"Backend {backend} not available"}

        # Simulate backend switch time
        time.sleep(0.02)
        self.active_backend = backend

        return {"status": "backend_switched", "active_backend": backend}

    def _handle_render_settings(self, payload: Dict) -> Dict:
        """Server receives render settings."""
        self.denoiser_type = payload.get("denoiser_type", "OPTIX")
        denoiser_intensity = payload.get("denoiser_intensity", 0.5)

        # If OptiX not available, fallback
        if self.denoiser_type == "OPTIX":
            try:
                # Would normally check for CUDA/OptiX availability
                pass
            except:
                self.denoiser_type = "OIDN"
                return {
                    "status": "warning",
                    "message": "OptiX unavailable, using OIDN",
                    "denoiser": self.denoiser_type
                }

        return {"status": "settings_applied", "denoiser": self.denoiser_type}

    def _handle_status_request(self) -> Dict:
        """Server responds to status query."""
        return {
            "status": "ready",
            "active_viewports": len(self.viewport_states),
            "current_frame": self.current_frame,
            "available_backends": self.available_backends,
            "active_backend": self.active_backend,
            "denoiser": self.denoiser_type,
            "render_queue_size": self.render_queue.qsize(),
        }

    def render_viewport_frame(self, viewport_id: str, frame_num: int,
                             width: int, height: int) -> bytes:
        """Simulate rendering a viewport frame."""
        # Simulate render time (typically 10-50ms for viewport)
        time.sleep(0.01 + (width * height) / (1920 * 1080 * 50))

        # Create mock JPEG frame
        jpeg_data = create_mock_jpeg(width, height, seed=frame_num)

        if viewport_id in self.viewport_states:
            self.viewport_states[viewport_id]["total_frames_rendered"] += 1

        return jpeg_data

    def render_final_frame(self, width: int, height: int) -> Tuple[bytes, Dict]:
        """Simulate rendering a final frame (F12)."""
        # Simulate longer render time
        time.sleep(0.05 + (width * height) / (1920 * 1080 * 10))

        # Beauty pass
        jpeg_data = create_mock_jpeg(width, height, seed=999)

        # Passes (for denoising)
        passes = {
            "beauty": jpeg_data,
            "normal": create_mock_jpeg(width, height, seed=1001),
            "depth": create_mock_jpeg(width, height, seed=1002),
            "albedo": create_mock_jpeg(width, height, seed=1003),
        }

        return jpeg_data, passes


# ============================================================================
# SIMULATION ENGINE
# ============================================================================

class E2ESimulation:
    """Main end-to-end simulation coordinator."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.report = SimulationReport()
        self.server = MockRenderServer()
        self.client_id = "test-client-001"

    def log(self, msg: str, indent: int = 0):
        """Print log message if verbose."""
        if self.verbose:
            prefix = "  " * indent
            print(f"[E2E] {prefix}{msg}")

    def run(self) -> SimulationReport:
        """Execute the full simulation."""
        print("\n" + "=" * 70)
        print("STARTING END-TO-END RENDER CYCLE SIMULATION")
        print("=" * 70 + "\n")

        try:
            self._phase_1_client_setup()
            self._phase_2_scene_sync()
            self._phase_3_viewport_render()
            self._phase_4_frame_reception()
            self._phase_5_pass_streaming()
            self._phase_6_multi_viewport()
            self._phase_7_animation()
            self._phase_8_optix_denoising()
            self._phase_9_gpu_backend_selection()
        except Exception as e:
            print(f"\nFATAL ERROR: {e}")
            import traceback
            traceback.print_exc()
            return self.report

        self.report.finalize()
        return self.report

    # ========================================================================
    # PHASE 1: CLIENT SETUP
    # ========================================================================

    def _phase_1_client_setup(self):
        """Phase 1: Mock Blender addon loads and registers engine."""
        phase = self.report.create_phase("Phase 1: Client Setup")
        phase.start()

        try:
            self.log("Initializing mock Blender context...", 0)
            context = MockBlenderContext()
            phase.details["blender_version"] = "4.1"
            phase.details["addon_version"] = "1.0.0"

            self.log("Registering RemoteRenderEngine...", 1)
            phase.details["engine_registered"] = "REMOTE_CYCLES"

            self.log("Loading addon preferences...", 1)
            phase.details["server_ip"] = "127.0.0.1"
            phase.details["server_port"] = 8765

            self.log("Attempting handshake with mock server...", 1)
            handshake_response = self.server.handle_message(
                self.client_id,
                MsgType.HANDSHAKE,
                {}
            )

            if handshake_response.get("status") == "connected":
                phase.details["connection_status"] = "connected"
                phase.details["server_capabilities"] = str(
                    handshake_response.get("capabilities", {})
                )
                self.log(f"Connected to server v{handshake_response.get('server_version')}", 1)

            # Simulate handshake message size
            handshake_size = len(json.dumps(handshake_response).encode())
            phase.data_received_bytes = handshake_size
            phase.data_sent_bytes = 100  # Small handshake request

            time.sleep(0.05)  # Connection overhead
            phase.complete()

        except Exception as e:
            phase.fail(str(e))
            raise

    # ========================================================================
    # PHASE 2: SCENE SYNC
    # ========================================================================

    def _phase_2_scene_sync(self):
        """Phase 2: Synchronize Blender scene to server."""
        phase = self.report.create_phase("Phase 2: Scene Sync")
        phase.start()

        try:
            self.log("Creating mock Blender scene...", 0)
            context = MockBlenderContext()
            scene = context.scene

            self.log(f"Scene: {scene.name} ({len(scene.objects)} objects)", 1)
            phase.details["object_count"] = len(scene.objects)
            phase.details["camera_present"] = scene.camera.name

            # Simulate .blend file serialization
            self.log("Serializing scene to .blend format...", 1)
            blend_size = 50_000_000  # Mock ~50MB .blend file
            blend_data = os.urandom(blend_size)
            phase.data_sent_bytes = blend_size

            serialize_time = time.time()

            # Send SCENE_SYNC message
            self.log(f"Sending scene data ({blend_size / (1024*1024):.1f} MB)...", 1)
            sync_response = self.server.handle_message(
                self.client_id,
                MsgType.SCENE_SYNC,
                {"blend_data": blend_data}
            )

            serialize_time = (time.time() - serialize_time) * 1000
            phase.details["serialization_time_ms"] = serialize_time
            phase.details["scene_hash"] = sync_response.get("scene_hash")
            phase.details["objects_loaded"] = sync_response.get("objects_loaded")

            self.log(f"Scene synced in {serialize_time:.1f}ms", 1)
            self.log(f"Server loaded {sync_response.get('objects_loaded')} objects", 1)

            phase.data_received_bytes = len(json.dumps(sync_response).encode())
            phase.complete()

        except Exception as e:
            phase.fail(str(e))
            raise

    # ========================================================================
    # PHASE 3: VIEWPORT RENDER
    # ========================================================================

    def _phase_3_viewport_render(self):
        """Phase 3: Start viewport rendering with streaming frames."""
        phase = self.report.create_phase("Phase 3: Viewport Render")
        phase.start()

        try:
            self.log("Creating primary viewport (1920x1080)...", 0)
            viewport_id = "viewport-0"
            width, height = 1920, 1080

            # Simulate view_update() detection
            self.log("Detecting scene changes via depsgraph...", 1)

            # Extract view matrices
            self.log("Extracting view matrices...", 1)
            context = MockBlenderContext()
            view_matrices = context.region_data.view_matrix
            proj_matrices = context.region_data.window_matrix
            phase.details["view_matrix_extracted"] = True

            # Send VIEWPORT_START
            self.log(f"Sending VIEWPORT_START ({width}x{height})...", 1)
            start_time = time.time()

            viewport_response = self.server.handle_message(
                self.client_id,
                MsgType.VIEWPORT_START,
                {
                    "viewport_id": viewport_id,
                    "width": width,
                    "height": height,
                    "view_matrix": view_matrices,
                    "projection_matrix": proj_matrices,
                }
            )

            phase.data_sent_bytes += 1000  # Message overhead
            self.log(f"Viewport active: {viewport_response.get('viewport_id')}", 1)

            # Render and stream frames
            self.log("Streaming viewport frames...", 1)
            frame_count = 5
            latencies = []

            for frame_num in range(1, frame_count + 1):
                frame_start = time.time()

                # Server renders frame
                jpeg_data = self.server.render_viewport_frame(
                    viewport_id, frame_num, width, height
                )

                # Simulate frame transmission
                latency = (time.time() - frame_start) * 1000
                latencies.append(latency)
                self.report.record_latency(latency)

                phase.data_received_bytes += len(jpeg_data)
                phase.frames_processed += 1

                self.log(f"  Frame {frame_num}: {len(jpeg_data):,} bytes, {latency:.1f}ms", 2)

            phase.details["frames_streamed"] = frame_count
            phase.details["avg_latency_ms"] = sum(latencies) / len(latencies)
            phase.details["max_latency_ms"] = max(latencies)

            phase.complete()

        except Exception as e:
            phase.fail(str(e))
            raise

    # ========================================================================
    # PHASE 4: FRAME RECEPTION & DISPLAY
    # ========================================================================

    def _phase_4_frame_reception(self):
        """Phase 4: Client receives and displays frames."""
        phase = self.report.create_phase("Phase 4: Frame Reception & Display")
        phase.start()

        try:
            self.log("Initializing RingBuffer (frame cache)...", 0)
            ring_buffer = {
                "max_frames": 60,
                "frames": [],
                "dropped": 0,
            }
            phase.details["buffer_capacity"] = ring_buffer["max_frames"]

            self.log("Simulating frame decode and GPU upload...", 1)

            # Simulate receiving 10 frames
            total_decode_time = 0
            total_upload_time = 0

            for i in range(10):
                # Create frame
                jpeg_data = create_mock_jpeg(1920, 1080, seed=i)

                # Decode JPEG
                decode_start = time.time()
                # In real code: decode_jpeg(jpeg_data)
                pixels = b"mock_pixel_data" * (1920 * 1080 // 16)
                decode_time = (time.time() - decode_start) * 1000
                total_decode_time += decode_time

                # GPU texture upload
                upload_start = time.time()
                # In real code: gpu.texture.create() + bind
                time.sleep(0.001)  # Simulate upload
                upload_time = (time.time() - upload_start) * 1000
                total_upload_time += upload_time

                ring_buffer["frames"].append({
                    "frame_num": i,
                    "timestamp": time.time(),
                    "size_bytes": len(jpeg_data),
                })

                phase.data_received_bytes += len(jpeg_data)
                phase.frames_processed += 1

            phase.details["total_frames_received"] = len(ring_buffer["frames"])
            phase.details["avg_decode_time_ms"] = total_decode_time / 10
            phase.details["avg_upload_time_ms"] = total_upload_time / 10

            self.log(f"Decoded {len(ring_buffer['frames'])} frames", 1)
            self.log(f"Avg decode: {total_decode_time / 10:.2f}ms", 1)
            self.log(f"Avg GPU upload: {total_upload_time / 10:.2f}ms", 1)

            phase.complete()

        except Exception as e:
            phase.fail(str(e))
            raise

    # ========================================================================
    # PHASE 5: PASS STREAMING
    # ========================================================================

    def _phase_5_pass_streaming(self):
        """Phase 5: Stream render passes (beauty → normals → depth → albedo)."""
        phase = self.report.create_phase("Phase 5: Pass Streaming")
        phase.start()

        try:
            self.log("Initiating final render (F12)...", 0)
            width, height = 1920, 1080

            # Tier 1: Beauty JPEG (streamed first)
            self.log("Tier 1 - Streaming beauty pass...", 1)
            beauty_jpeg = create_mock_jpeg(width, height, seed=999)
            phase.data_received_bytes += len(beauty_jpeg)
            phase.details["tier_1_beauty_bytes"] = len(beauty_jpeg)

            # Tier 2: Normal + Depth passes
            self.log("Tier 2 - Streaming normal & depth passes...", 1)
            normal_pass = create_mock_jpeg(width, height, seed=1001)
            depth_pass = create_mock_jpeg(width, height, seed=1002)
            phase.data_received_bytes += len(normal_pass) + len(depth_pass)
            phase.details["tier_2_normal_bytes"] = len(normal_pass)
            phase.details["tier_2_depth_bytes"] = len(depth_pass)

            # Tier 3: Albedo + other AOVs
            self.log("Tier 3 - Streaming albedo & AOVs...", 1)
            albedo_pass = create_mock_jpeg(width, height, seed=1003)
            aov_passes = [create_mock_jpeg(width, height, seed=1000+i)
                         for i in range(2)]
            phase.data_received_bytes += len(albedo_pass) + sum(len(p) for p in aov_passes)
            phase.details["tier_3_albedo_bytes"] = len(albedo_pass)

            # Simulate pass caching
            phase.details["passes_cached"] = 4
            phase.details["total_pass_data_mb"] = phase.data_received_bytes / (1024 * 1024)

            self.log(f"All passes received ({phase.data_received_bytes / (1024*1024):.1f} MB)", 1)

            phase.frames_processed = 1
            phase.complete()

        except Exception as e:
            phase.fail(str(e))
            raise

    # ========================================================================
    # PHASE 6: MULTI-VIEWPORT
    # ========================================================================

    def _phase_6_multi_viewport(self):
        """Phase 6: Open 2 viewports and render simultaneously."""
        phase = self.report.create_phase("Phase 6: Multi-Viewport Rendering")
        phase.start()

        try:
            self.log("Opening viewport 1 (1920x1080)...", 0)
            viewport_1_id = "viewport-1"
            v1_response = self.server.handle_message(
                self.client_id,
                MsgType.VIEWPORT_START,
                {"viewport_id": viewport_1_id, "width": 1920, "height": 1080}
            )

            self.log("Opening viewport 2 (1280x720)...", 0)
            viewport_2_id = "viewport-2"
            v2_response = self.server.handle_message(
                self.client_id,
                MsgType.VIEWPORT_START,
                {"viewport_id": viewport_2_id, "width": 1280, "height": 720}
            )

            self.log("Rendering frames simultaneously...", 1)
            phase.details["viewport_1_resolution"] = "1920x1080"
            phase.details["viewport_2_resolution"] = "1280x720"

            # Render frame for viewport 1
            frame_1 = self.server.render_viewport_frame(viewport_1_id, 1, 1920, 1080)
            phase.data_received_bytes += len(frame_1)
            phase.frames_processed += 1

            # Render frame for viewport 2 (scaled)
            frame_2 = self.server.render_viewport_frame(viewport_2_id, 1, 960, 540)
            phase.data_received_bytes += len(frame_2)
            phase.frames_processed += 1

            phase.details["viewport_1_bytes"] = len(frame_1)
            phase.details["viewport_2_bytes"] = len(frame_2)
            phase.details["total_bytes"] = len(frame_1) + len(frame_2)

            self.log(f"Viewport 1: {len(frame_1):,} bytes", 1)
            self.log(f"Viewport 2: {len(frame_2):,} bytes", 1)

            phase.complete()

        except Exception as e:
            phase.fail(str(e))
            raise

    # ========================================================================
    # PHASE 7: ANIMATION
    # ========================================================================

    def _phase_7_animation(self):
        """Phase 7: Timeline updates and frame-by-frame rendering."""
        phase = self.report.create_phase("Phase 7: Animation Timeline")
        phase.start()

        try:
            self.log("Setting animation frame range (1-240)...", 0)
            phase.details["frame_start"] = 1
            phase.details["frame_end"] = 240
            phase.details["fps"] = 24

            self.log("Jumping to frame 10...", 1)
            timeline_response = self.server.handle_message(
                self.client_id,
                MsgType.TIMELINE_UPDATE,
                {"frame_number": 10}
            )

            # Render frame 10
            frame_10 = self.server.render_viewport_frame("viewport-0", 10, 1920, 1080)
            phase.data_received_bytes += len(frame_10)
            phase.frames_processed += 1

            phase.details["frame_rendered"] = 10
            phase.details["frame_size_bytes"] = len(frame_10)

            self.log(f"Rendered frame 10: {len(frame_10):,} bytes", 1)

            self.log("Rendering frame sequence 100-105...", 1)
            for frame_num in range(100, 106):
                frame_data = self.server.render_viewport_frame("viewport-0", frame_num, 1920, 1080)
                phase.data_received_bytes += len(frame_data)
                phase.frames_processed += 1

            phase.details["frame_sequence_count"] = 6

            phase.complete()

        except Exception as e:
            phase.fail(str(e))
            raise

    # ========================================================================
    # PHASE 8: OptiX DENOISING
    # ========================================================================

    def _phase_8_optix_denoising(self):
        """Phase 8: Test OptiX denoising (with OIDN fallback)."""
        phase = self.report.create_phase("Phase 8: OptiX Denoising")
        phase.start()

        try:
            self.log("Requesting OptiX denoising with intensity 0.8...", 0)

            denoise_response = self.server.handle_message(
                self.client_id,
                "RENDER_SETTINGS",
                {
                    "denoiser_type": "OPTIX",
                    "denoiser_intensity": 0.8
                }
            )

            phase.details["requested_denoiser"] = "OPTIX"
            phase.details["denoiser_intensity"] = 0.8
            phase.details["denoiser_actually_used"] = denoise_response.get("denoiser", "OPTIX")

            if "warning" in denoise_response:
                phase.details["fallback_reason"] = denoise_response.get("message")
                self.log(f"Warning: {denoise_response.get('message')}", 1)

            # Simulate denoising EXR output
            denoised_exr = create_mock_exr_file(1920, 1080, ["beauty", "normal"])
            phase.data_received_bytes += len(denoised_exr)

            phase.details["denoised_exr_bytes"] = len(denoised_exr)
            self.log(f"Denoised EXR: {len(denoised_exr):,} bytes", 1)

            phase.complete()

        except Exception as e:
            phase.fail(str(e))
            raise

    # ========================================================================
    # PHASE 9: GPU BACKEND SELECTION
    # ========================================================================

    def _phase_9_gpu_backend_selection(self):
        """Phase 9: Test GPU backend selection (CUDA, OptiX, CPU)."""
        phase = self.report.create_phase("Phase 9: GPU Backend Selection")
        phase.start()

        try:
            self.log("Querying available backends...", 0)
            status = self.server.handle_message(
                self.client_id,
                MsgType.STATUS,
                {}
            )

            available = status.get("available_backends", [])
            phase.details["available_backends"] = available
            self.log(f"Available backends: {', '.join(available)}", 1)

            # Try switching to CUDA
            self.log("Switching to CUDA backend...", 1)
            cuda_response = self.server.handle_message(
                self.client_id,
                "RENDER_DEVICE_SELECT",
                {"backend": "CUDA"}
            )

            if cuda_response.get("status") == "backend_switched":
                phase.details["cuda_switch_successful"] = True
                self.log("CUDA backend active", 2)

            # Try switching to CPU (should succeed)
            self.log("Switching to CPU backend...", 1)
            cpu_response = self.server.handle_message(
                self.client_id,
                "RENDER_DEVICE_SELECT",
                {"backend": "CPU"}
            )

            if cpu_response.get("status") == "backend_switched":
                phase.details["cpu_switch_successful"] = True
                self.log("CPU backend active", 2)

            # Try invalid backend
            self.log("Attempting invalid backend (should fail gracefully)...", 1)
            invalid_response = self.server.handle_message(
                self.client_id,
                "RENDER_DEVICE_SELECT",
                {"backend": "INVALID_GPU"}
            )

            if invalid_response.get("status") == "error":
                phase.details["invalid_backend_rejected"] = True
                self.log(f"Rejected: {invalid_response.get('message')}", 2)

            phase.complete()

        except Exception as e:
            phase.fail(str(e))
            raise


# ============================================================================
# MAIN
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="End-to-end render cycle simulation"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--save-report",
        action="store_true",
        help="Save report to file"
    )

    args = parser.parse_args()

    # Run simulation
    sim = E2ESimulation(verbose=args.verbose)
    report = sim.run()

    # Print report
    print(report.get_summary())

    # Save if requested
    if args.save_report:
        report_path = Path("/Users/mk/Downloads/blender-remote-gpu/e2e_simulation_report.txt")
        with open(report_path, "w") as f:
            f.write(report.get_summary())
        print(f"\nReport saved to: {report_path}")

    # Return success code
    success = all(p.status in ["complete", "skipped"] for p in report.phases.values())
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
