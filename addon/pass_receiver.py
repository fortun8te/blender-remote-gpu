"""Client-side pass receiver — handles PASS_DATA and PASS_COMPLETE messages.

Stores passes in memory cache and triggers compositor updates.
Maintains UI feedback ("Passes arriving: 2/12").
"""

import logging
from typing import Callable, Optional, Dict, Any
from collections import deque
import time

from addon.compositor import LiveCompositor, PassArrivalEvent

logger = logging.getLogger("remote-gpu.addon.pass_receiver")


class PassReceiver:
    """Receives and buffers render passes from server.

    As each pass arrives:
    1. Store in _pass_cache
    2. Trigger compositor update
    3. Update UI status ("Passes arriving: 2/12")
    """

    def __init__(self, scene=None, on_status_changed: Callable = None):
        """Initialize pass receiver.

        Args:
            scene: Blender scene object
            on_status_changed: Callback for UI updates.
                              Called as: on_status_changed(status_dict)
        """
        self.scene = scene
        self.on_status_changed = on_status_changed

        # Pass cache
        self._pass_cache: Dict[str, bytes] = {}
        self._pass_metadata: Dict[str, Dict[str, Any]] = {}

        # Compositor integration
        self.compositor = LiveCompositor(scene) if scene else None

        # Statistics
        self._start_time: float = None
        self._arrival_times: Dict[str, float] = {}
        self._expected_passes: int = 0  # Can be set by PASS_COMPLETE message
        self._total_bandwidth: int = 0

        # UI status deque (last 10 events)
        self._events: deque = deque(maxlen=10)

    def on_pass_data(self, pass_name: str, channel_count: int, width: int, height: int,
                     data_format: str, data: bytes, tier: int = 0):
        """Handle incoming PASS_DATA message.

        Args:
            pass_name: Name of the pass (Beauty, Normal, Depth, etc.)
            channel_count: Number of channels (1, 3, 4)
            width: Image width
            height: Image height
            data_format: "RAW_FLOAT", "RAW_INT", "JPEG", "PNG"
            data: Binary pass data
            tier: PassTier value (1-4)
        """
        if self._start_time is None:
            self._start_time = time.time()

        arrival_time = time.time() - self._start_time

        # Store in cache
        self._pass_cache[pass_name] = data
        self._pass_metadata[pass_name] = {
            "channels": channel_count,
            "width": width,
            "height": height,
            "format": data_format,
            "tier": tier,
            "size_bytes": len(data),
            "arrival_time": arrival_time,
        }

        self._arrival_times[pass_name] = arrival_time
        self._total_bandwidth += len(data)

        logger.info(
            f"Pass received: {pass_name} at {arrival_time:.1f}s "
            f"({width}x{height}, {channel_count}ch, {len(data) / 1024 / 1024:.1f} MB)"
        )

        # Update compositor with new pass
        if self.compositor:
            event = PassArrivalEvent(pass_name, data, channel_count, width, height, data_format)
            event.timestamp = arrival_time
            self.compositor.on_pass_arrival(event)

        # Update UI
        self._update_ui_status()

        # Log event
        self._events.append({
            "type": "pass_arrival",
            "pass_name": pass_name,
            "time": arrival_time,
            "size_mb": len(data) / 1024 / 1024,
        })

    def on_pass_complete(self, total_passes: int, total_bandwidth_mb: float):
        """Handle PASS_COMPLETE message — all passes delivered.

        Args:
            total_passes: Total number of passes sent
            total_bandwidth_mb: Total bandwidth used (MB)
        """
        elapsed = time.time() - self._start_time if self._start_time else 0
        logger.info(
            f"Pass delivery complete: {total_passes} passes, "
            f"{total_bandwidth_mb:.1f} MB in {elapsed:.1f}s"
        )

        self._expected_passes = total_passes

        self._events.append({
            "type": "pass_complete",
            "total_passes": total_passes,
            "elapsed_seconds": elapsed,
        })

        self._update_ui_status(complete=True)

    def _update_ui_status(self, complete: bool = False):
        """Update UI with pass arrival status.

        Calls on_status_changed with dict containing:
        - passes_received: int
        - expected_passes: int (if known)
        - progress_percent: int
        - total_bandwidth_mb: float
        - recent_pass: str (name of last arrived pass)
        - compositor_updates: int
        """
        if not self.on_status_changed:
            return

        passes_received = len(self._pass_cache)
        progress_pct = 0
        if self._expected_passes > 0:
            progress_pct = int(100 * passes_received / self._expected_passes)

        status = {
            "passes_received": passes_received,
            "expected_passes": self._expected_passes,
            "progress_percent": progress_pct,
            "total_bandwidth_mb": self._total_bandwidth / 1024 / 1024,
            "complete": complete,
            "compositor_updates": self.compositor._eval_count if self.compositor else 0,
            "message": self._build_status_message(passes_received),
        }

        self.on_status_changed(status)

    def _build_status_message(self, passes_received: int) -> str:
        """Build human-readable status message."""
        if self._expected_passes > 0:
            return f"Passes arriving: {passes_received}/{self._expected_passes}"
        else:
            return f"Passes received: {passes_received}"

    def get_pass_status(self) -> Dict[str, Any]:
        """Get complete pass status for debugging."""
        return {
            "passes_received": len(self._pass_cache),
            "expected_passes": self._expected_passes,
            "total_bandwidth_mb": self._total_bandwidth / 1024 / 1024,
            "passes": {
                name: {
                    "size_mb": meta["size_bytes"] / 1024 / 1024,
                    "arrival_time_s": meta["arrival_time"],
                    "format": meta["format"],
                    "tier": meta["tier"],
                }
                for name, meta in self._pass_metadata.items()
            },
            "recent_events": list(self._events),
            "compositor_status": self.compositor.get_pass_status() if self.compositor else None,
        }

    def get_pass_data(self, pass_name: str) -> Optional[bytes]:
        """Retrieve cached pass data.

        Args:
            pass_name: Name of pass (e.g., "Normal", "Depth")

        Returns:
            Pass data bytes or None if not received
        """
        return self._pass_cache.get(pass_name)

    def clear_passes(self):
        """Clear all cached passes (before new render)."""
        self._pass_cache.clear()
        self._pass_metadata.clear()
        self._arrival_times.clear()
        self._total_bandwidth = 0
        self._start_time = None
        self._expected_passes = 0
        self._events.clear()

        if self.compositor:
            self.compositor.clear_passes()

        logger.info("Pass receiver cleared")
