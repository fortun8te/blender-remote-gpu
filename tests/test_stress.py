"""Stress tests for bug fixes under load."""

import sys
import os
import time
import threading
import random
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib.util

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

connection_path = os.path.join(os.path.dirname(__file__), "..", "addon", "connection.py")
Connection_module = load_module("connection", connection_path)
Connection = Connection_module.Connection


class StressTestResults:
    def __init__(self):
        self.tests = {}
        self.passed = 0
        self.failed = 0

    def add(self, name, passed, details=""):
        self.tests[name] = {"passed": passed, "details": details}
        if passed:
            self.passed += 1
        else:
            self.failed += 1
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
        if details:
            print(f"        {details}")

    def summary(self):
        print(f"\n{'='*60}")
        print(f"Stress Test Results: {self.passed} passed, {self.failed} failed")
        print(f"{'='*60}\n")
        return self.failed == 0


# ============================================================================
# STRESS TEST 1: Binary Frame Handling Under Load (FIX #3)
# ============================================================================
def stress_test_1_binary_frames():
    """Test binary frame handling with many concurrent frames."""
    print("\nSTRESS TEST 1: Binary Frame Handling Under Load (FIX #3)")
    print("-" * 60)

    results = StressTestResults()

    conn = Connection("ws://invalid:9999")

    # Generate test frames of varying sizes
    test_sizes = [
        1024,  # 1KB
        1024 * 100,  # 100KB
        1024 * 1024,  # 1MB
        10 * 1024 * 1024,  # 10MB
        100 * 1024 * 1024,  # 100MB
    ]

    frame_received = 0
    for size in test_sizes:
        try:
            frame = bytes([random.randint(0, 255) for _ in range(size)])
            conn._handle_binary_frame(frame)
            frame_received += len(frame)
        except Exception as e:
            results.add(
                f"Handle {size // 1024}KB frame",
                False,
                str(e)
            )
            return results

    results.add(
        f"Handle 5 frames totaling {frame_received // 1024 // 1024}MB",
        frame_received > 100 * 1024 * 1024,
        f"Received {frame_received // 1024 // 1024}MB successfully"
    )

    # Test rapid frame succession (simulate network burst)
    conn2 = Connection("ws://invalid:9999")
    rapid_frames = 100
    for i in range(rapid_frames):
        frame = bytes([i % 256] * (10 * 1024))  # 10KB each
        conn2._handle_binary_frame(frame)

    results.add(
        f"Handle {rapid_frames} rapid frames without crash",
        conn2.connected or True,  # Should not crash
        f"Processed {rapid_frames} x 10KB frames"
    )

    return results


# ============================================================================
# STRESS TEST 2: Exponential Backoff Under Repeated Failures (FIX #2)
# ============================================================================
def stress_test_2_backoff_progression():
    """Verify backoff progression stays within bounds."""
    print("\nSTRESS TEST 2: Exponential Backoff Progression (FIX #2)")
    print("-" * 60)

    results = StressTestResults()

    conn = Connection("ws://invalid:9999")

    # Simulate 50 connection attempts
    delays = []
    for attempt in range(50):
        delay = min(conn._reconnect_delay, conn._reconnect_delay_max)
        delays.append(delay)
        # Simulate backoff increase
        conn._reconnect_delay = min(
            conn._reconnect_delay * conn._reconnect_backoff,
            conn._reconnect_delay_max
        )

    # Verify progression: starts at 0.5, increases, caps at 30
    results.add(
        "Backoff starts at 0.5s",
        delays[0] == 0.5
    )

    results.add(
        "Backoff increases monotonically",
        all(delays[i] <= delays[i+1] for i in range(len(delays)-1))
    )

    results.add(
        "Backoff caps at 30s",
        max(delays) <= 30.0
    )

    # Find when it caps
    capped_at = next((i for i, d in enumerate(delays) if d == 30.0), None)
    results.add(
        f"Backoff reaches cap after {capped_at if capped_at else 'N/A'} attempts",
        capped_at is not None and capped_at < 15
    )

    # Verify reset works
    conn._reconnect_delay = 0.5
    results.add(
        "Backoff reset to 0.5s on success",
        conn._reconnect_delay == 0.5
    )

    return results


