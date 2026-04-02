"""Standalone tests for RingBuffer class (Phase 11).

Tests can run without Blender/bpy dependency by directly testing
the RingBuffer implementation copied below.
"""

import time
import threading
from collections import deque
from typing import Optional, Tuple


class RingBuffer:
    """Bounded ring buffer for frame data with FIFO eviction and metrics.

    (Copy of addon/connection.py::RingBuffer for standalone testing)
    """

    def __init__(self, max_frames: int = 50):
        self.max_frames = max_frames
        self._lock = threading.Lock()
        self._buffer: deque = deque(maxlen=max_frames)

        # Metrics
        self.frames_received = 0
        self.frames_dropped = 0
        self.buffer_size = 0
        self.latencies: deque = deque(maxlen=100)

    def put(self, frame: Tuple, timestamp_sent: Optional[float] = None) -> bool:
        with self._lock:
            was_full = len(self._buffer) >= self.max_frames
            self._buffer.append((frame, time.time(), timestamp_sent))
            self.frames_received += 1
            self.buffer_size = len(self._buffer)

            if was_full:
                self.frames_dropped += 1

            if timestamp_sent is not None:
                latency_ms = (time.time() - timestamp_sent) * 1000
                self.latencies.append(latency_ms)

            return not was_full

    def get(self) -> Optional[Tuple]:
        with self._lock:
            if self._buffer:
                frame, time_received, timestamp_sent = self._buffer[-1]
                return frame
        return None

    def drain(self) -> list:
        with self._lock:
            frames = list(self._buffer)
            self._buffer.clear()
            self.buffer_size = 0
            return frames

    def get_metrics(self) -> dict:
        with self._lock:
            avg_latency_ms = sum(self.latencies) / len(self.latencies) if self.latencies else 0
            return {
                "buffer_size": self.buffer_size,
                "frames_received": self.frames_received,
                "frames_dropped": self.frames_dropped,
                "avg_latency_ms": avg_latency_ms,
                "max_frames": self.max_frames,
            }


# ============================================================================
# Tests
# ============================================================================

def test_ring_buffer_basic():
    """Test basic frame addition and retrieval."""
    buffer = RingBuffer(max_frames=5)

    for i in range(3):
        frame = (f"frame_{i}".encode(), {"index": i})
        added = buffer.put(frame)
        assert added, f"Frame {i} should be added"

    latest = buffer.get()
    assert latest is not None
    jpeg_bytes, meta = latest
    assert meta["index"] == 2, "Should return the latest frame"

    metrics = buffer.get_metrics()
    assert metrics["buffer_size"] == 3
    assert metrics["frames_dropped"] == 0
    print("✓ test_ring_buffer_basic passed")


def test_ring_buffer_overflow():
    """Test FIFO eviction on buffer overflow."""
    buffer = RingBuffer(max_frames=3)

    for i in range(3):
        frame = (f"frame_{i}".encode(), {"index": i})
        buffer.put(frame)

    frame_4 = (b"frame_4", {"index": 4})
    added = buffer.put(frame_4)

    assert not added, "4th frame should not be added (buffer full)"

    latest = buffer.get()
    jpeg_bytes, meta = latest
    assert meta["index"] in [1, 2, 4], f"Expected frame 1, 2, or 4, got {meta['index']}"

    metrics = buffer.get_metrics()
    assert metrics["frames_dropped"] == 1
    assert metrics["buffer_size"] <= 3
    print("✓ test_ring_buffer_overflow passed")


def test_ring_buffer_multiple_overflows():
    """Test multiple overflow events."""
    buffer = RingBuffer(max_frames=2)

    # Add 5 frames total
    for i in range(5):
        frame = (f"frame_{i}".encode(), {"index": i})
        added = buffer.put(frame)
        print(f"  Frame {i}: added={added}, buffer_size={buffer.buffer_size}")

    metrics = buffer.get_metrics()
    # Frames 0 and 1 dropped, keeping only [3, 4]
    assert metrics["frames_dropped"] == 3, f"Expected 3 drops, got {metrics['frames_dropped']}"
    assert metrics["buffer_size"] == 2
    assert metrics["frames_received"] == 5

    latest = buffer.get()
    jpeg_bytes, meta = latest
    assert meta["index"] == 4, "Latest frame should be 4"
    print("✓ test_ring_buffer_multiple_overflows passed")


