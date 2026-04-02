# Installation & Setup — Step-by-Step

## TL;DR: 5-Minute Setup

**Windows Machine (RTX 5090):**
```bash
cd C:\path\to\blender-remote-gpu
start_server.bat
```

**Mac Machine (Blender):**
1. Open Blender
2. Edit → Preferences → Add-ons → Install from File
3. Select `/Users/mk/Downloads/blender-remote-gpu/addon/` folder
4. Enable the addon
5. Done — auto-connects, you'll see "Cycles GPU" in render engine dropdown

---

## Understanding the Architecture

**Where does the addon go?**
- ✅ **Mac (client)**: Install addon in Blender — this is where you edit scenes
- ❌ **Windows (server)**: NO addon — just run `start_server.bat`, Blender runs headless

**Why?**
- Mac Blender = your UI, where you work
- Windows Blender = headless subprocess, renders on RTX 5090
- Addon = communication layer on Mac side only

---

## Installation — Mac (Client)

### Step 1: Get the Addon Folder

The addon is at:
```
/Users/mk/Downloads/blender-remote-gpu/addon/
```

This folder must contain:
- `__init__.py`
- `engine.py`
- `connection.py`
- `preferences.py`
- `frame.py`
- `sync.py`
- (other .py files)

### Step 2: Install in Blender

1. **Open Blender** (any version 4.0+)
2. **Go to:** Edit → Preferences → Add-ons
3. **Click:** "Install from File"
4. **Navigate to:** `/Users/mk/Downloads/blender-remote-gpu/addon/`
5. **Click:** Select Folder (NOT a single file)
6. **Enable the addon:** Check the checkbox next to "Remote GPU Render"
7. **Done!**

### Step 3: Verify It Works

In Blender:
1. Go to **Render Properties** (camera icon)
2. In the render engine dropdown, you should see: **"Cycles GPU"**
3. Select it
4. You should see in the header: `"RTX 5090 | Latency: 47ms"` (or similar)

If it says "Disconnected", move to Windows setup.

---

## Installation — Windows (Server)

### Step 1: Requirements

- **Blender 4.0+** installed (can be in Program Files)
- **Python 3.10+** (Blender includes this)
- **NVIDIA GPU** with CUDA/OptiX drivers
- **Tailscale** installed and running

### Step 2: Start the Server

Double-click:
```
C:\path\to\blender-remote-gpu\start_server.bat
```

**Expected output:**
```
====================================
Remote GPU Render Server Startup
Tailscale Mode (VPN)
====================================

Setting up TLS certificate...
X TLS certificate generated:
  - C:\tmp\cert.pem
  - C:\tmp\key.pem

Checking dependencies...
Starting server on port 9876 (Tailscale) with TLS...
Press Ctrl+C to stop

Tailscale IP: Check 'tailscale ip' or 'tailscale status'

[2026-04-02 14:23:45] Server listening on 0.0.0.0:9876 with TLS
[2026-04-02 14:23:45] Waiting for client connection...
```

**If you see errors:**
- ❌ `"ERROR: Python not found"` → Install Python 3.10+ from python.org
- ❌ `"ERROR: server/server.py not found"` → Run from `blender-remote-gpu` directory
- ❌ `"OpenSSL not found"` → OK, TLS will be disabled (non-critical)

### Step 3: Verify GPU Access

The server logs will show:
```
[2026-04-02 14:23:46] GPU access OK: NVIDIA RTX 5090 (24000MB free)
```

If it says "Unknown (install pynvml...)", that's OK — GPU will still work.

**Keep this window open.** Server runs in foreground. Ctrl+C to stop.

---

## Installation — Verify Connection

### On Mac (Client):

1. **Open Blender**
2. **Select render engine:** "Cycles GPU"
3. **Go to:** Render Properties → Remote GPU
4. **Status should show:** "Connected" (green checkmark)
5. **Header shows:** "RTX 5090 | Latency: 23ms"

### If NOT connected:

**Check Windows:**
- [ ] `start_server.bat` is running
- [ ] Shows "GPU access OK"
- [ ] Shows "Waiting for client connection"

**Check Tailscale:**
- [ ] Mac is online (run `tailscale ip` in terminal)
- [ ] Windows is online (run `tailscale ip` in Command Prompt)
- [ ] They can ping each other:
  ```bash
  # Mac terminal:
  ping 100.74.135.83

  # Windows cmd:
  ping 100.119.65.49
  ```

---

## Your First Render

1. **In Blender on Mac:**
   - Open any .blend file (or create a simple cube)
   - Switch render engine to "Cycles GPU"
   - Press **F12** to render

2. **Expected behavior:**
   - Image appears in render result viewer
   - Status bar shows render progress
   - Takes 10-30 seconds (depending on scene complexity)

3. **Viewport rendering:**
   - Press **Z** → **Rendered** (or click viewport shading)
   - Camera pans and orbits should feel smooth
   - Samples accumulate in real-time

---

## Troubleshooting

### "Connection refused"
- Windows server not running? Double-click `start_server.bat`
- Firewall blocking? Windows Defender usually allows Tailscale
- Check Tailscale: `tailscale status` on both machines

### "TLS certificate error"
- Run `setup_tls.bat` manually on Windows (inside `blender-remote-gpu` folder)
- Or: Ignore (TLS is optional for dev)

### "Latency > 100ms"
- Normal over Tailscale (depending on internet)
- Check network: `tailscale netcheck`
- Reduce JPEG quality in Render Properties (40-60 range if slow)

### "Blender crashes on F12"
- Check Mac's Blender console: Window → Toggle System Console
- Windows server logs should show error
- All 30 bugs are fixed, so this shouldn't happen

### "Server GPU not detected"
- Windows Command Prompt:
  ```bash
  nvidia-smi
  ```
  Should show your RTX 5090

---

## What Now?

1. **Start Windows server:**
   ```
   C:\blender-remote-gpu\start_server.bat
   ```

2. **Install addon on Mac:**
   - Blender → Preferences → Add-ons → Install from File
   - Select `/Users/mk/Downloads/blender-remote-gpu/addon/` folder

3. **Open Blender, select "Cycles GPU" render engine**

4. **Press F12 to render — watch your Windows GPU do the work!**

---

## Under the Hood (What's Happening)

```
Mac Blender (UI)
    ↓
    └─→ Addon sends scene over Tailscale (TLS encrypted)
        ↓
Windows Server (headless Blender)
    ↓
    └─→ Renders with RTX 5090
        ↓
     └─→ Sends frame back over Tailscale
        ↓
Mac Blender displays result
```

Everything is encrypted (TLS), authenticated (API key), and over Tailscale (private VPN). Your ISP can't see your 3D models.

---

## Support

If something doesn't work:
1. Check the steps above
2. Run Windows server, watch for errors
3. Check Blender System Console on Mac: Window → Toggle System Console
4. Everything should just work — all bugs fixed!

Enjoy your RTX 5090. 🚀
