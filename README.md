# Blender Remote GPU

Render Blender scenes on a remote GPU over Tailscale. Start a render on your Mac in Blender, computing happens on Windows RTX card in real-time.

**Status:** ✓ Phase 1 complete (final render), Phase 2+ (live viewport) coming next

---

## Architecture

```
Mac (Blender 5.0 + addon)              Windows (RTX GPU)
+-----------------------+               +-----------------------+
| Blender UI            |               | Python WebSocket      |
|                       |  WebSocket    |                       |
| RemoteGPUEngine       |<-- ws:// ---->| server.py             |
| [Connect] [Render]    |  JSON msgs    |                       |
| GPU: RTX 5090         |  + binary     | • Receive .blend      |
|                       |  <-- PNG -----| • Render subprocess   |
+-----------------------+               | • Send JPEG back      |
        ↓                               +-----------------------+
    Tailscale VPN
    (encrypted tunnel)
    Mac: 100.119.65.49
    Win: 100.74.135.83:9876
```

### Why This Architecture?

1. **Simple sync WebSocket** — No complex async event loops (proven problematic in Blender)
2. **JSON protocol** — Debuggable, no binary framing overhead
3. **No TLS/auth** — Tailscale handles encryption & authentication at VPN level
4. **6 message types** — Minimal protocol (vs 25+ in over-engineered version)
5. **~800 lines total** — Rewrite from 6,741 lines of dead code

---

## Quick Start

### Prerequisites

- **Mac:** Blender 4.0+ installed
- **Windows:** Python 3.9+, Blender CLI available
- **Both:** Tailscale VPN with mutual access

### 1. Windows: Start Server

```bash
# Option A: Run directly
cd blender-remote-gpu
python server/server.py --port 9876

# Option B: Use batch file (Windows)
start_server.bat
```

**Output should show:**
```
[Server] Starting on 0.0.0.0:9876
[Server] GPU: NVIDIA GeForce RTX 5090 (24576 MB free)
[Server] Waiting for client connections...
```

### 2. Mac: Install Addon

1. Download the addon ZIP from GitHub releases
2. In Blender 5.0: **Edit → Preferences → Add-ons → Install**
3. Select `blender_remote_gpu_addon.zip`
4. Enable the addon: check “Blender Remote GPU”

### 3. Mac: Connect in Blender

1. Open Blender preferences addon panel (should auto-show)
2. Verify server IP = `100.74.135.83`, port = `9876`
3. Click **[Connect]**
4. You should see: `Connected — RTX 5090 (24576 MB free)`

### 4. Test Render

1. Open any .blend scene (or create a simple one)
2. Switch to **Cycles** render engine (Remote GPU requires it)
3. Press **F12** to render
4. Watch the image render on Windows, display on Mac

---

## Troubleshooting

### “Connection timeout” in Blender

**Checklist:**
- [ ] Windows server running? Check console for `[Server] Waiting for client connections...`
- [ ] Tailscale active on both machines? Run `tailscale status` on Mac
- [ ] Windows machine shows `active` and reachable?
- [ ] Firewall blocking port 9876 on Windows?

**Test connectivity from Mac:**
```bash
python3 << ‘EOF’
from websockets.sync.client import connect
import json

try:
    ws = connect(“ws://100.74.135.83:9876”, open_timeout=5)
    ws.send(json.dumps({“type”: “ping”}))
    print(“✓ Handshake:”, ws.recv())
    ws.close()
except Exception as e:
    print(“✗ Error:”, e)
EOF
```

### Addon fails to load

**Issue:** `ImportError: No module named ‘websockets’`

**Solution:** Addon auto-installs websockets on first load. If that fails:
```bash
pip install websockets
```

Or manually install to addon:
```bash
cd ~/.config/blender/5.0/scripts/addons/addon
pip install --target modules websockets
```

### Render hangs

**Check Windows server logs:**
- Is the Blender executable path correct? (Server tries: `C:\Program Files\Blender Foundation\Blender 4.1\blender.exe`, etc.)
- Is scene file valid .blend?
- GPU overheating? Check VRAM usage: `nvidia-smi`