def test_ring_buffer_latency_tracking():
    """Test latency calculation from timestamps."""
    buffer = RingBuffer(max_frames=10)

    now = time.time()
    frame = (b"frame_data", {"index": 0})
    buffer.put(frame, timestamp_sent=now - 0.050)

    metrics = buffer.get_metrics()
    assert len(buffer.latencies) == 1
    avg_latency = metrics["avg_latency_ms"]
    assert 40 < avg_latency < 80, f"Expected ~50ms latency, got {avg_latency}ms"
    print(f"✓ test_ring_buffer_latency_tracking passed (latency={avg_latency:.1f}ms)")


def test_ring_buffer_thread_safe():
    """Test thread-safe concurrent access."""
    buffer = RingBuffer(max_frames=100)
    errors = []

    def producer():
        try:
            for i in range(50):
                frame = (f"frame_{i}".encode(), {"index": i})
                buffer.put(frame)
                time.sleep(0.0001)
        except Exception as e:
            errors.append(f"Producer error: {e}")

    def consumer():
        try:
            for _ in range(50):
                frame = buffer.get()
                time.sleep(0.0001)
        except Exception as e:
            errors.append(f"Consumer error: {e}")

    t1 = threading.Thread(target=producer)
    t2 = threading.Thread(target=consumer)

    t1.start()
    t2.start()

    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not errors, f"Thread safety errors: {errors}"
    assert not t1.is_alive(), "Producer thread did not complete"
    assert not t2.is_alive(), "Consumer thread did not complete"
    print("✓ test_ring_buffer_thread_safe passed")


def test_ring_buffer_drain():
    """Test draining all frames."""
    buffer = RingBuffer(max_frames=5)

    for i in range(3):
        frame = (f"frame_{i}".encode(), {"index": i})
        buffer.put(frame)

    frames = buffer.drain()
    assert len(frames) == 3
    assert buffer.buffer_size == 0

    for i, (frame, timestamp_received, timestamp_sent) in enumerate(frames):
        jpeg_bytes, meta = frame
        assert meta["index"] == i
    print("✓ test_ring_buffer_drain passed")


def test_simulated_network_jitter():
    """Simulate network jitter with artificial delays."""
    buffer = RingBuffer(max_frames=50)

    base_time = time.time()
    latencies = [10, 15, 12, 50, 200, 250, 15, 20]

    for i, latency_ms in enumerate(latencies):
        timestamp_sent = base_time - (latency_ms / 1000.0)
        frame = (f"frame_{i}".encode(), {"index": i, "latency_ms": latency_ms})
        buffer.put(frame, timestamp_sent=timestamp_sent)

    metrics = buffer.get_metrics()
    avg_latency = metrics["avg_latency_ms"]
    expected_avg = sum(latencies) / len(latencies)

    # Allow 20% tolerance
    assert abs(avg_latency - expected_avg) < expected_avg * 0.2, \
        f"Expected avg {expected_avg}ms, got {avg_latency}ms"

    print(f"✓ test_simulated_network_jitter passed (avg={avg_latency:.1f}ms, expected={expected_avg:.1f}ms)")


def test_no_frames_available():
    """Test behavior with empty buffer."""
    buffer = RingBuffer(max_frames=5)

    result = buffer.get()
    assert result is None, "Empty buffer should return None"

    metrics = buffer.get_metrics()
    assert metrics["buffer_size"] == 0
    assert metrics["frames_received"] == 0
    assert metrics["frames_dropped"] == 0
    print("✓ test_no_frames_available passed")


def test_latency_without_timestamp():
    """Test graceful behavior when timestamp is not provided."""
    buffer = RingBuffer(max_frames=10)

    frame = (b"frame_data", {"index": 0})
    added = buffer.put(frame, timestamp_sent=None)

    assert added
    assert len(buffer.latencies) == 0, "No timestamp should not record latency"

    metrics = buffer.get_metrics()
    assert metrics["avg_latency_ms"] == 0
    print("✓ test_latency_without_timestamp passed")


if __name__ == "__main__":
    print("Running Phase 11 RingBuffer tests...\n")

    test_ring_buffer_basic()
    test_ring_buffer_overflow()
    test_ring_buffer_multiple_overflows()
    test_ring_buffer_latency_tracking()
    test_ring_buffer_thread_safe()
    test_ring_buffer_drain()
    test_simulated_network_jitter()
    test_no_frames_available()
    test_latency_without_timestamp()

    print("\n✅ All Phase 11 tests passed!")
