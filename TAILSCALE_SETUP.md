# Tailscale Setup Guide

## Quick Start (5 minutes)

### Step 1: Install Tailscale on Both Machines

**Windows:**
- Download from https://tailscale.com/download
- Install and log in with your account
- Note your Tailscale IP (e.g., `100.74.135.83`)

**Mac:**
- Download from https://tailscale.com/download
- Install and log in
- Note your Tailscale IP (e.g., `100.119.65.49`)

### Step 2: Update Dev Config on Mac

Edit `shared/dev_config.py`:

```python
REMOTE_SERVER_IP = "100.74.135.83"  # Windows Tailscale IP
LOCAL_CLIENT_IP = "100.119.65.49"   # Your Mac Tailscale IP
USE_TAILSCALE = True
USE_TLS = True
```

### Step 3: Generate TLS Certificate on Windows

Open Command Prompt and run:

```bash
cd C:\path\to\blender-remote-gpu
setup_tls.bat
```

This creates:
- `C:\tmp\cert.pem` (certificate)
- `C:\tmp\key.pem` (private key)

### Step 4: Start the Server

Double-click `start_server.bat` on Windows. It will:
1. Generate TLS cert if needed
2. Start server with TLS enabled
3. Listen on Tailscale interface

### Step 5: Start Blender on Mac

1. Launch Blender
2. Go to Render Properties → Remote GPU → Connect
3. Should auto-connect via Tailscale IP (from dev_config.py)
4. You'll see "Connected" ✓

---

## Security Features

✓ **TLS Encryption** — All traffic encrypted over Tailscale
✓ **API Key Auth** — Client/server must match key (dev: sk-render-dev-2026)
✓ **Tailscale VPN** — Private mesh network, no port exposure

---

## Troubleshooting

**"Connection refused"**
- Check Windows server is running: `tailscale ip` should show IP
- Firewall: Windows Defender usually allows Tailscale
- Ping from Mac: `ping 100.74.135.83`

**"TLS certificate error"**
- Run `setup_tls.bat` again on Windows
- Addon expects self-signed cert (fine for dev)

**"Latency > 100ms"**
- Normal over Tailscale (depending on internet)
- Check bandwidth: `tailscale netcheck`

**Frame drops**
- Reduce JPEG quality in preferences (40-75 range)
- Check network: `tailscale status --peers`

---

## Production Deployment

For production:

1. **Replace self-signed cert** with proper cert
2. **Update API_KEY** in dev_config.py (not `sk-render-dev-2026`)
3. **Enable VERIFY_SERVER_CERT** in dev_config.py
4. **Remove dev_config.py** entirely (users configure in Blender UI)

---

## Network Architecture

```
Mac (Blender)              Tailscale VPN                Windows (Server)
100.119.65.49              Private mesh                 100.74.135.83
    |                                                           |
    |-------- TLS encrypted WebSocket ----(encrypted)-----------|
    |  wss://100.74.135.83:9876/                                |
    |                                                           |
```

All traffic is:
- **Encrypted** by TLS
- **Private** (Tailscale mesh VPN)
- **Fast** (direct routing, no relay unless needed)
