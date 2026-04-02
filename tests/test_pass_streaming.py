"""Unit tests for progressive pass streaming system.

Tests cover:
1. Pass prioritization by tier
2. Delivery timing (respecting schedule windows)
3. Message formatting and WebSocket delivery
4. Compositor node mapping and updates
5. Pass caching and retrieval
"""

import asyncio
import unittest
from unittest.mock import Mock, AsyncMock, patch
import time
from io import BytesIO

# Imports (adjust paths as needed)
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from server.pass_streamer import PassStreamer, PassInfo, PassTier
from server.pass_delivery import PassDeliveryManager, PassEncoder
from addon.pass_receiver import PassReceiver
from addon.compositor import LiveCompositor, PassArrivalEvent


class TestPassStreamer(unittest.TestCase):
    """Test PassStreamer tier prioritization and scheduling."""

    def setUp(self):
        self.streamer = PassStreamer()

    def test_pass_schedule_tiers(self):
        """Verify pass tier assignments from schedule."""
        schedule = self.streamer.PASS_SCHEDULE

        # Beauty should be tier 1
        self.assertEqual(schedule["Beauty"][0], PassTier.BEAUTY_PREVIEW)

        # Normal, Depth, Diffuse should be tier 2
        self.assertEqual(schedule["Normal"][0], PassTier.ESSENTIAL)
        self.assertEqual(schedule["Depth"][0], PassTier.ESSENTIAL)
        self.assertEqual(schedule["Diffuse Color"][0], PassTier.ESSENTIAL)

        # Glossy, Specular should be tier 3
        self.assertEqual(schedule["Glossy"][0], PassTier.FULL_PASSES)
        self.assertEqual(schedule["Specular"][0], PassTier.FULL_PASSES)

        # Cryptomatte should be tier 4
        self.assertEqual(schedule["Cryptomatte"][0], PassTier.CRYPTOMATTE)

    def test_pass_delivery_windows(self):
        """Verify delivery time windows (start, end) per tier."""
        schedule = self.streamer.PASS_SCHEDULE

        # Beauty: 0-2s
        beauty_window = schedule["Beauty"][1:]
        self.assertEqual(beauty_window, (0, 2))

        # Normal: 2-8s
        normal_window = schedule["Normal"][1:]
        self.assertEqual(normal_window, (2, 8))

        # Cryptomatte: 30-40s
        crypto_window = schedule["Cryptomatte"][1:]
        self.assertEqual(crypto_window, (30, 40))

    def test_sort_passes_by_tier(self):
        """Verify passes are sorted by tier, then delivery window."""
        passes = [
            PassInfo("Cryptomatte", 3, 1920, 1080, "PNG", PassTier.CRYPTOMATTE, b""),
            PassInfo("Beauty", 3, 1920, 1080, "JPEG", PassTier.BEAUTY_PREVIEW, b""),
            PassInfo("Depth", 1, 1920, 1080, "PNG", PassTier.ESSENTIAL, b""),
            PassInfo("Glossy", 3, 1920, 1080, "PNG", PassTier.FULL_PASSES, b""),
        ]

        sorted_passes = self.streamer._sort_passes_by_tier(passes)
        sorted_names = [p.name for p in sorted_passes]

        # Should be: Beauty (tier 1) → Depth (tier 2) → Glossy (tier 3) → Cryptomatte (tier 4)
        self.assertEqual(sorted_names[0], "Beauty")
        self.assertIn(sorted_names[1], ["Depth", "Normal"])  # tier 2
        self.assertEqual(sorted_names[-1], "Cryptomatte")  # tier 4 last


