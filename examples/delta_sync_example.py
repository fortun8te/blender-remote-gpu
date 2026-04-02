"""Example: Using incremental scene synchronization (delta sync).

Demonstrates how the delta sync system works in the Blender addon.
This is a reference implementation — actual code is in addon/engine.py.

Key concepts:
1. Snapshots capture scene state (transforms, materials, lights)
2. Deltas are computed by comparing snapshots
3. Small deltas (~1KB) are sent instead of full .blend (~100MB)
4. Server applies deltas incrementally or requests full resync if needed
"""

# ============================================================================
# Example 1: Basic Delta Sync Flow
# ============================================================================

def example_basic_delta_sync():
    """Simplified flow showing delta sync in action."""
    import bpy
    from addon.sync import initialize_scene_tracker, compute_scene_delta, get_delta_metrics
    from shared.protocol import MsgType

    # Step 1: Initial scene sync (full .blend)
    print("=" * 60)
    print("STEP 1: Initial Full Scene Sync")
    print("=" * 60)

    blend_data = b"<full .blend file ~100MB>"
    print(f"Sending full scene: {len(blend_data)} bytes")

    # Simulate network send
    # conn.send_scene(blend_data)

    # Step 2: Baseline snapshot captured
    print("\nSTEP 2: Capture Baseline Snapshot")
    print("=" * 60)

    initialize_scene_tracker(bpy.context)
    print("Snapshot captured:")
    print("  - Cube @ (0, 0, 0)")
    print("  - Light @ (5, 5, 5)")
    print("  - Camera @ (10, 10, 10)")

    # Step 3: User makes a small change
    print("\nSTEP 3: User Moves Cube 5 Units in X")
    print("=" * 60)

    # Simulate object movement in Blender
    # (In real Blender: user moves object in viewport)

    # Step 4: Compute delta
    print("\nSTEP 4: Compute Delta")
    print("=" * 60)

    delta = compute_scene_delta(bpy.context, max_delta_bytes=1_000_000)

    if delta:
        print(f"Delta computed:")
        print(f"  - Modified objects: {len(delta.modified_objects)}")
        print(f"  - Deleted objects: {len(delta.deleted_objects)}")
        print(f"  - New objects: {len(delta.new_objects)}")
        print(f"  - Size estimate: {delta.size_estimate()} bytes")
        print(f"  - Full resync required: {delta.full_resync_required}")

        if not delta.full_resync_required:
            # Step 5: Send small delta
            print("\nSTEP 5: Send Minimal Delta")
            print("=" * 60)

            delta_dict = delta.to_dict()
            print(f"Sending delta message:")
            print(f"  {delta_dict}")

            # Simulate network send
            # conn.send(MsgType.SCENE_DELTA, delta_dict)

            # Step 6: Display bandwidth savings
            print("\nSTEP 6: Bandwidth Savings")
            print("=" * 60)

            saved = delta.bandwidth_saved_bytes
            delta_size = delta.size_estimate()
            full_size = 100_000_000  # 100MB typical .blend

            print(f"Full scene size: {full_size / 1024 / 1024:.1f} MB")
            print(f"Delta size: {delta_size} bytes")
            print(f"Bandwidth saved: {saved / 1024 / 1024:.1f} MB (~{100 * saved / full_size:.0f}%)")

    # Step 7: Check metrics
    print("\nSTEP 7: Performance Metrics")
    print("=" * 60)

    metrics = get_delta_metrics()
    print(f"Deltas computed: {metrics['deltas_computed']}")
    print(f"Full resyncs: {metrics['full_resyncs']}")
    print(f"Total bandwidth saved: {metrics['total_bandwidth_saved'] / 1024 / 1024:.1f} MB")
    print(f"Average delta size: {metrics['average_delta_size']:.0f} bytes")


# ============================================================================
# Example 2: What Gets Synced (and What Doesn't)
# ============================================================================

def example_what_gets_synced():
    """Shows which scene changes trigger delta vs full resync."""

    print("\n" + "=" * 60)
    print("WHAT GETS SYNCED")
    print("=" * 60)

    print("\n✓ FAST PATH (Delta Sync):")
    print("  - Move/rotate/scale object")
    print("  - Change light energy/color")
    print("  - Adjust camera lens")
    print("  - Delete object")
    print("  Size: ~1-100KB per update")

    print("\n✗ REQUIRES FULL RESYNC:")
    print("  - Edit mesh (add/remove vertices)")
    print("  - Edit material shader nodes")
    print("  - Add new object")
    print("  - Sculpt or subdivide")
    print("  Size: ~100MB per resync")

    print("\n💡 WORKAROUND:")
    print("  - Edit materials locally, then hide viewport")
    print("  - Make viewport changes in viewport shading mode")
    print("  - Use modifier preview instead of final result")


# ============================================================================
# Example 3: Fallback Mechanism
# ============================================================================

def example_fallback_mechanism():
    """Shows how the system falls back to full sync when needed."""

    print("\n" + "=" * 60)
    print("FALLBACK MECHANISM")
    print("=" * 60)

    print("\nScenario 1: Delta Too Large")
    print("-" * 60)
    print("User modifies 50 objects")
    print("Delta size: 2.5MB")
    print("Max threshold: 1MB")
    print("Action: FALLBACK TO FULL SYNC")
    print("Next update: Re-baseline snapshot")

    print("\nScenario 2: Geometry Change")
    print("-" * 60)
    print("User edits mesh (adds vertices)")
    print("Delta computed: geometry=true")
    print("Server response: 'requires_full_resync'")
    print("Action: SEND FULL SCENE")
    print("Result: Mesh fully transferred")

    print("\nScenario 3: Tracker Error")
    print("-" * 60)
    print("Object deleted in Blender")
    print("Snapshot misses reference object")
    print("Delta computation fails")
    print("Action: FALLBACK TO FULL SYNC")
    print("Result: Error recovered gracefully")

    print("\nKey Property: Always Correct")
    print("-" * 60)
    print("Even if fallback occurs, scene state is always consistent.")
    print("No risk of desynchronization.")


