# Agent USB1: USB Server for Thunderbolt Communication

## Overview
Created `usb_server.py` — Windows USB server that listens on Thunderbolt for connections from Mac Blender addon. Skips HTTP entirely, using binary JSON protocol over USB bulk transfers.

## Key Components

### USBServer Class (572 lines)
- **Device Detection**: Auto-detects Mac → Windows Thunderbolt USB device
- **Message Protocol**: 4KB JSON request/response buffers
- **Job Queue Integration**: Reuses existing `JobQueue` from b38
- **Worker Threads**: Spawn Blender subprocess for each render job
- **Binary Result Streaming**: Metadata + PNG data over USB

### Protocol

#### Render Request
```json
Mac sends:
{"cmd": "render", "scene_path": "C:\\scene.blend", "width": 640, "height": 360, "samples": 4}

Server responds:
{"status": "queued", "job_id": "abc123d7"}
```

#### Status Query
```json
Mac sends:
{"cmd": "status", "job_id": "abc123d7"}

Server responds:
{"status": "running|done|error", "progress": 0.5, "job_id": "abc123d7"}
```

#### Result Request
```json
Mac sends:
{"cmd": "result", "job_id": "abc123d7"}

Server responds (binary):
[4-byte metadata length] + [JSON metadata] + [PNG image data]
```

## Implementation Details

### USB Configuration
- **Endpoint IN**: 0x81 (bulk transfer from device to host)
- **Endpoint OUT**: 0x01 (bulk transfer from host to device)
- **Timeout**: 1000ms (USB_TIMEOUT)
- **Buffer**: 4096 bytes (USB_READ_SIZE)

### Threading
- **Main Thread**: Initialization, keepalive loop
- **Read Thread**: Continuously polls USB endpoint, dispatches messages
- **Worker Threads** (3 default): Dequeue jobs, execute Blender, store results
- **Thread Safety**: `JobQueue.lock` protects all state

### Blender Execution
- Auto-detects Blender.exe in standard Windows paths
- Spawns isolated subprocess with `--background` + `--python-expr`
- Renders directly to temp PNG (e.g., `C:\Users\...\Temp\render_abc123d7.png`)
- 5-minute timeout per job
- Base64 encodes PNG for JSON transport

### Logging
- **File**: `%TEMP%/usb_server.log` (typically `C:\Users\<user>\AppData\Local\Temp`)
- **Console**: Also logs to stdout
- **Levels**: DEBUG (USB reads/writes), INFO (job lifecycle), ERROR (failures)

## Dependencies
- `pyusb` — USB device communication
- `json` — Message serialization
- `subprocess` — Blender execution
- `threading` — Worker pool + read loop
- `job_queue.JobQueue` — Existing job queue from b38

## Error Handling
- **Blender not found**: Jobs fail with error message
- **USB disconnection**: Read loop exits cleanly, workers continue
- **Render timeout**: 5-minute timeout, marked as error
- **Invalid commands**: Responds with error status
- **Missing files**: Cleaned up, error logged

## Next Steps (Not in Scope)
1. Mac addon: Write USB client to send JSON + receive results
2. Visual Scout integration: Stream preview frames during render
3. Test harness: Validate USB protocol with mock device
4. Error recovery: Auto-reconnect on USB device loss

## Files Modified
- **New**: `/Users/mk/Downloads/blender-remote-gpu/usb_server.py` (572 lines)
- **Reused**: `job_queue.py` (no changes)

## Git Commit
```
commit 212a81353f48e16ac7197afb53a1cf9e783c3d89
Author: Michael Knaap <mk@macbook-pro.lan>
Date:   Fri Apr 3 20:19:42 2026 +0200

    Agent USB1: Create USB server for Thunderbolt communication
```

## Status
✅ Complete — USB server implementation finished, not tested yet as requested.
