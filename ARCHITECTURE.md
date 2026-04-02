# Progressive Pass Streaming Architecture

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              RENDER SERVER                                   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ _handle_render_start()                                               │  │
│  │  ├─ 1. Run Blender render → EXR (all AOVs)                          │  │
│  │  ├─ 2. Extract Beauty RGB                                           │  │
│  │  ├─ 3. JPEG encode (10MB) → PassInfo                               │  │
│  │  └─ 4. Create PassDeliveryManager & PassStreamer                   │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    │                                         │
│  ┌─────────────────────────────────▼──────────────────────────────────┐   │
│  │ PassDeliveryManager                                                 │   │
│  │ ├─ deliver_pass(PassInfo)                                          │   │
│  │ │  ├─ Pack PASS_DATA (0x24) msgpack header                        │   │
│  │ │  ├─ Add binary payload (compressed pass data)                   │   │
│  │ │  └─ Send via WebSocket                                          │   │
│  │ └─ _send_pass_complete()                                           │   │
│  │    └─ Send PASS_COMPLETE (0x25) signal                            │   │
│  │                                                                     │   │
│  │ PassEncoder (static utility methods)                               │   │
│  │ ├─ encode_beauty_to_jpeg()    → 10x compression                   │   │
│  │ ├─ encode_float_to_png()      → 40-60% compression               │   │
│  │ └─ encode_cryptomatte_metadata() → JSON                           │   │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    │                                         │
│  ┌─────────────────────────────────▼──────────────────────────────────┐   │
│  │ PassStreamer (async task)                                          │   │
│  │                                                                     │   │
│  │ start_render()                                                     │   │
│  │ ├─ Record T=0 (render completion time)                            │   │
│  │                                                                     │   │
│  │ stream_passes_async(exr_path)                                     │   │
│  │ ├─ _extract_passes_from_exr() → [PassInfo, ...]                  │   │
│  │ │  ├─ Parse OpenEXR headers                                      │   │
│  │ │  ├─ Read all layer/channel data                                │   │
│  │ │  └─ Detect Cryptomatte metadata                                │   │
│  │ │                                                                  │   │
│  │ ├─ _sort_passes_by_tier()                                        │   │
│  │ │  └─ Order by PassTier (1→2→3→4), then schedule window         │   │
│  │ │                                                                  │   │
│  │ └─ For each pass:                                                │   │
│  │    ├─ Look up PASS_SCHEDULE[pass_name]                          │   │
│  │    │  └─ Get (tier, window_start, window_end)                   │   │
│  │    │                                                              │   │
│  │    ├─ _deliver_pass_on_schedule(pass_info)                      │   │
│  │    │  ├─ Calculate wait_time = max(0, window_start - elapsed)  │   │
│  │    │  ├─ await asyncio.sleep(wait_time)                       │   │
│  │    │  ├─ on_pass_ready callback → delivery_mgr.deliver_pass() │   │
│  │    │  └─ Record delivery_times[pass_name]                      │   │
│  │    │                                                              │   │
│  │    └─ Repeat for next pass...                                   │   │
│  │                                                                     │   │
│  │ PASS_SCHEDULE (constants):                                        │   │
│  │ {                                                                  │   │
│  │   "Beauty":         (Tier 1, 0s,  2s)                           │   │
│  │   "Normal":         (Tier 2, 2s,  8s)                           │   │
│  │   "Depth":          (Tier 2, 2s,  8s)                           │   │
│  │   "Diffuse Color":  (Tier 2, 2s, 10s)                          │   │
│  │   "Glossy":         (Tier 3, 10s, 20s)                         │   │
│  │   "Specular":       (Tier 3, 10s, 25s)                         │   │
│  │   "Shadow":         (Tier 3, 15s, 30s)                         │   │
│  │   "AO":             (Tier 3, 15s, 30s)                         │   │
│  │   "Emission":       (Tier 3, 20s, 30s)                         │   │
│  │   "Cryptomatte":    (Tier 4, 30s, 40s)                         │   │
│  │ }                                                                  │   │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│                          WebSocket Stream ↓↓↓                              │
└─────────────────────────────────────────────────────────────────────────────┘

          [PASS_DATA(Beauty)]      [PASS_DATA(Normal)]      ...
          [PASS_COMPLETE]
                 ↓↓↓                        ↓↓↓

