"""Progressive multi-tier render pass streaming.

Delivers render passes in priority order:
  Tier 1 (0-2s):   Beauty pass as JPEG (instant preview)
  Tier 2 (2-10s):  Essential passes (Normal, Depth, Diffuse)
  Tier 3 (10-30s): Full passes (Glossy, Specular, Shadow, AO, Emission)
  Tier 4 (30-40s): Cryptomatte ID passes + metadata

Enables compositor to begin grading while remaining passes stream.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional
from enum import Enum
import struct
import io

try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = None
    Image = None

logger = logging.getLogger("remote-gpu.pass_streamer")


class PassTier(Enum):
    """Priority tiers for pass delivery."""
    BEAUTY_PREVIEW = 1      # 0-2s: Beauty as JPEG for viewport
    ESSENTIAL = 2           # 2-10s: Normal, Depth, Diffuse
    FULL_PASSES = 3         # 10-30s: Glossy, Specular, Shadow, AO, Emission
    CRYPTOMATTE = 4         # 30-40s: ID passes + metadata


@dataclass
class PassInfo:
    """Metadata for a single render pass."""
    name: str
    channels: int           # 1, 3, or 4
    width: int
    height: int
    format: str            # "RAW_FLOAT", "RAW_INT", "JPEG", "PNG"
    tier: PassTier
    data: bytes            # Binary pass data
    timestamp: float = None  # When pass was generated


class PassStreamer:
    """Manages progressive delivery of render passes to client.

    After render completes:
    1. Immediately encode & send Beauty as JPEG (tier 1)
    2. Start async thread for tier 2 (essential) passes
    3. Queue tier 3 & 4 passes at lower priority
    4. Client compositor re-evaluates as each pass arrives
    """

    # Pass priority & delivery windows (seconds from render completion)
    PASS_SCHEDULE = {
        # Tier 1: Beauty preview (0-2s)
        "Beauty": (PassTier.BEAUTY_PREVIEW, 0, 2),

        # Tier 2: Essential for basic compositing (2-10s)
        "Normal": (PassTier.ESSENTIAL, 2, 8),
        "Depth": (PassTier.ESSENTIAL, 2, 8),
        "Diffuse Color": (PassTier.ESSENTIAL, 2, 10),

        # Tier 3: Full material breakdown (10-30s)
        "Glossy": (PassTier.FULL_PASSES, 10, 20),
        "Specular": (PassTier.FULL_PASSES, 10, 25),
        "Shadow": (PassTier.FULL_PASSES, 15, 30),
        "AO": (PassTier.FULL_PASSES, 15, 30),
        "Emission": (PassTier.FULL_PASSES, 20, 30),

        # Tier 4: Advanced keying (30-40s)
        "Cryptomatte": (PassTier.CRYPTOMATTE, 30, 40),
    }

    def __init__(self, on_pass_ready: Callable[[PassInfo], None] = None):
        """Initialize streamer.

        Args:
            on_pass_ready: Callback when each pass is ready for delivery.
                          Called as on_pass_ready(PassInfo)
        """
        self.on_pass_ready = on_pass_ready
        self._passes: dict[str, PassInfo] = {}
        self._delivery_times: dict[str, float] = {}
        self._render_start_time: float = None
        self._streaming_task: asyncio.Task = None

    def start_render(self):
        """Mark render start time for scheduler."""
        self._render_start_time = time.time()
        self._delivery_times.clear()

    async def stream_passes_async(self, exr_path: str, encoder_callback: Callable = None):
        """Stream passes from EXR file asynchronously.

        Args:
            exr_path: Path to rendered EXR file with all passes
            encoder_callback: Optional async callable to encode passes.
                             Called as: data = await encoder_callback(pass_data, format)
        """
        if self._render_start_time is None:
            logger.warning("start_render() not called before streaming")
            self._render_start_time = time.time()

        try:
            # Parse EXR passes (placeholder — actual EXR parsing depends on OpenEXR library)
            passes = await self._extract_passes_from_exr(exr_path)

            # Sort by tier and schedule time window
            sorted_passes = self._sort_passes_by_tier(passes)

            # Stream each pass asynchronously, respecting schedule windows
            for pass_info in sorted_passes:
                await self._deliver_pass_on_schedule(pass_info, encoder_callback)

            # Signal completion
            if self.on_pass_ready:
                self.on_pass_ready(None)  # None signals end-of-passes

        except Exception as e:
            logger.error(f"Pass streaming error: {e}")
            if self.on_pass_ready:
                self.on_pass_ready(None)

    def _sort_passes_by_tier(self, passes: list[PassInfo]) -> list[PassInfo]:
        """Sort passes by tier, then by schedule window."""
        def sort_key(p: PassInfo):
            schedule = self.PASS_SCHEDULE.get(p.name, (PassTier.FULL_PASSES, 999, 999))
            tier_num = schedule[0].value
            window_start = schedule[1]
            return (tier_num, window_start)

        return sorted(passes, key=sort_key)

    async def _deliver_pass_on_schedule(self, pass_info: PassInfo, encoder_callback: Callable = None):
        """Wait until scheduled delivery time, then callback with pass data."""
        if self._render_start_time is None:
            return

        # Get schedule window
        schedule = self.PASS_SCHEDULE.get(pass_info.name, (PassTier.FULL_PASSES, 999, 999))
        _, window_start, window_end = schedule

        # Calculate wait time
        elapsed = time.time() - self._render_start_time
        wait_time = max(0, window_start - elapsed)

        logger.debug(f"Pass {pass_info.name} scheduled: delivery in {wait_time:.1f}s (tier {pass_info.tier.name})")

        # Wait for scheduled delivery
        if wait_time > 0:
            await asyncio.sleep(wait_time)

        # Apply optional encoding (e.g., compress to PNG)
        if encoder_callback and pass_info.format == "RAW_FLOAT":
            try:
                pass_info.data = await encoder_callback(pass_info.data, "PNG")
                pass_info.format = "PNG"
            except Exception as e:
                logger.warning(f"Could not encode {pass_info.name}: {e}, using raw")

        # Record delivery time and callback
        self._delivery_times[pass_info.name] = time.time() - self._render_start_time
        logger.info(f"Pass {pass_info.name} delivered at {self._delivery_times[pass_info.name]:.1f}s (tier {pass_info.tier.name})")

        if self.on_pass_ready:
            self.on_pass_ready(pass_info)

    async def _extract_passes_from_exr(self, exr_path: str) -> list[PassInfo]:
        """Extract all passes from EXR file.

        This is a placeholder implementation. Real implementation would:
        1. Read OpenEXR file headers
        2. Extract all layer/channel information
        3. Parse Cryptomatte JSON metadata
        4. Return PassInfo for each layer with proper format detection

        For now, returns empty list — will be implemented with OpenEXR library.
        """
        # TODO: Implement OpenEXR parsing
        # Would use:
        #  - imageio or OpenEXR library
        #  - Parse headers to determine channel count, data type
        #  - Detect Cryptomatte layers from metadata
        #  - Build PassInfo list with proper channels/format

        logger.debug(f"Placeholder: Would extract passes from {exr_path}")
        return []

    def get_delivery_summary(self) -> dict:
        """Get summary of pass delivery timing."""
        return {
            "total_passes": len(self._delivery_times),
            "delivery_times": self._delivery_times,
            "render_to_last_pass": max(self._delivery_times.values()) if self._delivery_times else 0,
        }