---

## Protocol

All messages are JSON text frames over WebSocket, optionally followed by binary data.

### Client → Server

```json
{“type”: “ping”}
```
Check if server is alive.

```json
{
  “type”: “scene_upload”,
  “filename”: “scene.blend”,
  “size”: 12345
}
```
Followed by binary frame with .blend file bytes.

```json
{
  “type”: “render_start”,
  “width”: 1920,
  “height”: 1080,
  “samples”: 128
}
```
Start rendering the uploaded scene.

```json
{“type”: “render_cancel”}
```
Cancel current render.

### Server → Client

```json
{
  “type”: “pong”,
  “gpu”: “NVIDIA GeForce RTX 5090”,
  “vram_free”: 24576
}
```
Response to ping. GPU name and free VRAM in MB.

```json
{
  “type”: “scene_ack”
}
```
Scene received and saved.

```json
{
  “type”: “progress”,
  “samples_done”: 64,
  “samples_total”: 128,
  “message”: “Rendering...”
}
```
Render progress updates.

```json
{
  “type”: “frame”,
  “width”: 1920,
  “height”: 1080,
  “format”: “png”
}
```
Followed by binary frame with PNG image bytes.

```json
{
  “type”: “error”,
  “message”: “Render failed: Blender not found”
}
```
Error occurred.

---

## File Structure

```
blender-remote-gpu/
├── addon/
│   ├── __init__.py          # Registration, package bootstrap
│   ├── preferences.py       # UI: IP, Port, Connect button
│   ├── operators.py         # OT_connect, OT_disconnect operators
│   ├── connection.py        # Threaded sync WebSocket client
│   ├── engine.py            # RenderEngine: final render only
│   └── modules/             # websockets installs here
├── server/
│   ├── server.py            # WebSocket server, message routing
│   └── renderer.py          # Blender subprocess rendering
├── shared/
│   └── protocol.py          # Message type constants
├── start_server.bat         # Windows batch launcher
└── README.md                # This file
```

---

## Development

### Running from Source

**Server (Windows):**
```bash
cd blender-remote-gpu
python server/server.py --port 9876
```

**Addon (Mac):**
1. Symlink to Blender addons folder:
   ```bash
   ln -s ~/Downloads/blender-remote-gpu/addon ~/.config/blender/5.0/scripts/addons/addon
   ```
2. Restart Blender
3. Enable in Preferences

### Testing

Run connectivity test from Mac:
```bash
python3 << ‘EOF’
from websockets.sync.client import connect
import json
ws = connect(“ws://100.74.135.83:9876”, open_timeout=10)
ws.send(json.dumps({“type”: “ping”}))
print(“Pong:”, ws.recv())
ws.close()
EOF
```

---

## Known Limitations

1. **Phase 1 only (final render)** — Live viewport streaming coming in Phase 2
2. **Tailscale-only** — Requires VPN to be running and connected
3. **Single scene at a time** — Server stores one .blend, renders one at a time
4. **No render resume** — Render cancellation stops immediately, no checkpoints
5. **No compositor** — Compositor nodes not supported yet (Phase 3)

---

## Roadmap

- [x] **Phase 1:** Final render (F12) ✓
- [ ] **Phase 2:** Live viewport streaming (camera orbit, progressive samples)
- [ ] **Phase 3:** Compositor support
- [ ] **Phase 4:** Multi-GPU load balancing
- [ ] **Phase 5:** Web interface for server monitoring

---

## Credits

Built with:
- **Blender 5.0 Python API**
- **WebSockets** (sync client)
- **Tailscale VPN**

Original concept: Interactive GPU rendering over VPN for Mac ↔ Windows pipeline.

---

## License

MIT

---

## Support

For issues:
1. Check **Troubleshooting** section above
2. Review Windows server logs
3. Run connectivity test
4. File issue on GitHub with:
   - `blender --version`
   - `python --version`
   - `tailscale status`
   - Full error message from Blender console