┌─────────────────────────────────────────────────────────────────────────────┐
│                              BLENDER CLIENT                                  │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ connection.py (WebSocket handler)                                    │  │
│  │                                                                       │  │
│  │ async for msg in websocket:                                         │  │
│  │   msg_type, data, binary = unpack_websocket(msg)                   │  │
│  │                                                                       │  │
│  │   if msg_type == PASS_DATA:                                        │  │
│  │     ├─ Extract metadata:                                           │  │
│  │     │  ├─ pass_name, channel_count, width, height               │  │
│  │     │  ├─ data_format (JPEG/PNG/RAW), tier                     │  │
│  │     │  └─ timestamp (seconds since render start)                │  │
│  │     │                                                              │  │
│  │     └─ pass_receiver.on_pass_data(                               │  │
│  │        pass_name, channels, w, h, fmt, binary, tier)            │  │
│  │                                                                    │  │
│  │   elif msg_type == PASS_COMPLETE:                               │  │
│  │     └─ pass_receiver.on_pass_complete(total_passes, bw_mb)     │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    │                                         │
│  ┌─────────────────────────────────▼──────────────────────────────────┐   │
│  │ PassReceiver                                                        │   │
│  │                                                                     │   │
│  │ _pass_cache: {pass_name → raw_bytes}                             │   │
│  │ _pass_metadata: {pass_name → {w, h, channels, format, ...}}     │   │
│  │                                                                     │   │
│  │ on_pass_data(pass_name, ...):                                    │   │
│  │ ├─ Store in _pass_cache[pass_name] = data_bytes                │   │
│  │ ├─ Store metadata in _pass_metadata[pass_name]                 │   │
│  │ ├─ Record arrival_times[pass_name] = elapsed_time             │   │
│  │ ├─ Accumulate _total_bandwidth += len(data)                   │   │
│  │ │                                                                │   │
│  │ ├─ Create PassArrivalEvent(pass_name, data, channels, ...)   │   │
│  │ │                                                                │   │
│  │ ├─ if compositor:                                              │   │
│  │ │  └─ compositor.on_pass_arrival(event)                       │   │
│  │ │                                                                │   │
│  │ └─ _update_ui_status()                                         │   │
│  │    ├─ Calculate progress_percent                              │   │
│  │    └─ Call on_status_changed(status_dict)                    │   │
│  │       └─ Update viewport UI: "Passes arriving: 2/12"         │   │
│  │                                                                    │   │
│  │ on_pass_complete(total_passes, bw_mb):                         │   │
│  │ ├─ Mark delivery as complete                                  │   │
│  │ ├─ Log summary statistics                                    │   │
│  │ └─ Update UI: "Complete" ✓                                   │   │
│  │                                                                     │   │
│  │ get_pass_data(pass_name) → bytes or None                      │   │
│  │ get_pass_status() → full diagnostic info                      │   │
│  │ clear_passes() → reset for new render                         │   │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    │                                         │
│  ┌─────────────────────────────────▼──────────────────────────────────┐   │
│  │ LiveCompositor                                                      │   │
│  │                                                                     │   │
│  │ _pass_cache: {pass_name → {width, height, channels, format}} │   │
│  │ _pass_node_map: {pass_name → [CompositorNode, ...]}         │   │
│  │ _eval_count: int (incremental re-evaluations)                │   │
│  │                                                                     │   │
│  │ __init__(scene):                                                │   │
│  │ ├─ Scan compositor node tree                                  │   │
│  │ ├─ Build pass → node dependency map                          │   │
│  │ └─ Example: {"Normal" → [node_ColorRamp, node_Viewer]}     │   │
│  │                                                                     │   │
│  │ on_pass_arrival(event: PassArrivalEvent):                     │   │
│  │ ├─ Cache pass data in _pass_cache[event.pass_name]          │   │
│  │ ├─ Look up affected_nodes = _pass_node_map[pass_name]       │   │
│  │ └─ _update_affected_nodes(pass_name, affected_nodes)        │   │
│  │    ├─ For each node:                                        │   │
│  │    │  └─ _update_node_input(node, pass_name)               │   │
│  │    │     ├─ Create temp image from pass_data                │   │
│  │    │     └─ Connect to node input socket                    │   │
│  │    │                                                          │   │
│  │    └─ _evaluate_compositor()                                │   │
│  │       └─ scene.node_tree.update()                           │   │
│  │          (Blender re-evaluates affected nodes only)         │   │
│  │                                                                    │   │
│  │ get_pass_status() → diagnostic info                           │   │
│  │ clear_passes() → reset for new render                         │   │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  Viewport UI Updates:                                                       │
│  ├─ T=2s:  "Passes arriving: 1/12 (Beauty)" + preview rendering   │  │
│  ├─ T=8s:  "Passes arriving: 3/12 (Normal, Depth, Diffuse)"       │  │
│  │           Compositor active, color correction available         │  │
│  ├─ T=30s: "Passes arriving: 10/12 (all materials)"               │  │
│  │           Full material breakdown                              │  │
│  ├─ T=35s: "Passes arriving: 12/12 ✓"                           │  │
│  │           Keying/selection tools unlocked                     │  │
│  └─ All passes available for export/further work                  │  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow Timeline

