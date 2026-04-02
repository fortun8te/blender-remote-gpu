# Changes Summary: Tailscale + TLS + Latency Display

## What Just Changed (Parallel Build)

### 1. Network Configuration (Tailscale)
- ✓ **dev_config.py**: Updated to use Tailscale IPs
  - Mac: `100.119.65.49`
  - Windows: `100.74.135.83`
- ✓ **Disabled hardcoded LAN IP** — now VPN-first

### 2. Security (TLS + API Key)
- ✓ **connection.py**: Added TLS support
  - Auto-converts `ws://` → `wss://` when TLS enabled
  - Self-signed cert support for dev
  - API key in headers (passed to server)
- ✓ **server/server.py**:
  - WebSocket server now supports TLS
  - Cert generation helper (openssl)
  - API key validation on connect
- ✓ **setup_tls.sh / setup_tls.bat**: Generate self-signed certs
  - Windows: `C:\tmp\cert.pem`, `C:\tmp\key.pem`
  - Mac: `/tmp/cert.pem`, `/tmp/key.pem`

### 3. Network Resilience
- ✓ **connection.py**: Auto-reconnect with exponential backoff
  - Retry up to 10 times
  - 500ms backoff → 30s max wait
  - Handles packet loss gracefully
- ✓ **Per-connection state tracking** for recovery

### 4. Latency Display (Real-Time)
- ✓ **connection.py**: Track latency in ms from server timestamps
- ✓ **addon/engine.py**: Viewport header shows real-time latency
  - Format: "RTX GPU | Latency: 47ms"
  - Updates every frame
  - Shows per-viewport priority if multi-viewport

### 5. Compression Controls
- ✓ **preferences.py**: JPEG quality slider (40-95 range)
  - Default: 75 (balanced)
  - User can adjust without UI restart
- ✓ **UI Cleanup**: Removed emojis, using icons only
  - No "✓", "🔗", "😀" in UI
  - Professional appearance

### 6. Clean UI (No Emojis)
- ✓ **preferences.py**: Rewrote all panels
  - ❌ Removed emoji from status messages
  - ✓ Using Blender native icons (INFO, GPU, URL, etc.)
  - Clean, minimal interface

### 7. Start Scripts Updated
- ✓ **start_server.sh** (Mac/Linux):
  - Auto-generates TLS cert on first run
  - Detects Tailscale
  - Starts server with `--use-tls`
- ✓ **start_server.bat** (Windows):
  - Same, but for Windows paths
  - Shows Tailscale IP at startup

---

## What's Ready to Test

### Local Testing (No Changes Needed)
1. Dev config already has your Tailscale IPs hardcoded
2. Run `start_server.bat` on Windows → generates TLS cert automatically
3. Run Blender on Mac → should auto-connect via Tailscale IP
4. Should see: "Connected | Latency: XX ms" in viewport

### What to Verify
- [ ] Windows server starts without errors
- [ ] TLS cert generates (should see 365-day message)
- [ ] Mac Blender auto-connects (dev_config has Tailscale IP)
- [ ] Latency displays in viewport header (real-time)
- [ ] Can adjust JPEG quality slider (40-95)
- [ ] UI has no emojis, only icons
- [ ] Works over Tailscale (not LAN)
- [ ] Auto-reconnect works if you pause/resume network

---

## Files Changed

### Modified Files
1. `shared/dev_config.py` — Tailscale IPs + TLS config
2. `addon/connection.py` — TLS + auto-reconnect + latency tracking
3. `addon/preferences.py` — Compression slider + clean UI
4. `addon/engine.py` — Latency display in viewport header
5. `server/server.py` — TLS support + cert generation + API key
6. `start_server.sh` — TLS setup + Tailscale detection
7. `start_server.bat` — TLS setup + Tailscale detection

### New Files
1. `setup_tls.sh` — TLS cert generation (Mac/Linux)
2. `setup_tls.bat` — TLS cert generation (Windows)
3. `TAILSCALE_SETUP.md` — Complete setup guide
4. `CHANGES_SUMMARY.md` — This file

---

## What's NOT in Scope (Per Requirements)

- ❌ Multi-GPU support (skip for now)
- ❌ Batch queue (users can manually trigger renders)
- ❌ GPU status panel (shows in viewport header instead)
- ❌ H.264/NVENC encoding (sticking with progressive JPEG)

---

## Next Steps (When You're Ready)

1. **Windows Setup**:
   ```bash
   cd C:\path\to\blender-remote-gpu
   start_server.bat
   ```

2. **Mac Setup**:
   - Dev config already set (has your IPs)
   - Launch Blender
   - Go to Render Properties → Remote GPU
   - Click "Connect"

3. **Test**:
   - Switch to "Remote Cycles" render engine
   - Enter viewport rendered mode (Z → Rendered)
   - Orbit camera → should see live preview
   - F12 → final render should work

---

## Security Note

For production, you'll want to:
- Generate proper TLS certs (not self-signed)
- Change API key from `sk-render-dev-2026`
- Enable cert verification (`VERIFY_SERVER_CERT = True`)
- Remove `dev_config.py` entirely (users configure in UI)

For now (development), everything is preconfigured for fast iteration.