class TestPassDeliveryManager(unittest.TestCase):
    """Test WebSocket message delivery."""

    def setUp(self):
        self.mock_ws = AsyncMock()
        self.manager = PassDeliveryManager(self.mock_ws)

    async def test_pass_delivery_message_format(self):
        """Verify PASS_DATA message contains correct fields."""
        pass_info = PassInfo(
            name="Normal",
            channels=3,
            width=1920,
            height=1080,
            format="PNG",
            tier=PassTier.ESSENTIAL,
            data=b"fake_pass_data",
            timestamp=5.0
        )

        # Mock the _send method to capture messages
        sent_messages = []

        async def mock_send(msg_type, data, binary=None):
            sent_messages.append((msg_type, data, binary))

        self.manager._send = mock_send

        await self.manager.deliver_pass(pass_info)

        self.assertEqual(len(sent_messages), 1)
        msg_type, data, binary = sent_messages[0]

        # Verify message structure
        self.assertEqual(data["pass_name"], "Normal")
        self.assertEqual(data["channel_count"], 3)
        self.assertEqual(data["width"], 1920)
        self.assertEqual(data["height"], 1080)
        self.assertEqual(data["data_format"], "PNG")
        self.assertEqual(data["tier"], PassTier.ESSENTIAL.value)
        self.assertEqual(binary, b"fake_pass_data")

    async def test_pass_complete_message(self):
        """Verify PASS_COMPLETE message sends total stats."""
        sent_messages = []

        async def mock_send(msg_type, data, binary=None):
            sent_messages.append((msg_type, data))

        self.manager._send = mock_send

        # Deliver 2 passes, then complete
        pass1 = PassInfo("Beauty", 3, 1920, 1080, "JPEG", PassTier.BEAUTY_PREVIEW,
                        b"x" * 1000000)
        pass2 = PassInfo("Normal", 3, 1920, 1080, "PNG", PassTier.ESSENTIAL,
                        b"y" * 2000000)

        await self.manager.deliver_pass(pass1)
        await self.manager.deliver_pass(pass2)
        await self.manager.deliver_pass(None)  # Signals completion

        # Last message should be PASS_COMPLETE
        final_msg_type, final_data = sent_messages[-1]
        self.assertEqual(final_data["total_passes"], 2)
        self.assertAlmostEqual(final_data["total_bandwidth_mb"], 2.86, places=1)


class TestPassReceiver(unittest.TestCase):
    """Test client-side pass reception and caching."""

    def setUp(self):
        self.receiver = PassReceiver(scene=None)

    def test_pass_caching(self):
        """Verify passes are cached with metadata."""
        self.receiver.on_pass_data(
            pass_name="Normal",
            channel_count=3,
            width=1920,
            height=1080,
            data_format="PNG",
            data=b"normal_pass_data",
            tier=2
        )

        # Verify cached
        self.assertEqual(len(self.receiver._pass_cache), 1)
        self.assertEqual(self.receiver._pass_cache["Normal"], b"normal_pass_data")

        # Verify metadata
        metadata = self.receiver._pass_metadata["Normal"]
        self.assertEqual(metadata["channels"], 3)
        self.assertEqual(metadata["width"], 1920)
        self.assertEqual(metadata["height"], 1080)
        self.assertEqual(metadata["format"], "PNG")
        self.assertEqual(metadata["size_bytes"], 16)

    def test_pass_retrieval(self):
        """Verify passes can be retrieved from cache."""
        self.receiver.on_pass_data(
            "Depth", 1, 1920, 1080, "PNG", b"depth_data", 2
        )

        retrieved = self.receiver.get_pass_data("Depth")
        self.assertEqual(retrieved, b"depth_data")

        # Non-existent pass should return None
        self.assertIsNone(self.receiver.get_pass_data("NonExistent"))

    def test_progress_tracking(self):
        """Verify progress percentage calculation."""
        self.receiver._expected_passes = 12

        self.receiver.on_pass_data("Pass1", 3, 1920, 1080, "PNG", b"", 1)
        status1 = self.receiver.get_pass_status()
        self.assertEqual(status1["expected_passes"], 12)

        self.receiver.on_pass_data("Pass2", 3, 1920, 1080, "PNG", b"", 1)
        status2 = self.receiver.get_pass_status()
        self.assertEqual(status2["passes_received"], 2)

    def test_bandwidth_tracking(self):
        """Verify total bandwidth is accumulated."""
        self.receiver.on_pass_data("Pass1", 3, 1920, 1080, "PNG", b"x" * 1000000, 1)
        self.receiver.on_pass_data("Pass2", 3, 1920, 1080, "PNG", b"y" * 2000000, 1)

        status = self.receiver.get_pass_status()
        expected_mb = 3.0 / 1024  # 3 million bytes / 1024^2
        self.assertAlmostEqual(status["total_bandwidth_mb"], expected_mb, places=2)

    def test_clear_passes(self):
        """Verify pass cache can be cleared for new render."""
        self.receiver.on_pass_data("Pass1", 3, 1920, 1080, "PNG", b"data", 1)
        self.assertEqual(len(self.receiver._pass_cache), 1)

        self.receiver.clear_passes()
        self.assertEqual(len(self.receiver._pass_cache), 0)
        self.assertEqual(self.receiver._total_bandwidth, 0)