```
TIME │ SERVER                          │ NETWORK             │ CLIENT
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
 0s  │ Render completes                │                     │
     │ EXR with 12 AOVs ready          │                     │
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
 0s  │ Extract Beauty RGB              │                     │
     │ Encode to JPEG (10MB)           │                     │
     │ Pack PASS_DATA message          │                     │
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
0.5s │                                 │ PASS_DATA(Beauty)→  │ Receive Beauty
     │                                 │                     │ Cache + Compositor
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
1s   │ Start PassStreamer async task   │                     │ Viewport shows
     │                                 │                     │ JPEG preview
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
2s   │ Extract Normal (3 channels)     │                     │
     │ Compress to PNG                 │                     │
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
3s   │                                 │ PASS_DATA(Normal)→  │ Normal cached
     │                                 │                     │ Compositor nodes
     │                                 │                     │ updated
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
4s   │ Extract Depth (1 channel)       │                     │ Status: 2/12
     │ Compress to PNG                 │                     │
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
5s   │                                 │ PASS_DATA(Depth)→   │ Depth cached
     │                                 │                     │ Depth blur effects
     │                                 │                     │ available
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
6s   │ Extract Diffuse Color           │                     │ Status: 3/12
     │                                 │                     │
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
7s   │                                 │ PASS_DATA(Diffuse)→ │ Diffuse cached
     │                                 │                     │ Color correction
     │                                 │                     │ FULLY ACTIVE
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
10s  │ Extract Glossy, Specular        │                     │
15s  │                                 │ PASS_DATA(Glossy)→  │ Material breakdown
20s  │ Extract Shadow, AO, Emission    │ PASS_DATA(Specular) │ Status: 7/12
     │                                 │ PASS_DATA(Shadow)→  │
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
25s  │                                 │ PASS_DATA(AO)→      │ Full passes
     │                                 │ PASS_DATA(Emission) │ cached
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
30s  │ Extract Cryptomatte             │                     │
35s  │ Parse Cryptomatte metadata JSON │ PASS_DATA(Crypto)→  │ Keying/selection
     │                                 │ PASS_COMPLETE→      │ tools unlocked
─────┼─────────────────────────────────┼─────────────────────┼──────────────────
35s+ │ All passes streamed             │                     │ Status: COMPLETE ✓
     │ (12 passes total)               │                     │ All AOVs available
```

## Class Relationships

```
SERVER SIDE:
    PassStreamer
    ├─ PASS_SCHEDULE: dict
    ├─ start_render()
    ├─ stream_passes_async(exr_path)
    ├─ _extract_passes_from_exr(exr_path)
    ├─ _sort_passes_by_tier(passes)
    ├─ _deliver_pass_on_schedule(pass_info)
    └─ get_delivery_summary()

    PassInfo (dataclass)
    ├─ name: str
    ├─ channels: int
    ├─ width: int
    ├─ height: int
    ├─ format: str
    ├─ tier: PassTier
    ├─ data: bytes
    └─ timestamp: float

    PassDeliveryManager
    ├─ deliver_pass(pass_info)
    ├─ _send_pass_complete()
    ├─ _send(msg_type, data, binary)
    └─ get_delivery_stats()

    PassEncoder (static utilities)
    ├─ encode_beauty_to_jpeg()
    ├─ encode_float_to_png()
    └─ encode_cryptomatte_metadata()

CLIENT SIDE:
    PassReceiver
    ├─ on_pass_data(pass_name, ...)
    ├─ on_pass_complete(total_passes, bw_mb)
    ├─ get_pass_data(pass_name)
    ├─ get_pass_status()
    ├─ _update_ui_status()
    └─ clear_passes()

    LiveCompositor
    ├─ on_pass_arrival(event)
    ├─ _scan_compositor_nodes()
    ├─ _update_affected_nodes(pass_name, nodes)
    ├─ _update_node_input(node, pass_name)
    ├─ _evaluate_compositor()
    ├─ get_pass_status()
    └─ clear_passes()

    PassArrivalEvent (dataclass)
    ├─ pass_name: str
    ├─ pass_data: bytes
    ├─ channels: int
    ├─ width: int
    ├─ height: int
    ├─ data_format: str
    └─ timestamp: float

    CompositorNode (dataclass)
    ├─ node_name: str
    ├─ socket_index: int
    ├─ expected_pass: str
    └─ is_connected: bool
```

## Synchronization Points

1. **Render Completion** → Start PassStreamer (T=0)
2. **Beauty Ready** → Send immediately (T≈0.5s)
3. **Tier 2 Window** → Send Normal, Depth, Diffuse (T=2-10s)
4. **Compositor Active** → Client can grade/composite (T≈8s)
5. **Tier 3 Window** → Send remaining material passes (T=10-30s)
6. **Tier 4 Window** → Send Cryptomatte (T=30-40s)
7. **All Passes Ready** → Send PASS_COMPLETE (T≈35s)
8. **Export Ready** → All AOVs available for save/further use

## Error Handling

```
Server Side:
├─ EXR parsing fails
│  └─ Log error, continue with available passes
├─ WebSocket send fails
│  └─ Log error, stop streaming (client will retry or skip)
└─ Encoding fails
   └─ Log warning, send raw data instead

Client Side:
├─ Receive corrupted PASS_DATA
│  └─ Log error, skip pass, continue waiting for next
├─ Compositor update fails
│  └─ Log warning, cache pass but skip compositor update
└─ Connection closes during streaming
   └─ Log error, clear passes, wait for new render
```

---

This architecture enables:
- **Predictable performance**: Tiers deliver on schedule
- **Live feedback**: Compositor updates as passes arrive
- **Bandwidth efficiency**: Compression at each tier
- **Modularity**: Each component independently testable
- **Extensibility**: Easy to add new passes or adjust timing
