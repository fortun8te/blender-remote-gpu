"""Example integration of multi-viewport support in server.

This shows how to update the existing RenderServer class to support
Phase 12 multi-viewport rendering. Add these snippets to server.py.
"""

# ============================================================================
# STEP 1: Import viewport manager at top of server.py
# ============================================================================

# from server.viewport_manager import get_viewport_manager
# from shared.protocol import MsgType


# ============================================================================
# STEP 2: Update _handle_viewport_start() to support multi-viewport
# ============================================================================

async def _handle_viewport_start_updated(self, ws, data: dict, session_id: str, frame_buffer=None):
    """Handle viewport render request with Phase 12 multi-viewport support."""
    operation_id = str(uuid.uuid4())[:12]
    logger.info(f"[{session_id}] Viewport start: op={operation_id}")

    # Phase 12: Extract multi-viewport parameters
    viewport_id = data.get("viewport_id")
    zoom_level = float(data.get("zoom_level", 100.0))

    blend_path = self.scene_manager.get_blend_path()
    if blend_path is None:
        logger.error(f"[{session_id}] No scene loaded for viewport")
        await self._send_error(ws, "SYNC_004", "No scene loaded")
        return

    # Cancel any existing viewport render for this session
    self._cancel_viewport()

    view_matrix = data.get("view_matrix")
    proj_matrix = data.get("proj_matrix")
    requested_resolution = tuple(data.get("resolution", [800, 600]))
    max_samples = data.get("samples", 128)

    # Phase 12: Register viewport and get target resolution
    from server.viewport_manager import get_viewport_manager
    mgr = get_viewport_manager()

    if viewport_id:
        vp_state = mgr.register_viewport(viewport_id, requested_resolution, zoom_level)
        target_resolution = vp_state.target_resolution
        priority = vp_state.priority

        logger.info(
            f"[{session_id}] Viewport {viewport_id}: "
            f"requested {requested_resolution}, target {target_resolution} (priority={priority})"
        )

        # Send priority confirmation to client
        await self._send(ws, MsgType.VIEWPORT_PRIORITY, {
            "viewport_id": viewport_id,
            "priority": priority,
            "target_resolution": list(target_resolution),
        })
    else:
        # Legacy single-viewport mode
        target_resolution = requested_resolution
        viewport_id = "legacy"
        priority = 0

    # Start progressive rendering in background
    self._viewport_task = asyncio.create_task(
        self._viewport_render_loop_updated(
            ws, blend_path, view_matrix, proj_matrix,
            target_resolution,  # Use target, not requested
            max_samples, operation_id, session_id, frame_buffer,
            viewport_id=viewport_id, priority=priority  # Pass to render loop
        )
    )


# ============================================================================
# STEP 3: Update _viewport_render_loop() to include viewport_id in frames
# ============================================================================

async def _viewport_render_loop_updated(
    self, ws, blend_path, view_matrix, proj_matrix,
    resolution, max_samples, operation_id, session_id, frame_buffer,
    viewport_id: str = None, priority: int = 0
):
    """Viewport render loop with Phase 12 multi-viewport support.

    Main changes:
    - Render at target_resolution (already adjusted by register_viewport)
    - Include viewport_id in frame metadata
    - Record frame stats per viewport
    """
    from server.viewport_manager import get_viewport_manager
    mgr = get_viewport_manager()

    logger.info(
        f"[{session_id}] Starting viewport render loop: "
        f"{resolution[0]}x{resolution[1]}, {max_samples} samples, "
        f"viewport_id={viewport_id}, priority={priority}"
    )

    sample = 0
    max_elapsed = 600  # 10 minute timeout

    start_time = time.time()

    try:
        while sample < max_samples:
            elapsed = time.time() - start_time

            if elapsed > max_elapsed:
                logger.warning(f"[{session_id}] Viewport timeout after {elapsed:.1f}s")
                break

            # Render next sample
            # (Assuming render_sample() is your existing render function)
            jpeg_data = await self.live_viewport.render_sample(
                view_matrix, proj_matrix, resolution, sample
            )

            if jpeg_data is None:
                logger.debug(f"[{session_id}] render_sample returned None")
                break

            # Phase 12: Include viewport_id in frame metadata
            metadata = {
                "width": resolution[0],
                "height": resolution[1],
                "sample": sample,
                "timestamp_sent": time.time(),
            }

            if viewport_id and viewport_id != "legacy":
                metadata["viewport_id"] = viewport_id

            # Send frame with metadata
            await self._send(ws, MsgType.FRAME_VIEWPORT, metadata, binary=jpeg_data)

            # Phase 12: Record stats
            if viewport_id:
                mgr.record_frame_sent(viewport_id, len(jpeg_data))

            sample += 1

            # Log periodically
            if sample % 10 == 0:
                fps = sample / (time.time() - start_time)
                logger.debug(
                    f"[{session_id}] Viewport {viewport_id}: "
                    f"sample {sample}/{max_samples}, fps={fps:.1f}"
                )

    except asyncio.CancelledError:
        logger.info(f"[{session_id}] Viewport render cancelled")
    except Exception as e:
        logger.error(f"[{session_id}] Viewport render error: {e}", exc_info=True)
    finally:
        elapsed = time.time() - start_time
        logger.info(
            f"[{session_id}] Viewport render complete: "
            f"{sample} samples in {elapsed:.1f}s, "
            f"viewport_id={viewport_id}"
        )

        # Phase 12: Unregister viewport when done
        if viewport_id and viewport_id != "legacy":
            mgr.unregister_viewport(viewport_id)