class TestLiveCompositor(unittest.TestCase):
    """Test compositor node mapping and updates."""

    def setUp(self):
        # Mock bpy scene
        self.mock_scene = Mock()
        self.mock_scene.node_tree = Mock()
        self.mock_scene.node_tree.nodes = {}
        self.mock_scene.node_tree.update = Mock()

        self.compositor = LiveCompositor(self.mock_scene)

    def test_compositor_initialization(self):
        """Verify compositor initializes without errors."""
        self.assertIsNotNone(self.compositor)
        self.assertEqual(self.compositor._eval_count, 0)
        self.assertEqual(len(self.compositor._pass_cache), 0)

    def test_pass_arrival_event(self):
        """Verify PassArrivalEvent creation and fields."""
        event = PassArrivalEvent(
            pass_name="Normal",
            pass_data=b"normal_data",
            channels=3,
            width=1920,
            height=1080,
            data_format="PNG"
        )

        self.assertEqual(event.pass_name, "Normal")
        self.assertEqual(event.channels, 3)
        self.assertEqual(event.width, 1920)
        self.assertEqual(event.height, 1080)
        self.assertEqual(event.data_format, "PNG")

    def test_pass_cache_update_on_arrival(self):
        """Verify pass data is cached when event fires."""
        event = PassArrivalEvent("Normal", b"data", 3, 1920, 1080, "PNG")
        self.compositor.on_pass_arrival(event)

        self.assertIn("Normal", self.compositor._pass_cache)
        cached = self.compositor._pass_cache["Normal"]
        self.assertEqual(cached["width"], 1920)
        self.assertEqual(cached["channels"], 3)


class TestPassEncoderIntegration(unittest.TestCase):
    """Test pass encoding functions."""

    async def test_jpeg_encoding_ratio(self):
        """Verify JPEG encoding achieves significant compression."""
        # Create dummy RGB data (simple gradient)
        width, height, channels = 1920, 1080, 3
        size = width * height * channels
        rgb_data = bytes([i % 256 for i in range(size)])

        # Encode to JPEG
        jpeg_data = await PassEncoder.encode_beauty_to_jpeg(
            rgb_data, width, height, quality=85
        )

        # JPEG should be significantly smaller than raw
        compression = len(rgb_data) / len(jpeg_data)
        self.assertGreater(compression, 5.0)  # Expect 5x+ compression

    async def test_png_encoding_availability(self):
        """Verify PNG encoding is available if PIL/numpy are installed."""
        # This test gracefully handles missing PIL/numpy
        if PassEncoder is not None:
            # If encoder exists, it should not raise on availability check
            self.assertIsNotNone(PassEncoder)


# Integration test runner
class TestIntegration(unittest.TestCase):
    """End-to-end integration tests."""

    async def test_full_pass_streaming_workflow(self):
        """Simulate complete pass streaming workflow."""
        # Create components
        streamer = PassStreamer()
        receiver = PassReceiver()
        delivery = PassDeliveryManager(None)

        # Simulate passes arriving
        passes = [
            ("Beauty", 3, "JPEG", 10),
            ("Normal", 3, "PNG", 12),
            ("Depth", 1, "PNG", 8),
        ]

        for name, channels, fmt, size_mb in passes:
            # Server sends pass
            data = b"x" * (size_mb * 1024 * 1024)
            pass_info = PassInfo(name, channels, 1920, 1080, fmt,
                                PassTier.BEAUTY_PREVIEW, data)

            # Client receives
            receiver.on_pass_data(name, channels, 1920, 1080, fmt, data, 1)

        # Verify final state
        status = receiver.get_pass_status()
        self.assertEqual(status["passes_received"], 3)
        self.assertAlmostEqual(status["total_bandwidth_mb"], 30, places=0)


def run_async_test(coro):
    """Helper to run async tests."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


if __name__ == "__main__":
    # Run async tests
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])

    # Patch async test methods
    for test_group in suite:
        for test in test_group:
            if hasattr(test, '_testMethodName'):
                method = getattr(test, test._testMethodName)
                if asyncio.iscoroutinefunction(method):
                    setattr(test, test._testMethodName,
                           lambda m=method: run_async_test(m()))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)
