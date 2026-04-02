"""Tests for bounded ring buffer frame buffering (Phase 11).

Tests the RingBuffer class with overflow conditions, latency tracking,
and metrics collection.
"""

import sys
import os
import time
import threading
from pathlib import Path
from collections import deque

# Add parent directory to path
test_dir = Path(__file__).parent
project_dir = test_dir.parent
sys.path.insert(0, str(project_dir))

# Import RingBuffer directly (avoid bpy import)
import importlib.util
connection_path = project_dir / "addon" / "connection.py"
spec = importlib.util.spec_from_file_location("connection_module", connection_path)
connection_module = importlib.util.module_from_spec(spec)

# Mock bpy before loading connection.py
sys.modules['bpy'] = None

spec.loader.exec_module(connection_module)
RingBuffer = connection_module.RingBuffer


def test_ring_buffer_basic():
    """Test basic frame addition and retrieval."""
    buffer = RingBuffer(max_frames=5)

    # Add frames
    for i in range(3):
        frame = (f"frame_{i}".encode(), {"index": i})
        added = buffer.put(frame)
        assert added, f"Frame {i} should be added"

    # Get latest frame
    latest = buffer.get()
    assert latest is not None
    jpeg_bytes, meta = latest
    assert meta["index"] == 2, "Should return the latest frame"

    metrics = buffer.get_metrics()
    assert metrics["buffer_size"] == 3
    assert metrics["frames_dropped"] == 0


def test_ring_buffer_overflow():
    """Test FIFO eviction on buffer overflow."""
    buffer = RingBuffer(max_frames=3)

    # Fill buffer to capacity
    for i in range(3):
        frame = (f"frame_{i}".encode(), {"index": i})
        buffer.put(frame)

    # Add one more — should trigger drop
    frame_4 = (b"frame_4", {"index": 4})
    added = buffer.put(frame_4)

    # Frame 4 was not added (frame 0 was dropped instead)
    assert not added, "4th frame should not be added (buffer full)"

    # Latest frame should still be frame 3 (from original 0-2 + new 3 replaced 0)
    latest = buffer.get()
    jpeg_bytes, meta = latest
    # After putting 4 frames with max_frames=3: [1,2,4] with 0 dropped
    assert meta["index"] in [1, 2, 4], f"Expected frame 1, 2, or 4, got {meta['index']}"

    metrics = buffer.get_metrics()
    assert metrics["frames_dropped"] == 1
    assert metrics["buffer_size"] <= 3


def test_ring_buffer_latency_tracking():
    """Test latency calculation from timestamps."""
    buffer = RingBuffer(max_frames=10)

    now = time.time()
    # Simulate frame sent 50ms ago
    frame = (b"frame_data", {"index": 0})
    buffer.put(frame, timestamp_sent=now - 0.050)

    metrics = buffer.get_metrics()
    assert len(buffer.latencies) == 1
    avg_latency = metrics["avg_latency_ms"]
    assert 40 < avg_latency < 80, f"Expected ~50ms latency, got {avg_latency}ms"


def test_ring_buffer_thread_safe():
    """Test thread-safe concurrent access."""
    buffer = RingBuffer(max_frames=100)
    errors = []

    def producer():
        try:
            for i in range(50):
                frame = (f"frame_{i}".encode(), {"index": i})
                buffer.put(frame)
                time.sleep(0.001)
        except Exception as e:
            errors.append(f"Producer error: {e}")

    def consumer():
        try:
            for _ in range(50):
                frame = buffer.get()
                time.sleep(0.001)
        except Exception as e:
            errors.append(f"Consumer error: {e}")

    # Run producer and consumer concurrently
    t1 = threading.Thread(target=producer)
    t2 = threading.Thread(target=consumer)

    t1.start()
    t2.start()

    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not errors, f"Thread safety errors: {errors}"
    assert not t1.is_alive(), "Producer thread did not complete"
    assert not t2.is_alive(), "Consumer thread did not complete"


def test_ring_buffer_drain():
    """Test draining all frames."""
    buffer = RingBuffer(max_frames=5)

    # Add frames
    for i in range(3):
        frame = (f"frame_{i}".encode(), {"index": i})
        buffer.put(frame)

    # Drain
    frames = buffer.drain()
    assert len(frames) == 3
    assert buffer.buffer_size == 0

    # Verify frames
    for i, (frame, timestamp_received, timestamp_sent) in enumerate(frames):
        jpeg_bytes, meta = frame
        assert meta["index"] == i


def test_client_frame_buffer_async():
    """Test ClientFrameBuffer from server side."""
    import asyncio
    from server.server import ClientFrameBuffer

    async def test():
        buffer = ClientFrameBuffer("127.0.0.1:5000", max_frames=3)

        # Add frames
        for i in range(3):
            dropped = not await buffer.put(f"frame_{i}".encode(), {"index": i})
            assert not dropped, f"Frame {i} should not be dropped"

        # Add one more — should trigger drop
        dropped = not await buffer.put(b"frame_4", {"index": 4})
        assert dropped, "Frame 4 should not be added (buffer full)"

        # Check metrics
        metrics = await buffer.get_metrics()
        assert metrics["frames_dropped"] == 1
        assert metrics["frames_queued"] == 4

    asyncio.run(test())


def test_simulated_network_jitter():
    """Simulate network jitter with artificial delays."""
    buffer = RingBuffer(max_frames=50)
    jitter_errors = []

    # Simulate frames arriving with varying latencies
    base_time = time.time()
    latencies = [10, 15, 12, 50, 200, 250, 15, 20]  # ms

    for i, latency_ms in enumerate(latencies):
        # Simulate frame sent latency_ms ago
        timestamp_sent = base_time - (latency_ms / 1000.0)
        frame = (f"frame_{i}".encode(), {"index": i, "latency_ms": latency_ms})
        buffer.put(frame, timestamp_sent=timestamp_sent)

    # Check metrics
    metrics = buffer.get_metrics()
    avg_latency = metrics["avg_latency_ms"]
    expected_avg = sum(latencies) / len(latencies)

    # Allow 20% tolerance due to timing variations
    assert abs(avg_latency - expected_avg) < expected_avg * 0.2, \
        f"Expected avg {expected_avg}ms, got {avg_latency}ms"

    print(f"Jitter test: avg_latency={avg_latency:.1f}ms, expected={expected_avg:.1f}ms")


if __name__ == "__main__":
    print("Running Phase 11 frame buffer tests...")

    test_ring_buffer_basic()
    print("✓ test_ring_buffer_basic")

    test_ring_buffer_overflow()
    print("✓ test_ring_buffer_overflow")

    test_ring_buffer_latency_tracking()
    print("✓ test_ring_buffer_latency_tracking")

    test_ring_buffer_thread_safe()
    print("✓ test_ring_buffer_thread_safe")

    test_ring_buffer_drain()
    print("✓ test_ring_buffer_drain")

    test_client_frame_buffer_async()
    print("✓ test_client_frame_buffer_async")

    test_simulated_network_jitter()
    print("✓ test_simulated_network_jitter")

    print("\nAll Phase 11 tests passed!")