# ============================================================================
# STEP 4: Add fallback mode check in connection handler
# ============================================================================

async def handle_client_updated(self, websocket):
    """Handle client connection with fallback mode support."""
    from server.viewport_manager import get_viewport_manager
    mgr = get_viewport_manager()

    client_addr = websocket.remote_address
    session_id = str(uuid.uuid4())[:8]
    frame_buffer = ClientFrameBuffer(str(client_addr))

    logger.info(f"Client connected: {client_addr}")

    try:
        async for raw in websocket:
            if isinstance(raw, bytes):
                try:
                    msg_type, data = unpack_websocket(raw)

                    # Phase 12: Check for fallback mode before processing
                    if mgr.should_fallback_to_primary():
                        logger.warning("Server overloaded, requesting fallback mode")
                        await self._send(websocket, MsgType.FALLBACK_MODE, {
                            "primary_viewport_id": mgr.get_primary_viewport_id(),
                            "reason": "GPU overloaded with multiple viewports",
                        })
                        # Continue processing, but client will reduce secondary viewports

                    # Read binary follow-up if indicated
                    binary = None
                    if data.get("has_binary"):
                        binary = await websocket.recv()

                    await self._handle_message(websocket, msg_type, data, binary, session_id, frame_buffer)

                except Exception as e:
                    logger.error(f"Message handling error: {e}")
                    await self._send_error(websocket, "PROTO_002", "Invalid message")

    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Client disconnected: {client_addr}")
    finally:
        # Phase 12: Clean up all viewports for this session
        for vp in mgr.get_all_viewports():
            mgr.unregister_viewport(vp.viewport_id)

        logger.info(f"Client session ended: {client_addr}")


# ============================================================================
# STEP 5: Add status endpoint to report viewport state
# ============================================================================

def _get_status_updated(self) -> dict:
    """Get server status including viewport information."""
    from server.viewport_manager import get_viewport_manager
    mgr = get_viewport_manager()

    base_status = self._get_status()  # Existing status

    # Phase 12: Add viewport info
    base_status["viewports"] = mgr.get_status()

    return base_status


# ============================================================================
# INTEGRATION CHECKLIST
# ============================================================================

"""
1. Import viewport_manager and MsgType at top of server.py
   from server.viewport_manager import get_viewport_manager

2. Update _handle_viewport_start() to:
   - Extract viewport_id and zoom_level from data
   - Call mgr.register_viewport() to get target_resolution
   - Send VIEWPORT_PRIORITY message back to client
   - Pass viewport_id to _viewport_render_loop()

3. Update _viewport_render_loop() to:
   - Include viewport_id in frame metadata
   - Call mgr.record_frame_sent() for stats
   - Call mgr.unregister_viewport() in finally block

4. Update handle_client() to:
   - Check mgr.should_fallback_to_primary() after each message
   - Send FALLBACK_MODE if needed
   - Clean up all viewports in finally block

5. Test:
   - Open 1 viewport: works as before
   - Open 2 viewports: both render, P1 at 50% resolution
   - Open 3 viewports: P0 full, P1-2 at 50%
   - Open 4 viewports: P0 full, P1-3 at varying resolutions
   - Resize viewport: resolution updates
   - Close viewport: priorities reassign
   - Fallback mode: only P0 continues rendering
"""
