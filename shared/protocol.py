"""Binary message protocol for client-server communication.

All messages are msgpack-encoded dicts with a 'type' field.
Binary payloads (frames, .blend files) are sent as raw bytes
following a header message.
"""

import struct
import time
from enum import IntEnum
from typing import Union, Optional, Tuple, List

try:
    import msgpack
except ImportError:
    msgpack = None

# Message sequence counter (Issue #5: message ordering)
_msg_sequence = 0


class MsgType(IntEnum):
    # Client -> Server
    SCENE_FULL = 0x01       # Full .blend file transfer (~100MB)
    SCENE_DELTA = 0x02      # Incremental update (~1KB): {
                             #   "modified_objects": [{
                             #     "name": str,
                             #     "type": str,
                             #     "changes": {
                             #       "transform": [16 floats],
                             #       "material_N": {...},
                             #       "light": {...},
                             #       "camera": {...},
                             #     }
                             #   }],
                             #   "deleted_objects": [str],
                             #   "new_objects": [str],
                             #   "full_resync_required": bool,
                             #   "bandwidth_saved_bytes": int,
                             # }
    RENDER_SETTINGS = 0x03  # Render denoiser & AOV config: {
                             #   "denoiser_type": "OFF" | "OIDN" | "OPTIX",
                             #   "intensity": 0.0-1.0,
                             #   "auto_aov_creation": bool,
                             # }
    RENDER_START = 0x10     # Start final render
    RENDER_CANCEL = 0x11    # Cancel current render
    RENDER_DEVICE_SELECT = 0x12  # Select GPU backend: {"backend": "OPTIX", "device_idx": 0}
    VIEWPORT_START = 0x20   # Start viewport render: {
                             #   "view_matrix": [16 floats],
                             #   "proj_matrix": [16 floats],
                             #   "resolution": [width, height],
                             #   "samples": int,
                             #   "current_frame": int (optional),
                             #   "frame_start": int (optional),
                             #   "frame_end": int (optional),
                             #   "frame_step": int (optional),
                             #   "fps": float (optional),
                             #   "is_playing": bool (optional),
                             # }
    VIEWPORT_STOP = 0x21    # Stop viewport render
    VIEWPORT_CAMERA = 0x22  # Camera-only update (fast path)
    TIMELINE_UPDATE = 0x23  # Timeline frame/play state change: {
                             #   "frame": int,
                             #   "is_playing": bool,
                             # }
    BAKE_START = 0x30       # Start bake job
    SIM_START = 0x40        # Start simulation
    PING = 0xF0             # Heartbeat

    # Server -> Client
    FRAME_VIEWPORT = 0x80   # JPEG viewport frame: {
                             #   "format": "JPEG" (Issue #6),
                             #   "width": int, "height": int,
                             #   "timestamp_sent": float,
                             # }
                             # Binary payload: JPEG data
    FRAME_FINAL = 0x81      # Multilayer EXR final render (beauty + all AOVs): {
                             #   "format": "EXR" (Issue #6),
                             #   "frame": int (frame number, for animations),
                             #   "frame_start": int,
                             #   "frame_end": int,
                             #   "total_frames": int,
                             #   "passes": [str] available render passes (Combined, Normal, Depth, etc.),
                             #   "width": int, "height": int,
                             #   "compression": "ZIP" (DWA for bandwidth efficiency),
                             # }
                             # Binary payload: multilayer EXR data with all AOVs preserved
    FRAME_PROGRESS = 0x82   # Render progress update
    VIEWPORT_PRIORITY = 0x83  # Phase 12 — Multi-viewport priority: {
                             #   "viewport_id": str,
                             #   "priority": int (0=primary, 1+=secondary),
                             #   "target_resolution": [w, h],
                             # }
    FALLBACK_MODE = 0x84    # Phase 12 — Server fallback: {
                             #   "primary_viewport_id": str,
                             #   "reason": str,
                             # }
    PASS_DATA = 0x24        # Streaming render pass data: {
                             #   "pass_name": str (Beauty, Normal, Depth, etc.),
                             #   "channel_count": int,
                             #   "width": int,
                             #   "height": int,
                             #   "data_format": str ("RAW_FLOAT", "RAW_INT", "JPEG", "PNG"),
                             #   "is_metadata": bool (optional, for Cryptomatte JSON),
                             # }
                             # binary payload contains pass data
    PASS_COMPLETE = 0x25    # All passes delivered: {
                             #   "total_passes": int,
                             #   "timestamps": {pass_name: float, ...},
                             # }
    STATUS = 0x90           # Server status: {
                             #   "gpu_name": str,
                             #   "gpu_temp": float,
                             #   "vram_used": int,
                             #   "vram_total": int,
                             #   "available_backends": ["OPTIX", "CUDA", ...],
                             #   "current_backend": "OPTIX",
                             #   "backend_devices": [{"name": str, "vram_gb": float, ...}],
                             # }
    SCENE_ACK = 0x91        # Scene received and loaded
    DELTA_ACK = 0x92        # Delta applied successfully
    PONG = 0xF1             # Heartbeat response
    ERROR = 0xFF            # Error


