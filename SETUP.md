# Remote GPU Render — Setup Guide

## Architecture
```
Mac (Blender 5+ with addon)         Windows (RTX 5090)
┌─────────────────────┐             ┌──────────────────┐
│ Blender UI (local)  │  WebSocket  │ Render Server    │
│ Remote Cycles Engine│◄───────────►│ Headless Blender │
│ Your .blend files   │             │ Cycles/OptiX     │
└─────────────────────┘             └──────────────────┘
```

## Windows Setup (Render Server)

### 1. Prerequisites
- Blender 5+ installed (must be in PATH or provide full path)
- Python 3.11+
- NVIDIA drivers up to date (RTX 5090)

### 2. Install dependencies
```bash
cd blender-remote-gpu
pip install -r requirements.txt
```

### 3. Start the server
```bash
# If Blender is in PATH:
python -m server.server

# If Blender is not in PATH:
python -m server.server --blender "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"

# Custom port:
python -m server.server --port 9876
```

### 4. Find your IP address
```bash
ipconfig
# Look for "IPv4 Address" under your Ethernet adapter
# Example: 192.168.1.100
```

### 5. Optional: GPU monitoring
```bash
pip install pynvml
# The server will show GPU name, VRAM, and temperature in the Blender panel
```

## Mac Setup (Blender Addon)

### 1. Install Python dependencies in Blender's Python
```bash
# Find Blender's Python:
/Applications/Blender.app/Contents/Resources/5.0/python/bin/python3 -m pip install websockets msgpack Pillow numpy
```

### 2. Install the addon
Option A: Symlink (for development)
```bash
ln -s /path/to/blender-remote-gpu/addon ~/.config/blender/5.0/scripts/addons/remote_gpu_render
```

Option B: Copy
```bash
cp -r /path/to/blender-remote-gpu/addon ~/.config/blender/5.0/scripts/addons/remote_gpu_render
```

Note: Also copy the `shared/` directory next to the addon, or install the shared module.

### 3. Enable the addon in Blender
- Edit > Preferences > Add-ons
- Search for "Remote GPU Render"
- Enable it
- Set the server IP and port in the addon preferences

### 4. Use it
- In Render Properties, change the render engine to "Remote Cycles"
- Click "Connect" in the Remote GPU panel
- The viewport will now render on your Windows GPU
- F12 for final renders also goes to the remote GPU

## Tailscale Setup (for remote/WAN access)

### 1. Install Tailscale on both machines
- Mac: https://tailscale.com/download/mac
- Windows: https://tailscale.com/download/windows

### 2. Use the Tailscale IP instead of LAN IP
- Find your Windows Tailscale IP: `tailscale ip` (usually 100.x.x.x)
- Set that as the server IP in Blender's addon preferences

### 3. Latency expectations
- LAN (Ethernet): ~1-5ms round trip
- Tailscale (same city): ~10-30ms
- Tailscale (cross-country): ~50-100ms
- Tailscale (international): ~100-200ms

## Troubleshooting

### "Connection failed"
- Check Windows firewall allows port 9876
- Verify the server is running (you should see "Waiting for client connection...")
- Try pinging the Windows IP from Mac: `ping 192.168.1.100`

### "Render failed"
- Check that Blender works on Windows: `blender -b --python-expr "import bpy; print('ok')"`
- Check the server console for error messages
- Ensure NVIDIA drivers and CUDA/OptiX are working

### Slow viewport
- Reduce viewport JPEG quality in addon preferences (lower = faster)
- Start with low sample counts
- On Tailscale: expect higher latency, reduce quality accordingly
