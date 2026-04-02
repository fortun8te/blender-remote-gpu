# Progressive Multi-Tier Pass Streaming — Implementation Guide

## Architecture Overview

Progressive pass streaming enables the client compositor to begin working **while renders are still completing**. Instead of waiting for full EXR, passes stream in priority order:

### Delivery Tiers

| Tier | Time | Passes | Use Case | Size |
|------|------|--------|----------|------|
| **1** | 0-2s | Beauty (JPEG) | Viewport preview | ~10 MB |
| **2** | 2-10s | Normal, Depth, Diffuse | Color correction, depth effects | ~30 MB |
| **3** | 10-30s | Glossy, Specular, Shadow, AO, Emission | Material breakdown | ~50 MB |
| **4** | 30-40s | Cryptomatte + metadata | Advanced keying | ~5 MB |

## File Structure

### Server-Side (Python)

```
server/
├── pass_streamer.py         # Tier scheduling & pass extraction
├── pass_delivery.py         # WebSocket delivery & encoding
└── server.py (updated)      # Integrate PassDeliveryManager
```

**Key Classes:**
- `PassStreamer`: Manages priority scheduling of passes. Extracts passes from EXR, schedules delivery by tier, calls `on_pass_ready` callback.
- `PassDeliveryManager`: Sends passes over WebSocket via `PASS_DATA` (0x24) and `PASS_COMPLETE` (0x25) messages.
- `PassEncoder`: Handles compression (JPEG for beauty, PNG for float data, JSON for Cryptomatte).

### Client-Side (Blender Python)

```
addon/
├── pass_receiver.py         # Message handler & caching
├── compositor.py            # Live compositor updates
└── connection.py (updated)  # Route PASS_DATA messages
```

**Key Classes:**
- `PassReceiver`: Caches passes, triggers compositor updates, provides UI status.
- `LiveCompositor`: Maps compositor nodes to passes, re-evaluates graph on arrival.
- `PassArrivalEvent`: Event object passed to compositor.

## Protocol Messages

### PASS_DATA (0x24) — Individual Pass Arrival

**Client ← Server**

```python
{
    "type": 0x24,
    "pass_name": "Normal",              # str
    "channel_count": 3,                 # 1, 3, or 4
    "width": 1920,                      # px
    "height": 1080,                     # px
    "data_format": "PNG",               # "RAW_FLOAT", "RAW_INT", "JPEG", "PNG"
    "tier": 2,                          # PassTier.value (1-4)
    "timestamp": 2.5,                   # seconds since render start
    "binary_len": 5242880               # binary payload size
}
[binary: pass_data bytes]
```

### PASS_COMPLETE (0x25) — All Passes Delivered

**Client ← Server**

```python
{
    "type": 0x25,
    "total_passes": 12,                 # Total passes delivered
    "total_bandwidth_mb": 95.0,         # Cumulative bandwidth
}
```

## Server Integration Steps

### 1. Import Pass Modules (server.py)

```python
from server.pass_streamer import PassStreamer, PassInfo
from server.pass_delivery import PassDeliveryManager, PassEncoder
```

### 2. Add Async Pass Streaming to Render Handler

In `_handle_render_start()`, after rendering completes:

```python
# Existing: render finishes and produces EXR
image_data = await self.final_renderer.render_final(...)

# NEW: Start progressive pass streaming
delivery_mgr = PassDeliveryManager(ws)
streamer = PassStreamer(
    on_pass_ready=lambda pass_info: asyncio.create_task(
        delivery_mgr.deliver_pass(pass_info)
    )
)

# Encode beauty pass immediately (tier 1)
beauty_jpeg = await PassEncoder.encode_beauty_to_jpeg(
    beauty_rgb_data, resolution[0], resolution[1]
)
beauty_pass = PassInfo(
    name="Beauty",
    channels=3,
    width=resolution[0],
    height=resolution[1],
    format="JPEG",
    tier=PassTier.BEAUTY_PREVIEW,
    data=beauty_jpeg
)
await delivery_mgr.deliver_pass(beauty_pass)

# Start async streaming of remaining passes
streamer.start_render()
asyncio.create_task(
    streamer.stream_passes_async(exr_output_path)
)
```

### 3. Handle EXR Pass Extraction

Implement `_extract_passes_from_exr()` in `pass_streamer.py`:

```python
async def _extract_passes_from_exr(self, exr_path: str) -> list[PassInfo]:
    """Extract all passes from EXR using OpenEXR library."""
    import OpenEXR
    import Imath

    exr_file = OpenEXR.InputFile(exr_path)
    header = exr_file.header()

    passes = []
    for channel_name in exr_file.channels(['R', 'G', 'B', ...]):
        # Parse channel into PassInfo
        # Detect tier based on pass name
        pass_info = PassInfo(...)
        passes.append(pass_info)

    return passes
```

## Client Integration Steps

### 1. Import Pass Modules (connection.py)

```python
from addon.pass_receiver import PassReceiver
from shared.protocol import MsgType
```

### 2. Route PASS_DATA Messages in Connection Handler

In the WebSocket message loop:

```python
# Existing message routing
if msg_type == MsgType.FRAME_VIEWPORT:
    # ... handle viewport frames

elif msg_type == MsgType.PASS_DATA:
    # NEW: Handle incoming pass
    pass_receiver.on_pass_data(
        pass_name=data["pass_name"],
        channel_count=data["channel_count"],
        width=data["width"],
        height=data["height"],
        data_format=data["data_format"],
        data=binary,
        tier=data.get("tier", 0)
    )

elif msg_type == MsgType.PASS_COMPLETE:
    # NEW: All passes delivered
    pass_receiver.on_pass_complete(
        total_passes=data["total_passes"],
        total_bandwidth_mb=data["total_bandwidth_mb"]
    )
```

### 3. Initialize PassReceiver on Scene Load

```python
def __init__(self):
    self.pass_receiver = PassReceiver(
        scene=bpy.context.scene,
        on_status_changed=self._on_pass_status_changed
    )

def _on_pass_status_changed(self, status: dict):
    """Called when new pass arrives or status changes."""
    print(f"{status['message']} ({status['progress_percent']}%)")

    # Update viewport UI if needed
    if hasattr(bpy.context, 'window_manager'):
        bpy.context.window_manager.progress_begin(
            0, status['expected_passes'] or 1
        )
        bpy.context.window_manager.progress_update(status['passes_received'])
```

## UI Integration — "Passes Arriving" Status

Add to viewport UI (engine.py or panel):

```python
def draw_pass_status(self, layout):
    """Show pass arrival progress in viewport."""
    status = self.pass_receiver.get_pass_status()

    if status['passes_received'] > 0:
        row = layout.row()
        row.label(
            text=f"Passes: {status['passes_received']}/{status['expected_passes']}",
            icon="RENDER_RESULT"
        )
        row.label(text=f"{status['total_bandwidth_mb']:.1f} MB")

        if status['complete']:
            layout.label(text="Complete", icon="CHECKMARK")
```

## Testing Workflow

### Test 1: Server-Side Pass Extraction
```python
# In server console
from server.pass_streamer import PassStreamer
streamer = PassStreamer()
streamer.start_render()
passes = asyncio.run(streamer.stream_passes_async("render_output.exr"))
```

### Test 2: Pass Delivery Timing
```python
# Monitor server logs for pass delivery timeline
# Expected output:
# Pass Beauty delivered at 0.5s (tier 1)
# Pass Normal delivered at 3.2s (tier 2)
# Pass Depth delivered at 4.1s (tier 2)
# Pass Cryptomatte delivered at 35.8s (tier 4)
# Pass delivery complete: 12 passes, 95.0 MB
```

### Test 3: Compositor Live Updates
```python
# In Blender, set up compositor nodes with pass inputs
# Render with progressive streaming enabled
# Watch compositor graph update as passes arrive (no re-render needed)
# Check "Passes arriving: 2/12" status in viewport
```

## Performance Expectations

### Bandwidth Optimization
- Beauty JPEG: ~10 MB (84% smaller than 16-bit EXR)
- Float passes PNG: 40-60% compression vs. raw
- Total ~95 MB instead of ~150 MB full EXR

### Timeline (1920×1080, 128 samples)
- 0.0s: Render starts
- 2.0s: Beauty JPEG arrives → viewport updates
- 8.0s: Normal + Depth arrive → compositor active
- 10.0s: Diffuse arrives → color correction active
- 30.0s: Full passes available → material breakdown
- 35.0s: Cryptomatte available → keying unlocked

### Compositor Efficiency
- Only re-evaluates nodes affected by new pass (not full graph)
- Multiple passes per update possible (async arrival)
- Zero additional render passes needed

## Future Enhancements

1. **Adaptive Tier Prioritization**: Client-side hints (e.g., "user only cares about Cryptomatte")
2. **Pass Prefetching**: Predict which passes will be needed, prioritize accordingly
3. **Lossy/Lossless Toggle**: User preference for compression vs. quality
4. **Partial Pass Updates**: Progressive refinement within a tier (low-res → full-res)
5. **Pass Validation**: CRC checks for data integrity
6. **Server-Side Caching**: Cache encoded passes between renders of same scene

## Troubleshooting

### No passes arriving
- Check server logs for "Pass delivery error"
- Verify EXR file has expected layers
- Confirm WebSocket connection is open

### Compositor not updating
- Check that LiveCompositor mapped nodes correctly
- Verify pass names match compositor node expectations
- Enable debug logging: `logging.getLogger("remote-gpu").setLevel(logging.DEBUG)`

### High bandwidth usage
- Check JPEG quality setting (default 85%)
- Verify float passes are PNG-encoded (not raw)
- Consider lower tier limits if bandwidth is critical

## References

- **Protocol Definition**: `shared/protocol.py` (MsgType.PASS_DATA, PASS_COMPLETE)
- **Pass Scheduling**: `server/pass_streamer.py` (PassTier, PASS_SCHEDULE)
- **WebSocket Delivery**: `server/pass_delivery.py` (PassDeliveryManager)
- **Client Caching**: `addon/pass_receiver.py` (PassReceiver)
- **Compositor Integration**: `addon/compositor.py` (LiveCompositor)