def pack(msg_type: MsgType, data: Optional[dict] = None, binary: Optional[bytes] = None) -> bytes:
    """Pack a message into wire format.

    Format: [1 byte type][4 bytes payload len][payload][optional binary]
    If binary is provided, payload includes a 'binary_len' field.
    """
    if data is None:
        data = {}
    data["type"] = int(msg_type)
    if binary is not None:
        data["binary_len"] = len(binary)

    if msgpack is None:
        raise RuntimeError("msgpack not installed")

    payload = msgpack.packb(data, use_bin_type=True)
    header = struct.pack("!BI", int(msg_type), len(payload))
    parts = [header, payload]
    if binary is not None:
        parts.append(binary)
    return b"".join(parts)


def unpack(raw: bytes) -> Tuple[MsgType, dict, Optional[bytes]]:
    """Unpack a wire-format message.

    Returns (msg_type, data_dict, optional_binary).
    """
    if len(raw) < 5:
        raise ValueError("Message too short")

    msg_type_int, payload_len = struct.unpack("!BI", raw[:5])
    msg_type = MsgType(msg_type_int)
    payload = raw[5:5 + payload_len]

    if msgpack is None:
        raise RuntimeError("msgpack not installed")

    data = msgpack.unpackb(payload, raw=False)
    binary = None
    binary_len = data.get("binary_len", 0)
    if binary_len > 0:
        binary_start = 5 + payload_len
        binary = raw[binary_start:binary_start + binary_len]
        if len(binary) != binary_len:
            raise ValueError(f"Expected {binary_len} bytes of binary, got {len(binary)}")

    return msg_type, data, binary


def pack_websocket(msg_type: MsgType, data: Optional[dict] = None, binary: Optional[bytes] = None) -> List:
    """Pack for WebSocket transport.

    Returns a list of messages to send:
    - First: msgpack header (binary WebSocket frame)
    - Second (optional): raw binary data (binary WebSocket frame)

    This avoids copying large binary blobs into msgpack.
    Issues #4 and #5: Add timestamp_sent and sequence number for latency tracking and ordering.
    """
    global _msg_sequence

    if data is None:
        data = {}
    data["type"] = int(msg_type)
    data["timestamp_sent"] = time.time()  # Issue #4: timestamp for FRAME_VIEWPORT latency
    data["seq"] = _msg_sequence  # Issue #5: sequence number for ordering
    _msg_sequence += 1

    if binary is not None:
        data["has_binary"] = True
        data["binary_len"] = len(binary)

    header = msgpack.packb(data, use_bin_type=True)
    messages = [header]
    if binary is not None:
        messages.append(binary)
    return messages


def unpack_websocket(header_bytes: bytes) -> Tuple[MsgType, dict]:
    """Unpack a WebSocket header message.

    Binary payload (if has_binary=True) arrives as the next WebSocket message.
    Issue #8: Add error handling for SSL/msgpack errors.
    """
    try:
        data = msgpack.unpackb(header_bytes, raw=False)
        msg_type = MsgType(data["type"])
        return msg_type, data
    except ValueError as e:
        raise ValueError(f"Invalid msgpack data: {e}")
    except KeyError:
        raise ValueError("Message missing 'type' field")
    except Exception as e:
        raise RuntimeError(f"Failed to unpack WebSocket message: {e}")
