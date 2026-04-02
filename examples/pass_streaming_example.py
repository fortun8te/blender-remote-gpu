"""Example: Progressive pass streaming integration in render handler.

Shows how to integrate PassStreamer and PassDeliveryManager into
the existing _handle_render_start() flow.

This is a reference implementation — adapt to your specific server code.
"""

import asyncio
import logging
from pathlib import Path

# These would be imported from your server modules
# from server.pass_streamer import PassStreamer, PassInfo, PassTier
# from server.pass_delivery import PassDeliveryManager, PassEncoder
# from shared.protocol import MsgType

logger = logging.getLogger("example")


async def handle_render_start_with_progressive_passes(
    ws,
    blend_path: str,
    resolution: tuple,
    samples: int,
    output_format: str = "EXR"
):
    """Example render handler with progressive pass streaming.

    Workflow:
    1. Render final image (produces EXR with all AOVs)
    2. Extract beauty pass, JPEG-encode immediately
    3. Send beauty as PASS_DATA (tier 1) — viewport updates
    4. Spawn async task to stream remaining passes by tier
    5. Send PASS_COMPLETE when done
    """

    # ============================================================================
    # PHASE 1: RENDER (existing code)
    # ============================================================================

    logger.info(f"Starting render: {resolution[0]}x{resolution[1]}, {samples} samples")

    # Existing render logic
    # image_data = await final_renderer.render_final(
    #     blend_path, resolution, samples, output_format,
    #     denoiser_type="OFF"
    # )
    # exr_output_path = "/path/to/render_output.exr"

    # For this example, assume render succeeded
    exr_output_path = "/tmp/render_output.exr"
    resolution_actual = resolution

    # ============================================================================
    # PHASE 2: BEAUTY PREVIEW (Tier 1, 0-2s)
    # ============================================================================

    logger.info("Tier 1: Streaming beauty preview...")

    # In real code, extract beauty RGB from EXR
    # For example:
    #   import OpenEXR
    #   exr = OpenEXR.InputFile(exr_output_path)
    #   beauty_rgb = exr.channel('R', 'G', 'B')
    beauty_rgb = b"\x00" * (resolution[0] * resolution[1] * 3)  # Placeholder

    # Encode to JPEG immediately
    # beauty_jpeg = await PassEncoder.encode_beauty_to_jpeg(
    #     beauty_rgb, resolution[0], resolution[1], quality=85
    # )
    beauty_jpeg = b"\xff\xd8\xff..."  # Placeholder JPEG

    # Create PassInfo and deliver
    # beauty_pass = PassInfo(
    #     name="Beauty",
    #     channels=3,
    #     width=resolution[0],
    #     height=resolution[1],
    #     format="JPEG",
    #     tier=PassTier.BEAUTY_PREVIEW,
    #     data=beauty_jpeg,
    #     timestamp=time.time()
    # )
    #
    # delivery_mgr = PassDeliveryManager(ws)
    # await delivery_mgr.deliver_pass(beauty_pass)

    logger.info(f"Beauty sent: {len(beauty_jpeg) / 1024 / 1024:.1f} MB JPEG")

    # ============================================================================
    # PHASE 3: ASYNC PASS STREAMING (Tiers 2-4, async)
    # ============================================================================

    logger.info("Starting async pass streaming (tiers 2-4)...")

    # Spawn background task to stream remaining passes
    # This allows function to return to client ASAP while passes continue flowing
    # asyncio.create_task(
    #     _stream_remaining_passes_async(
    #         ws, exr_output_path, resolution_actual
    #     )
    # )

    logger.info("Render handler returning; passes streaming in background")
    return True


