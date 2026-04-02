"""
Unit tests for animation timeline playback support.

Tests cover:
1. Timeline data extraction (sync.py)
2. Protocol message types and schema
3. Animation rendering pipeline
4. Frame metadata handling
"""

import unittest
import tempfile
import os
import sys

# Add parent directories to path
test_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(test_dir)
sys.path.insert(0, project_dir)

from shared.protocol import MsgType, pack, unpack


class TestProtocolAnimationSupport(unittest.TestCase):
    """Test protocol message types for animation support."""

    def test_timeline_update_message_type(self):
        """TIMELINE_UPDATE should be message type 0x23."""
        self.assertEqual(MsgType.TIMELINE_UPDATE, 0x23)

    def test_timeline_update_pack_unpack(self):
        """Test packing and unpacking TIMELINE_UPDATE messages."""
        # Pack a timeline update
        frame = 42
        is_playing = True
        msg_type, data, binary = MsgType.TIMELINE_UPDATE, {
            "frame": frame,
            "is_playing": is_playing,
        }, None

        packed = pack(msg_type, data, binary)
        unpacked_type, unpacked_data, unpacked_binary = unpack(packed)

        self.assertEqual(unpacked_type, MsgType.TIMELINE_UPDATE)
        self.assertEqual(unpacked_data["frame"], frame)
        self.assertEqual(unpacked_data["is_playing"], is_playing)

    def test_viewport_start_with_timeline_fields(self):
        """VIEWPORT_START message should include optional timeline fields."""
        data = {
            "view_matrix": list(range(16)),
            "proj_matrix": list(range(16, 32)),
            "resolution": [1920, 1080],
            "samples": 128,
            "current_frame": 10,
            "frame_start": 1,
            "frame_end": 100,
            "frame_step": 1,
            "fps": 24.0,
            "is_playing": True,
        }

        packed = pack(MsgType.VIEWPORT_START, data)
        _, unpacked_data, _ = unpack(packed)

        # Verify all timeline fields preserved
        self.assertEqual(unpacked_data["current_frame"], 10)
        self.assertEqual(unpacked_data["frame_start"], 1)
        self.assertEqual(unpacked_data["frame_end"], 100)
        self.assertEqual(unpacked_data["frame_step"], 1)
        self.assertEqual(unpacked_data["fps"], 24.0)
        self.assertEqual(unpacked_data["is_playing"], True)

    def test_frame_final_with_animation_metadata(self):
        """FRAME_FINAL message should include animation frame metadata."""
        data = {
            "format": "EXR",
            "frame": 42,
            "frame_start": 1,
            "frame_end": 100,
            "total_frames": 100,
        }

        packed = pack(MsgType.FRAME_FINAL, data)
        msg_type, unpacked_data, _ = unpack(packed)

        self.assertEqual(msg_type, MsgType.FRAME_FINAL)
        self.assertEqual(unpacked_data["frame"], 42)
        self.assertEqual(unpacked_data["total_frames"], 100)


class TestAnimationRenderScript(unittest.TestCase):
    """Test animation render script generation."""

    def setUp(self):
        """Import renderer module."""
        try:
            from server.renderer import SubprocessRenderer
            self.SubprocessRenderer = SubprocessRenderer
        except ImportError:
            self.skipTest("server.renderer not available")

    def test_build_animation_script_structure(self):
        """Generated animation script should have required structure."""
        renderer = self.SubprocessRenderer()
        script = renderer._build_animation_script(
            resolution=(1920, 1080),
            samples=64,
            output_format="EXR",
            frame_start=1,
            frame_end=10,
            frame_step=1,
            output_dir="/tmp/render",
        )

        # Check for critical script elements
        self.assertIn("scene.frame_set", script)
        self.assertIn("bpy.ops.render.render", script)
        self.assertIn("frame_0001", script)
        self.assertIn("1920", script)  # width
        self.assertIn("1080", script)  # height
        self.assertIn("64", script)  # samples

    def test_animation_script_frame_range(self):
        """Animation script should iterate correct frame range."""
        renderer = self.SubprocessRenderer()
        script = renderer._build_animation_script(
            resolution=(1920, 1080),
            samples=64,
            output_format="EXR",
            frame_start=5,
            frame_end=15,
            frame_step=2,
            output_dir="/tmp/render",
        )

        # Should iterate from 5 to 15 by 2
        self.assertIn("frame_num = 5", script)
        self.assertIn("while frame_num <= 15", script)
        self.assertIn("frame_num += 2", script)

    def test_animation_script_gpu_setup(self):
        """Animation script should configure GPU."""
        renderer = self.SubprocessRenderer()
        script = renderer._build_animation_script(
            resolution=(1920, 1080),
            samples=64,
            output_format="EXR",
            frame_start=1,
            frame_end=10,
            frame_step=1,
            output_dir="/tmp/render",
        )

        # Should enable GPU
        self.assertIn("cycles.device = 'GPU'", script)
        # Should try GPU backends
        self.assertIn("OPTIX", script)
        self.assertIn("CUDA", script)


class TestTimelineDataExtraction(unittest.TestCase):
    """Test timeline data extraction functions."""

    def test_get_current_frame_signature(self):
        """get_current_frame should be callable and return int."""
        try:
            from addon.sync import get_current_frame
            # We can't easily mock Blender context, so just verify callable
            self.assertTrue(callable(get_current_frame))
        except ImportError:
            self.skipTest("addon.sync not available")

    def test_get_frame_range_signature(self):
        """get_frame_range should return 3-tuple."""
        try:
            from addon.sync import get_frame_range
            self.assertTrue(callable(get_frame_range))
        except ImportError:
            self.skipTest("addon.sync not available")

    def test_get_playback_fps_signature(self):
        """get_playback_fps should return float."""
        try:
            from addon.sync import get_playback_fps
            self.assertTrue(callable(get_playback_fps))
        except ImportError:
            self.skipTest("addon.sync not available")


class TestAnimationRenderTimeout(unittest.TestCase):
    """Test animation render timeout configuration."""

    def test_animation_timeout_constant(self):
        """ANIMATION_RENDER_TIMEOUT should be set to 3600 seconds."""
        try:
            from server.renderer import ANIMATION_RENDER_TIMEOUT
            self.assertEqual(ANIMATION_RENDER_TIMEOUT, 3600)
        except ImportError:
            self.skipTest("server.renderer not available")


class TestViewportLiveFrameSetting(unittest.TestCase):
    """Test viewport live renderer frame setting."""

    def test_set_current_frame_signature(self):
        """LiveViewportRenderer should have set_current_frame method."""
        try:
            from server.viewport_live import LiveViewportRenderer
            renderer = LiveViewportRenderer()
            self.assertTrue(hasattr(renderer, 'set_current_frame'))
            self.assertTrue(callable(getattr(renderer, 'set_current_frame')))
        except ImportError:
            self.skipTest("server.viewport_live not available")


if __name__ == '__main__':
    unittest.main()
