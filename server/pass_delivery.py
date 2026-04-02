"""Pass delivery handler — sends passes to client as they're ready.

Coordinates with pass_streamer to deliver passes over WebSocket.
Handles encoding, compression, and protocol wrapping.
"""

import asyncio
import logging
import struct
from typing import Callable, Optional
import io

try:
    from PIL import Image
    import numpy as np
except ImportError:
    Image = None
    np = None

from shared.protocol import MsgType, pack_websocket

logger = logging.getLogger("remote-gpu.pass_delivery")


class PassDeliveryManager:
    """Manages delivery of passes to WebSocket client."""

    def __init__(self, websocket, send_callback: Callable = None):
        """Initialize pass delivery.

        Args:
            websocket: WebSocket connection to client
            send_callback: Optional callback for testing/logging
                          Called as: await send_callback(msg_type, data, binary)
        """
        self.websocket = websocket
        self.send_callback = send_callback
        self._delivery_count = 0
        self._total_bandwidth = 0

    async def deliver_pass(self, pass_info) -> bool:
        """Send a single pass to client.

        Args:
            pass_info: PassInfo object with pass_name, channels, data, format

        Returns:
            True if delivery succeeded, False on error
        """
        if pass_info is None:
            # Signal end of passes
            return await self._send_pass_complete()

        try:
            data = {
                "pass_name": pass_info.name,
                "channel_count": pass_info.channels,
                "width": pass_info.width,
                "height": pass_info.height,
                "data_format": pass_info.format,
                "tier": pass_info.tier.value,
                "timestamp": pass_info.timestamp or 0,
            }

            # Send via protocol
            await self._send(MsgType.PASS_DATA, data, pass_info.data)

            self._delivery_count += 1
            self._total_bandwidth += len(pass_info.data)

            logger.info(
                f"Pass delivered: {pass_info.name} "
                f"({pass_info.width}x{pass_info.height}, {pass_info.channels}ch) "
                f"{len(pass_info.data) / 1024 / 1024:.1f} MB"
            )

            return True

        except Exception as e:
            logger.error(f"Pass delivery error: {e}")
            return False

    async def _send_pass_complete(self) -> bool:
        """Signal that all passes have been delivered."""
        try:
            data = {
                "total_passes": self._delivery_count,
                "total_bandwidth_mb": self._total_bandwidth / 1024 / 1024,
            }
            await self._send(MsgType.PASS_COMPLETE, data)
            logger.info(
                f"Pass delivery complete: {self._delivery_count} passes, "
                f"{self._total_bandwidth / 1024 / 1024:.1f} MB"
            )
            return True
        except Exception as e:
            logger.error(f"Could not send PASS_COMPLETE: {e}")
            return False

    async def _send(self, msg_type: MsgType, data: dict, binary: bytes = None) -> bool:
        """Send message via WebSocket."""
        try:
            if self.send_callback:
                await self.send_callback(msg_type, data, binary)
            else:
                # Direct WebSocket send
                messages = pack_websocket(msg_type, data, binary)
                for msg in messages:
                    await self.websocket.send(msg)

            return True
        except Exception as e:
            logger.error(f"WebSocket send error: {e}")
            return False

    def get_delivery_stats(self) -> dict:
        """Get delivery statistics."""
        return {
            "passes_delivered": self._delivery_count,
            "total_bandwidth_mb": self._total_bandwidth / 1024 / 1024,
            "avg_pass_size_mb": (self._total_bandwidth / max(1, self._delivery_count)) / 1024 / 1024,
        }


class PassEncoder:
    """Encodes render pass data to compact formats (JPEG, PNG, compressed binary)."""

    @staticmethod
    async def encode_beauty_to_jpeg(rgb_data: bytes, width: int, height: int, quality: int = 85) -> bytes:
        """Encode RGB pass data to JPEG for fast preview.

        Args:
            rgb_data: Raw RGB pixel data (width * height * 3 bytes)
            width: Image width in pixels
            height: Image height in pixels
            quality: JPEG quality (1-100)

        Returns:
            JPEG-encoded bytes
        """
        if Image is None or np is None:
            logger.warning("PIL/numpy not available, returning raw data")
            return rgb_data

        try:
            # Convert bytes to numpy array
            img_array = np.frombuffer(rgb_data, dtype=np.uint8).reshape((height, width, 3))

            # Create PIL image and encode
            img = Image.fromarray(img_array, mode="RGB")
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=quality, optimize=True)
            jpeg_data = output.getvalue()

            compression_ratio = len(rgb_data) / len(jpeg_data)
            logger.debug(
                f"Beauty encoded: {len(rgb_data) / 1024 / 1024:.1f} MB → "
                f"{len(jpeg_data) / 1024 / 1024:.1f} MB ({compression_ratio:.1f}x)"
            )

            return jpeg_data
        except Exception as e:
            logger.error(f"JPEG encoding failed: {e}")
            return rgb_data

    @staticmethod
    async def encode_float_to_png(float_data: bytes, width: int, height: int,
                                  channels: int = 1) -> bytes:
        """Encode float channel data (Normal, Depth, etc.) to PNG.

        Args:
            float_data: Raw float32 pixel data (width * height * channels * 4 bytes)
            width: Image width in pixels
            height: Image height in pixels
            channels: Number of channels (1, 3, or 4)

        Returns:
            PNG-encoded bytes
        """
        if Image is None or np is None:
            logger.warning("PIL/numpy not available, returning raw data")
            return float_data

        try:
            # Convert float32 to uint8 (scale to 0-255)
            float_array = np.frombuffer(float_data, dtype=np.float32).reshape((height, width, channels))
            uint8_array = (np.clip(float_array, 0.0, 1.0) * 255).astype(np.uint8)

            # Create PIL image
            if channels == 1:
                img = Image.fromarray(uint8_array[:, :, 0], mode="L")
            elif channels == 3:
                img = Image.fromarray(uint8_array, mode="RGB")
            elif channels == 4:
                img = Image.fromarray(uint8_array, mode="RGBA")
            else:
                logger.warning(f"Unsupported channel count: {channels}")
                return float_data

            # Encode to PNG
            output = io.BytesIO()
            img.save(output, format="PNG", compress_level=9)
            png_data = output.getvalue()

            compression_ratio = len(float_data) / len(png_data)
            logger.debug(
                f"Float pass encoded: {len(float_data) / 1024 / 1024:.1f} MB → "
                f"{len(png_data) / 1024 / 1024:.1f} MB ({compression_ratio:.1f}x)"
            )

            return png_data
        except Exception as e:
            logger.error(f"PNG encoding failed: {e}")
            return float_data

    @staticmethod
    async def encode_cryptomatte_metadata(crypto_json: dict) -> bytes:
        """Encode Cryptomatte metadata to compact JSON.

        Args:
            crypto_json: Cryptomatte metadata dict

        Returns:
            JSON bytes
        """
        try:
            import json
            json_bytes = json.dumps(crypto_json, separators=(",", ":")).encode("utf-8")
            logger.debug(f"Cryptomatte metadata: {len(json_bytes)} bytes")
            return json_bytes
        except Exception as e:
            logger.error(f"Cryptomatte encoding failed: {e}")
            return b"{}"