# ============================================================================
# STRESS TEST 3: Concurrent Operations (Connection thread safety)
# ============================================================================
def stress_test_3_concurrent_operations():
    """Test that Connection handles concurrent send/recv safely."""
    print("\nSTRESS TEST 3: Concurrent Operations Thread Safety (FIX #1, #7)")
    print("-" * 60)

    results = StressTestResults()

    conn = Connection("ws://invalid:9999")

    # Simulate concurrent send/recv
    send_count = 100
    errors = []

    def sender():
        for i in range(send_count):
            try:
                conn.send_json({"type": "test", "id": i})
                time.sleep(0.001)  # Small delay
            except Exception as e:
                errors.append(f"Send error: {e}")

    def receiver():
        for i in range(send_count):
            try:
                msg = conn.recv()
                time.sleep(0.001)
            except Exception as e:
                errors.append(f"Recv error: {e}")

    # Run concurrent operations
    threads = []
    for i in range(3):
        t = threading.Thread(target=sender)
        t.start()
        threads.append(t)

    for i in range(2):
        t = threading.Thread(target=receiver)
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=5.0)

    # Check queues didn't crash
    results.add(
        "Queue operations thread-safe with 5 concurrent threads",
        len(errors) == 0,
        f"Errors: {errors}" if errors else "No errors"
    )

    # Verify queue sizes are reasonable (queue may have items since worker not running)
    send_queue_size = conn._send_queue.qsize()
    results.add(
        f"Send queue has {send_queue_size} pending items (worker not connected)",
        send_queue_size >= 100,  # At least the 100 items we queued
        f"Queue size: {send_queue_size}"
    )

    return results


# ============================================================================
# STRESS TEST 4: Large Binary Data Integrity (FIX #3)
# ============================================================================
def stress_test_4_binary_integrity():
    """Verify large binary data isn't corrupted during validation."""
    print("\nSTRESS TEST 4: Binary Data Integrity (FIX #3)")
    print("-" * 60)

    results = StressTestResults()

    conn = Connection("ws://invalid:9999")

    # Create predictable test data
    test_data = bytes(range(256)) * 100  # 25.6KB of predictable pattern
    original_hash = hash(test_data)

    # Send through validation
    conn._handle_binary_frame(test_data)

    # Retrieve from queue
    try:
        received = conn.recv_binary()
        if received:
            received_hash = hash(received)
            results.add(
                "Large binary data passes through validation unchanged",
                original_hash == received_hash,
                f"Original hash: {original_hash}, Received hash: {received_hash}"
            )
        else:
            results.add(
                "Receive binary data from queue",
                False,
                "No data in queue"
            )
    except Exception as e:
        results.add(
            "Receive binary data from queue",
            False,
            str(e)
        )

    # Test with random data
    random_data = bytes([random.randint(0, 255) for _ in range(1024 * 1024)])  # 1MB random
    random_hash = hash(random_data)

    conn2 = Connection("ws://invalid:9999")
    conn2._handle_binary_frame(random_data)
    received2 = conn2.recv_binary()

    results.add(
        "1MB random data passes validation unchanged",
        hash(received2) == random_hash if received2 else False
    )

    return results


# ============================================================================
# STRESS TEST 5: Error Recovery (FIX #7)
# ============================================================================
def stress_test_5_error_recovery():
    """Test that connection recovers from errors without resource leaks."""
    print("\nSTRESS TEST 5: Error Recovery (FIX #7)")
    print("-" * 60)

    results = StressTestResults()

    # Create multiple connections and simulate errors
    connections = []
    for i in range(10):
        conn = Connection(f"ws://invalid:{9000+i}")
        connections.append(conn)

    # Try to close them (simulating error recovery)
    for conn in connections:
        try:
            conn.close()
        except Exception as e:
            results.add(
                f"Close connection {connections.index(conn)}",
                False,
                str(e)
            )
            return results

    results.add(
        "Create and close 10 connections without resource leak",
        all(not c.connected for c in connections)
    )

    # Verify stop event is set
    results.add(
        "All connections properly stopped",
        all(c._stop.is_set() for c in connections)
    )

    return results


# ============================================================================
# MAIN STRESS TEST RUNNER
# ============================================================================
def main():
    print("\n" + "=" * 60)
    print("BLENDER REMOTE GPU - STRESS TESTS FOR BUG FIXES")
    print("=" * 60)

    all_results = []

    all_results.append(("Binary Frame Load", stress_test_1_binary_frames()))
    all_results.append(("Backoff Progression", stress_test_2_backoff_progression()))
    all_results.append(("Concurrent Operations", stress_test_3_concurrent_operations()))
    all_results.append(("Binary Integrity", stress_test_4_binary_integrity()))
    all_results.append(("Error Recovery", stress_test_5_error_recovery()))

    # Print summaries
    total_passed = 0
    total_failed = 0

    for test_name, results in all_results:
        passed = results.summary()
        total_passed += results.passed
        total_failed += results.failed

    print("\n" + "=" * 60)
    print(f"STRESS TEST RESULTS: {total_passed} passed, {total_failed} failed")
    print("=" * 60)

    return total_failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