# ============================================================================
# Example 4: Performance Characteristics
# ============================================================================

def example_performance():
    """Shows performance improvements from delta sync."""

    print("\n" + "=" * 60)
    print("PERFORMANCE IMPROVEMENTS")
    print("=" * 60)

    import time

    scenarios = [
        ("Transform only", 1, 200, 100_000_000),
        ("10 objects moved", 10, 2_000, 100_000_000),
        ("Material edit", 1, 0, 100_000_000),  # Full resync
        ("Heavy geometry edit", 100, 0, 100_000_000),  # Full resync
    ]

    print("\nScenario | # Objects | Delta Size | Full Size | Time Saved")
    print("-" * 70)

    for scenario, objs, delta_size, full_size in scenarios:
        # Estimate: 100 Mbps network = 10ms per 100MB
        delta_time = max(1, delta_size / 100 / 1024)  # ms
        full_time = full_size / 100 / 1024 / 1024 * 10  # ms

        saved = full_time - delta_time if delta_size > 0 else 0
        pct = 100 * (1 - delta_time / full_time) if full_time > 0 else 0

        status = "Δ" if delta_size > 0 else "F"
        print(
            f"{scenario:20} | {objs:3} | {delta_size:7}B | {full_size / 1024 / 1024:6.0f}MB | "
            f"{saved:6.0f}ms ({pct:5.0f}%) {status}"
        )

    print("\nΔ = Delta sync, F = Full sync (fallback)")


# ============================================================================
# Example 5: Manual Delta Inspection
# ============================================================================

def example_inspect_delta():
    """Shows how to inspect delta contents."""

    import json
    from addon.scene_diff import SceneDelta, ObjectDelta

    print("\n" + "=" * 60)
    print("INSPECTING DELTA CONTENTS")
    print("=" * 60)

    # Create example delta
    delta = SceneDelta(
        timestamp=1234567890.123,
        modified_objects=[
            ObjectDelta(
                name="Cube",
                type="MESH",
                changes={
                    "transform": [1, 0, 0, 5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
                }
            ),
            ObjectDelta(
                name="Light",
                type="LIGHT",
                changes={
                    "light": {
                        "energy": 500.0,
                        "color": [1.0, 0.8, 0.6]
                    }
                }
            ),
        ],
        deleted_objects=["OldCube"],
        new_objects=[],
        bandwidth_saved_bytes=99_990_000,
    )

    print("\nDelta as JSON:")
    print("-" * 60)
    delta_dict = delta.to_dict()
    print(json.dumps(delta_dict, indent=2))

    print("\nDelta Statistics:")
    print("-" * 60)
    print(f"Size: {delta.size_estimate()} bytes")
    print(f"Objects modified: {len(delta.modified_objects)}")
    print(f"Objects deleted: {len(delta.deleted_objects)}")
    print(f"Objects created: {len(delta.new_objects)}")
    print(f"Bandwidth saved: {delta.bandwidth_saved_bytes / 1024 / 1024:.1f} MB")


# ============================================================================
# Example 6: Metrics and Monitoring
# ============================================================================

def example_monitoring():
    """Shows how to monitor delta sync performance over time."""

    import json

    print("\n" + "=" * 60)
    print("MONITORING AND METRICS")
    print("=" * 60)

    # Simulated metrics after a work session
    metrics = {
        "deltas_computed": 127,
        "full_resyncs": 3,
        "total_delta_bytes_sent": 275000,
        "total_bandwidth_saved": 12_600_000_000,
        "average_delta_size": 2165,
    }

    print("\nSession Metrics:")
    print("-" * 60)
    print(f"Deltas computed: {metrics['deltas_computed']}")
    print(f"Full resyncs: {metrics['full_resyncs']}")
    print(f"Total delta bytes: {metrics['total_delta_bytes_sent'] / 1024:.1f} KB")
    print(f"Total bandwidth saved: {metrics['total_bandwidth_saved'] / 1024 / 1024 / 1024:.1f} GB")
    print(f"Average delta: {metrics['average_delta_size']:.0f} bytes")

    print("\nAnalysis:")
    print("-" * 60)

    # Calculate efficiency
    full_syncs_avoided = metrics['deltas_computed']
    total_sent = metrics['total_delta_bytes_sent']
    total_saved = metrics['total_bandwidth_saved']

    estimated_full_size = 100_000_000
    total_full_sent = (metrics['deltas_computed'] + metrics['full_resyncs']) * estimated_full_size
    total_actual_sent = total_sent + (metrics['full_resyncs'] * estimated_full_size)

    print(f"Estimated if no delta sync: {total_full_sent / 1024 / 1024 / 1024:.1f} GB")
    print(f"Actually sent: {total_actual_sent / 1024 / 1024 / 1024:.1f} GB")
    print(f"Efficiency: {100 * (1 - total_actual_sent / total_full_sent):.1f}%")
    print(f"Full resyncs: {metrics['full_resyncs']} ({100 * metrics['full_resyncs'] / (metrics['deltas_computed'] + metrics['full_resyncs']):.1f}%)")


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("DELTA SYNC EXAMPLES")
    print("=" * 60)

    print("\nThese examples show how incremental scene synchronization works.")
    print("Run individually or view as reference.")

    example_what_gets_synced()
    example_fallback_mechanism()
    example_performance()
    example_inspect_delta()
    example_monitoring()

    print("\n" + "=" * 60)
    print("For more information, see: DELTA_SYNC.md")
    print("=" * 60)