async def _stream_remaining_passes_async(
    ws,
    exr_path: str,
    resolution: tuple
):
    """Stream remaining passes asynchronously (tiers 2-4).

    Runs in background after beauty is sent.
    Respects tier scheduling to avoid overwhelming client.

    Args:
        ws: WebSocket connection
        exr_path: Path to rendered EXR file
        resolution: Image resolution
    """

    # Initialize components
    # streamer = PassStreamer()
    # delivery_mgr = PassDeliveryManager(ws)
    # encoder = PassEncoder()

    # streamer.start_render()

    logger.info("Async pass streaming started")

    try:
        # Extract all passes from EXR
        # passes = await streamer._extract_passes_from_exr(exr_path)
        # passes_sorted = streamer._sort_passes_by_tier(passes)

        # For example, simulate pass streaming:
        simulated_passes = [
            {
                "name": "Normal",
                "channels": 3,
                "tier": 2,
                "delay": 3.0,  # seconds from render completion
                "size_mb": 12,
            },
            {
                "name": "Depth",
                "channels": 1,
                "tier": 2,
                "delay": 4.0,
                "size_mb": 8,
            },
            {
                "name": "Diffuse Color",
                "channels": 3,
                "tier": 2,
                "delay": 6.0,
                "size_mb": 15,
            },
            {
                "name": "Glossy",
                "channels": 3,
                "tier": 3,
                "delay": 15.0,
                "size_mb": 15,
            },
            {
                "name": "Specular",
                "channels": 3,
                "tier": 3,
                "delay": 18.0,
                "size_mb": 12,
            },
            {
                "name": "Cryptomatte",
                "channels": 3,
                "tier": 4,
                "delay": 35.0,
                "size_mb": 5,
            },
        ]

        start_time = asyncio.get_event_loop().time()

        # Deliver each pass on schedule
        for pass_info in simulated_passes:
            # Wait for scheduled delivery time
            elapsed = asyncio.get_event_loop().time() - start_time
            wait_time = max(0, pass_info["delay"] - elapsed)

            logger.info(
                f"Tier {pass_info['tier']}: {pass_info['name']} "
                f"will arrive in {wait_time:.1f}s"
            )

            await asyncio.sleep(wait_time)

            logger.info(
                f"Delivering {pass_info['name']} "
                f"({pass_info['size_mb']} MB, tier {pass_info['tier']})"
            )

            # In real code:
            # pass_info_obj = PassInfo(...)
            # await delivery_mgr.deliver_pass(pass_info_obj)

        # Signal completion
        logger.info("Sending PASS_COMPLETE")
        # await delivery_mgr._send_pass_complete()

    except Exception as e:
        logger.error(f"Pass streaming error: {e}")
    finally:
        logger.info("Async pass streaming finished")


async def example_full_workflow():
    """Complete example workflow: render → progressive passes → compositor update."""

    logger.basicConfig(level=logging.INFO)

    logger.info("=== Progressive Pass Streaming Example ===\n")

    # Step 1: Render
    logger.info("STEP 1: Rendering...")
    logger.info("  Scene: Cornell Box")
    logger.info("  Resolution: 1920x1080")
    logger.info("  Samples: 128")
    logger.info("  Estimated render time: 45 seconds")

    await asyncio.sleep(1)  # Simulate render
    logger.info("  [RENDER COMPLETE]")

    # Step 2: Beauty preview (tier 1)
    logger.info("\nSTEP 2: Beauty preview (tier 1)")
    logger.info("  Encoding beauty RGB → JPEG (quality 85%)")
    logger.info("  JPEG: 10 MB (15x smaller than 16-bit)")

    await asyncio.sleep(0.5)
    logger.info("  [SENT] Beauty JPEG")
    logger.info("  [CLIENT] Viewport displays preview")

    # Step 3: Essential passes (tier 2)
    logger.info("\nSTEP 3: Essential passes (tier 2, async)")
    tier2_passes = [
        ("Normal", 3, 12),      # (name, channels, size_mb)
        ("Depth", 1, 8),
        ("Diffuse Color", 3, 15),
    ]

    for pass_name, channels, size in tier2_passes:
        await asyncio.sleep(2)
        logger.info(f"  [SENT] {pass_name} ({channels}ch, {size} MB)")
        logger.info(f"  [CLIENT] Compositor updated: {pass_name} input")

    # Step 4: Full passes (tier 3)
    logger.info("\nSTEP 4: Full passes (tier 3)")
    tier3_passes = ["Glossy", "Specular", "Shadow", "AO", "Emission"]

    for pass_name in tier3_passes:
        await asyncio.sleep(2)
        logger.info(f"  [SENT] {pass_name}")
        logger.info(f"  [CLIENT] Material breakdown update")

    # Step 5: Cryptomatte (tier 4)
    logger.info("\nSTEP 5: Cryptomatte (tier 4)")
    await asyncio.sleep(3)
    logger.info("  [SENT] Cryptomatte ID + metadata")
    logger.info("  [CLIENT] Keying/selection tools enabled")

    # Step 6: Complete
    logger.info("\nSTEP 6: Completion")
    logger.info("  [SENT] PASS_COMPLETE (12 passes, 95 MB)")
    logger.info("  [CLIENT] All passes cached, compositor fully updated")

    logger.info("\n=== Summary ===")
    logger.info("Total time: 35 seconds (vs. 45s for full EXR wait)")
    logger.info("Compositor active: After 6 seconds (10x faster)")
    logger.info("Bandwidth saved: 15 MB (full EXR would be ~150 MB)")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )

    # Run example
    asyncio.run(example_full_workflow())
