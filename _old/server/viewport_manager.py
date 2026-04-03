"""Server-side viewport manager for multi-viewport rendering.

Tracks active viewports, assigns priorities, and manages resolution
scaling for secondary viewports to balance GPU load.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger("viewport_manager")


@dataclass
class ServerViewportState:
    """Server-side per-viewport state."""
    viewport_id: str
    resolution: tuple  # (width, height)
    zoom_level: float  # Client-provided zoom
    priority: int  # 0 = primary (P0), 1+ = secondary (P1, P2, ...)
    target_resolution: tuple  # Adjusted for priority
    created_at: float = field(default_factory=time.time)
    frames_sent: int = 0
    bytes_sent: int = 0

    def get_adjusted_resolution(self) -> tuple:
        """Get target resolution for this viewport based on priority.

        Primary (P0): Full resolution
        Secondary (P1, P2): 50% resolution (1920→960)
        Tertiary+ (P3, P4): 33% resolution (1920→640)
        """
        width, height = self.resolution

        if self.priority == 0:
            # Primary: full resolution
            return (width, height)
        elif self.priority <= 2:
            # Secondary: 50% resolution
            return (max(1, int(width * 0.5)), max(1, int(height * 0.5)))
        else:
            # Tertiary+: 33% resolution
            return (max(1, int(width * 0.33)), max(1, int(height * 0.33)))

    def update_resolution(self, new_resolution: tuple):
        """Update viewport resolution and recalculate target."""
        self.resolution = new_resolution
        self.target_resolution = self.get_adjusted_resolution()

    def to_dict(self) -> dict:
        """Serialize to dict for logging."""
        return {
            "viewport_id": self.viewport_id,
            "resolution": self.resolution,
            "target_resolution": self.target_resolution,
            "zoom_level": self.zoom_level,
            "priority": self.priority,
            "frames_sent": self.frames_sent,
            "bytes_sent_mb": self.bytes_sent / 1024 / 1024,
        }


class ViewportManager:
    """Manage multiple concurrent viewport renders on server."""

    def __init__(self):
        self._viewports: Dict[str, ServerViewportState] = {}
        self._viewport_order: list = []  # Ordered by priority
        self._lock = __import__("threading").Lock()
        self._fallback_mode = False

    def register_viewport(
        self,
        viewport_id: str,
        resolution: tuple,
        zoom_level: float = 100.0,
    ) -> ServerViewportState:
        """Register a new viewport or update existing.

        Args:
            viewport_id: Unique viewport ID from client
            resolution: Requested (width, height)
            zoom_level: Zoom percentage from client

        Returns:
            ServerViewportState with assigned priority
        """
        with self._lock:
            if viewport_id in self._viewports:
                # Update existing
                vp = self._viewports[viewport_id]
                vp.update_resolution(resolution)
                vp.zoom_level = zoom_level
                logger.debug(f"Updated viewport {viewport_id}: {resolution}")
                return vp

            # Create new viewport
            priority = len(self._viewports)

            vp = ServerViewportState(
                viewport_id=viewport_id,
                resolution=resolution,
                zoom_level=zoom_level,
                priority=priority,
            )
            vp.target_resolution = vp.get_adjusted_resolution()

            self._viewports[viewport_id] = vp
            self._viewport_order.append(viewport_id)

            logger.info(
                f"Registered viewport {viewport_id} (priority={priority}): "
                f"{resolution} -> {vp.target_resolution}"
            )

            # Check if we have too many viewports
            if len(self._viewports) > 4:
                logger.warning(
                    f"Too many viewports ({len(self._viewports)}). "
                    "Consider falling back to primary viewport only."
                )

            return vp

    def unregister_viewport(self, viewport_id: str) -> bool:
        """Remove a viewport from manager.

        Args:
            viewport_id: Viewport to remove

        Returns:
            True if removed, False if not found
        """
        with self._lock:
            if viewport_id in self._viewports:
                vp = self._viewports.pop(viewport_id)
                self._viewport_order.remove(viewport_id)

                logger.info(
                    f"Unregistered viewport {viewport_id}: "
                    f"{vp.frames_sent} frames, {vp.bytes_sent / 1024:.1f} KB"
                )

                # Reassign priorities if needed
                if self._viewport_order:
                    self._reassign_priorities()

                return True
            return False

    def get_viewport(self, viewport_id: str) -> Optional[ServerViewportState]:
        """Get viewport state by ID."""
        with self._lock:
            return self._viewports.get(viewport_id)

    def get_all_viewports(self) -> list:
        """Get all active viewports (ordered by priority)."""
        with self._lock:
            return [self._viewports[vid] for vid in self._viewport_order if vid in self._viewports]

    def get_primary_viewport_id(self) -> Optional[str]:
        """Get the primary (P0) viewport ID."""
        with self._lock:
            return self._viewport_order[0] if self._viewport_order else None

    def get_viewport_count(self) -> int:
        """Return number of active viewports."""
        with self._lock:
            return len(self._viewports)

    def should_fallback_to_primary(self) -> bool:
        """Check if server can't keep up with multiple viewports.

        Returns True if we should fallback to primary viewport only.
        """
        # Heuristic: if > 4 viewports or primary is dropping frames
        with self._lock:
            if len(self._viewports) > 4:
                return True

            # Check if primary is dropping frames (future: track per viewport)
            # For now, heuristic based on viewport count
            return False

    def record_frame_sent(self, viewport_id: str, frame_size_bytes: int):
        """Record frame sent for a viewport."""
        vp = self.get_viewport(viewport_id)
        if vp is not None:
            with self._lock:
                vp.frames_sent += 1
                vp.bytes_sent += frame_size_bytes

    def get_status(self) -> dict:
        """Get summary of all viewports."""
        with self._lock:
            return {
                "total_viewports": len(self._viewports),
                "fallback_mode": self._fallback_mode,
                "primary_viewport_id": self._viewport_order[0] if self._viewport_order else None,
                "viewports": [vp.to_dict() for vp in self._viewports.values()],
            }

    def _reassign_priorities(self):
        """Reassign priorities after viewport changes."""
        with self._lock:
            for idx, viewport_id in enumerate(self._viewport_order):
                if viewport_id in self._viewports:
                    old_priority = self._viewports[viewport_id].priority
                    self._viewports[viewport_id].priority = idx
                    if old_priority != idx:
                        logger.debug(
                            f"Reassigned {viewport_id} priority {old_priority} -> {idx}"
                        )


# Global manager instance
_global_viewport_manager: Optional[ViewportManager] = None


def get_viewport_manager() -> ViewportManager:
    """Get or create global viewport manager."""
    global _global_viewport_manager
    if _global_viewport_manager is None:
        _global_viewport_manager = ViewportManager()
    return _global_viewport_manager
