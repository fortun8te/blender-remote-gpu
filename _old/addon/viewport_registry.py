"""Viewport registry for managing multiple concurrent viewports.

Tracks viewport metadata (resolution, zoom level, region ID) and provides
centralized viewport lifecycle management.
"""

import hashlib
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger("viewport_registry")


@dataclass
class ViewportState:
    """Per-viewport state tracking."""
    viewport_id: str
    region_id: int
    resolution: tuple  # (width, height)
    zoom_level: float  # Blender zoom percentage
    priority: int  # 0 = primary (P0), 1+ = secondary (P1, P2, ...)
    last_frame_time: float = field(default_factory=time.time)
    last_camera_matrix: Optional[list] = None
    last_frame_number: Optional[int] = None
    last_is_playing: Optional[bool] = None
    created_at: float = field(default_factory=time.time)
    frame_count: int = 0
    frames_dropped: int = 0

    def update_frame_time(self):
        """Update last frame timestamp."""
        self.last_frame_time = time.time()

    def get_adjusted_resolution(self) -> tuple:
        """Get resolution adjusted for zoom level.

        Zoom < 50%: render at 50% resolution
        Zoom 50-200%: render at 100% resolution
        Zoom > 200%: render at 200% resolution (request upscale from server)
        """
        width, height = self.resolution

        if self.zoom_level < 50:
            return (max(1, int(width * 0.5)), max(1, int(height * 0.5)))
        elif self.zoom_level > 200:
            return (int(width * 2), int(height * 2))
        else:
            return (width, height)

    def to_dict(self) -> dict:
        """Serialize to dict for logging/debugging."""
        return {
            "viewport_id": self.viewport_id,
            "region_id": self.region_id,
            "resolution": self.resolution,
            "adjusted_resolution": self.get_adjusted_resolution(),
            "zoom_level": self.zoom_level,
            "priority": self.priority,
            "frame_count": self.frame_count,
            "frames_dropped": self.frames_dropped,
            "uptime_s": time.time() - self.created_at,
        }


class ViewportRegistry:
    """Centralized registry for managing multiple viewports."""

    def __init__(self):
        self._viewports: Dict[str, ViewportState] = {}
        self._lock = __import__("threading").Lock()
        self._next_priority = 0  # Auto-assign priorities

    def register_viewport(
        self,
        region,
        zoom_level: float = 100.0,
    ) -> ViewportState:
        """Register a new viewport or update existing one.

        Args:
            region: Blender region object
            zoom_level: Zoom percentage (100 = 1x, 50 = 0.5x, 200 = 2x)

        Returns:
            ViewportState object for this viewport
        """
        with self._lock:
            # Generate viewport ID from region space_data hash
            viewport_id = self._generate_viewport_id(region)

            if viewport_id in self._viewports:
                # Update existing viewport
                vp = self._viewports[viewport_id]
                vp.resolution = (region.width, region.height)
                vp.zoom_level = zoom_level
                vp.update_frame_time()
                logger.debug(
                    f"Updated viewport {viewport_id}: "
                    f"{vp.resolution[0]}x{vp.resolution[1]} @ {zoom_level}%"
                )
                return vp

            # Create new viewport
            priority = 0 if len(self._viewports) == 0 else self._next_priority + 1
            self._next_priority = priority

            vp = ViewportState(
                viewport_id=viewport_id,
                region_id=id(region),
                resolution=(region.width, region.height),
                zoom_level=zoom_level,
                priority=priority,
            )

            self._viewports[viewport_id] = vp
            logger.info(
                f"Registered viewport {viewport_id} (priority={priority}): "
                f"{vp.resolution[0]}x{vp.resolution[1]} @ {zoom_level}%"
            )
            return vp

    def unregister_viewport(self, viewport_id: str) -> bool:
        """Remove a viewport from registry.

        Args:
            viewport_id: ID of viewport to remove

        Returns:
            True if viewport was registered and removed
        """
        with self._lock:
            if viewport_id in self._viewports:
                vp = self._viewports.pop(viewport_id)
                logger.info(
                    f"Unregistered viewport {viewport_id} "
                    f"({vp.frame_count} frames, {vp.frames_dropped} dropped)"
                )

                # Reassign priorities: 0 for first, 1 for second, etc.
                self._reassign_priorities()
                return True
            return False

    def get_viewport(self, viewport_id: str) -> Optional[ViewportState]:
        """Get viewport state by ID."""
        with self._lock:
            return self._viewports.get(viewport_id)

    def get_all_viewports(self) -> list:
        """Get all active viewports (thread-safe copy)."""
        with self._lock:
            return list(self._viewports.values())

    def get_primary_viewport(self) -> Optional[ViewportState]:
        """Get the primary (P0 priority) viewport."""
        with self._lock:
            for vp in self._viewports.values():
                if vp.priority == 0:
                    return vp
            return None

    def get_viewport_count(self) -> int:
        """Return number of active viewports."""
        with self._lock:
            return len(self._viewports)

    def should_drop_frame(self, viewport_id: str) -> bool:
        """Check if this viewport should drop frames due to congestion.

        Primary viewport (P0) never drops.
        Secondary viewports drop if buffer is full.
        """
        vp = self.get_viewport(viewport_id)
        if vp is None:
            return True
        if vp.priority == 0:
            return False  # Primary never drops
        # Secondary viewports can drop (handled in frame buffer)
        return False

    def record_frame(self, viewport_id: str, dropped: bool = False):
        """Record frame delivery for a viewport."""
        vp = self.get_viewport(viewport_id)
        if vp is not None:
            with self._lock:
                vp.frame_count += 1
                if dropped:
                    vp.frames_dropped += 1
                vp.update_frame_time()

    def get_status(self) -> dict:
        """Get summary status of all viewports."""
        with self._lock:
            viewports_list = [vp.to_dict() for vp in self._viewports.values()]
            return {
                "total_viewports": len(self._viewports),
                "primary_viewport_id": next(
                    (vp.viewport_id for vp in self._viewports.values() if vp.priority == 0),
                    None,
                ),
                "viewports": viewports_list,
            }

    @staticmethod
    def _generate_viewport_id(region) -> str:
        """Generate stable viewport ID from region space_data.

        Uses hash of region memory address and space_data type.
        """
        # Use region ID combined with space data type
        data = f"{id(region)}:{region.space_data.__class__.__name__}"
        return hashlib.md5(data.encode()).hexdigest()[:12]

    def _reassign_priorities(self):
        """Reassign priorities after viewport removal."""
        sorted_vps = sorted(self._viewports.values(), key=lambda v: v.created_at)
        for idx, vp in enumerate(sorted_vps):
            vp.priority = idx
        if sorted_vps:
            logger.debug(f"Reassigned priorities: {[v.priority for v in sorted_vps]}")


# Global registry instance
_global_registry: Optional[ViewportRegistry] = None


def get_viewport_registry() -> ViewportRegistry:
    """Get or create global viewport registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = ViewportRegistry()
    return _global_registry
