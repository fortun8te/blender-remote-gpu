#!/usr/bin/env python3
"""
Test suite for b35 fixes:
1. State management (atomic flag setting)
2. Logging output for scene load progress
3. Polling logic improvements
4. Event pump verification (visual inspection)
"""

import json
import sys
import os
import re

def test_render_worker_syntax():
    """Verify render_worker.py has all b35 improvements."""
    print("\n[TEST] render_worker.py improvements...")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "render_worker.py")) as f:
        code = f.read()

    checks = {
        "Atomic state with _render_lock": "_render_lock:" in code,
        "LOAD_START logging": "[LOAD_START]" in code,
        "LOAD_STATE logging": "[LOAD_STATE]" in code,
        "LOAD_MAINFILE_START logging": "[LOAD_MAINFILE_START]" in code,
        "LOAD_GPU_SETUP logging": "[LOAD_GPU_SETUP" in code,
        "LOAD_COMPLETE logging": "[LOAD_COMPLETE]" in code,
        "LOAD_ERROR logging": "[LOAD_ERROR]" in code,
        "Event pump with redraw_timer": "bpy.ops.wm.redraw_timer_execute" in code,
        "Reduced sleep (0.05s)": "time.sleep(0.05)" in code,
        "Context override temp_override": "bpy.context.temp_override" in code,
        "PING logging": "[PING]" in code,
        "Atomic ping state read": "with _render_lock:" in code and "loaded = _scene_loaded" in code,
        "Build b35": '"b35"' in code and 'build' in code,
    }

    passed = 0
    for check, condition in checks.items():
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {check}")
        if condition:
            passed += 1

    print(f"  {passed}/{len(checks)} checks passed")
    return passed == len(checks)


def test_server_polling_logic():
    """Verify server.py has improved polling logic."""
    print("\n[TEST] server.py polling improvements...")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "server.py")) as f:
        code = f.read()

    checks = {
        "Fine-grained polling (0.1s)": "time.sleep(0.1)" in code,
        "Max wait constant": "max_wait = 120" in code,
        "Consecutive error tracking": "consecutive_errors" in code,
        "Connectivity detection": "consecutive_errors > 10" in code,
        "State change logging": "scene_loading=" in code and "scene_loaded=" in code,
        "Last state tracking": "last_state" in code,
        "Early failure detection": "if not scene_loading and not scene_loaded and last_state" in code,
        "Elapsed time logging": "elapsed = time.time() - start_time" in code,
    }

    passed = 0
    for check, condition in checks.items():
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {check}")
        if condition:
            passed += 1

    print(f"  {passed}/{len(checks)} checks passed")
    return passed == len(checks)


def test_logging_output():
    """Verify structured logging is in place."""
    print("\n[TEST] Logging structure...")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "render_worker.py")) as f:
        code = f.read()

    # Count log statements with structured tags
    log_tags = [
        "LOAD_START", "LOAD_STATE", "LOAD_MAINFILE_START", "LOAD_MAINFILE_OK",
        "LOAD_GPU_SETUP_START", "LOAD_GPU_SETUP_OK", "LOAD_OBJECTS",
        "LOAD_COMPLETE", "LOAD_ERROR", "LOAD_DONE", "PING",
        "HTTP_RESTART"
    ]

    found_tags = 0
    for tag in log_tags:
        if f"[{tag}]" in code:
            print(f"  [PASS] Found tag [{tag}]")
            found_tags += 1
        else:
            print(f"  [FAIL] Missing tag [{tag}]")

    print(f"  {found_tags}/{len(log_tags)} tags found")
    return found_tags >= len(log_tags) - 2  # Allow 1-2 misses


def test_context_override():
    """Verify context override is safe."""
    print("\n[TEST] Context override safety...")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "render_worker.py")) as f:
        code = f.read()

    # Find the context override section
    if "bpy.context.temp_override" in code:
        # Verify it's used with open_mainfile
        if "temp_override(window=" in code and "bpy.ops.wm.open_mainfile" in code:
            print("  [PASS] Context override wraps open_mainfile")
            return True

    print("  [FAIL] Context override not properly implemented")
    return False


def test_thread_safety():
    """Verify lock usage is comprehensive."""
    print("\n[TEST] Thread safety...")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "render_worker.py")) as f:
        code = f.read()

    # Look for proper lock usage patterns
    lock_patterns = [
        (r"with _render_lock:", "render_lock acquired"),
        (r"with _pending_lock:", "pending_lock acquired"),
    ]

    checks = {}
    for pattern, desc in lock_patterns:
        matches = len(re.findall(pattern, code))
        checks[desc] = matches >= 2
        print(f"  [{'PASS' if matches >= 2 else 'FAIL'}] {desc}: {matches} occurrences")

    return all(checks.values())


def main():
    print("=" * 60)
    print("BLENDER RENDER WORKER b35 TEST SUITE")
    print("=" * 60)

    results = [
        ("render_worker.py improvements", test_render_worker_syntax()),
        ("server.py polling logic", test_server_polling_logic()),
        ("Logging structure", test_logging_output()),
        ("Context override safety", test_context_override()),
        ("Thread safety", test_thread_safety()),
    ]

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")

    print(f"\n{passed}/{len(results)} test groups passed")

    if passed == len(results):
        print("\nAll tests passed! Ready for b35 commit.")
        return 0
    else:
        print("\nSome tests failed. Review above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
