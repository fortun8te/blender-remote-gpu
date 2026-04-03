# USB/Thunderbolt Quick Start Guide

Get your Remote GPU Render system running over USB/Thunderbolt in 5 minutes.

## Windows Setup (Render Server)

### Prerequisites
- Windows 10/11 with USB 3.0 port
- Blender 4.0+ installed
- Python 3.9+ with pip
- PyUSB library

### Installation

1. **Install PyUSB**
```bash
pip install pyusb
```

2. **Start the USB Server**
```bash
cd /path/to/blender-remote-gpu
python3 usb_server.py
```

You should see output:
```
2026-04-03 12:00:00 [INFO] Agent USB1: Windows USB Server for Thunderbolt
2026-04-03 12:00:00 [INFO] Blender path: C:\Program Files\Blender Foundation\Blender\blender.exe
2026-04-03 12:00:00 [INFO] USB Server ready
```

3. **Verify Server is Running**
Check the log file:
```bash
type %TEMP%\usb_server.log
```

**The Windows server is now ready. Keep it running in the background.**

## Mac Setup (Blender Addon)

### Prerequisites
- macOS 11+ with Thunderbolt 4/3 port
- Blender 4.0+ installed
- Thunderbolt cable (USB-C compatible)
- PyUSB in Blender's Python environment

### Installation

1. **Install PyUSB in Blender**

Open Terminal and run:
```bash
/Applications/Blender.app/Contents/Resources/4.3/python/bin/pip install pyusb
```

(Replace `4.3` with your Blender version if different)

2. **Install the Addon**

Copy the `remote_gpu_render` folder to Blender addons:
```bash
mkdir -p ~/Library/Application\ Support/Blender/4.3/scripts/addons/
cp -r remote_gpu_render ~/Library/Application\ Support/Blender/4.3/scripts/addons/
```

3. **Enable in Blender**

- Open Blender
- Go to **Preferences** → **Add-ons**
- Search for "Remote GPU"
- Check the box to enable
- You should see: `[RemoteGPU] Registered v1.0.39 b39`

### Hardware Connection

4. **Plug in the Thunderbolt Cable**

- Connect Mac and Windows machine with Thunderbolt 4 cable
- Both machines should detect the connection
- Wait 2-3 seconds for device enumeration

5. **Connect in Blender**

- Open the 3D Viewport
- Press `N` to open the sidebar
- Click the **Remote GPU** tab
- Click **Auto-discover (TB4 / LAN)**
  - OR manually enter Windows IP/port and click **Connect**

If successful, you should see:
```
CONNECTED
Latency: 2.5ms
```

## First Render

### Setup Steps

1. **Create a Test Scene**
   - New Blender project with default cube
   - Set render engine to "Remote GPU"
   - Set viewport shading to "Rendered" (Z key)

2. **Upload Scene**
   - In Remote GPU panel, click **Upload Scene**
   - Wait for "Scene uploaded" message

3. **Split Viewport**
   - Click and drag the viewport edge (left side)
   - Create left (workspace) and right (preview) viewport

4. **Set Preview Viewport**
   - Hover mouse over RIGHT viewport
   - In Remote GPU panel, click **Set This as Preview Viewport**
   - You should see "PREVIEW VIEWPORT" label

5. **Start Live Preview**
   - Click **Start Live Preview**
   - Watch the right viewport show live rendered result
   - Rotate the cube in the LEFT viewport
   - Right viewport updates automatically (low latency!)

### Test Render (F12)

For a full render:
- Press F12 on the Mac
- Blender sends job to Windows via USB
- Windows spawns Blender subprocess
- Render appears in image editor
- Check latency in addon panel (should be 1-5ms)

## Monitoring

### Server Status

**Windows — tail the log file:**
```bash
# PowerShell
Get-Content -Path $env:TEMP\usb_server.log -Wait

# Or use tail (if you have WSL/Git Bash)
tail -f /tmp/usb_server.log
```

**Expected output:**
```
[INFO] usb_server: USB Server initialized
[INFO] usb_server: Found Blender at C:\Program Files\Blender Foundation\Blender\blender.exe
[INFO] usb_server: USB Server ready
[DEBUG] usb_server: Received: {'cmd': 'render', 'scene_path': 'C:\\Users\\...\\scene.blend', ...}
[INFO] usb_server: Render job submitted: a1b2c3d4 (1920x1080, 256spp)
[DEBUG] usb_server: Queue: {'queue_size': 0, 'total_submitted': 1, 'queued': 0, 'running': 1, ...}
[INFO] usb_server: Render complete: a1b2c3d4 (2097152 bytes)
```

### Client Status

**Mac — check connection:**
In Blender Remote GPU panel:
```
CONNECTED
Latency: 2.3ms
```

**Or check log file:**
```bash
tail -f /tmp/usb_client.log
```

## Troubleshooting

### "Device not found on Thunderbolt USB"

**Problem**: Addon can't see Windows machine

**Solutions**:
1. Verify Thunderbolt cable is plugged in (both ends)
2. Check Windows server is running (`python3 usb_server.py`)
3. Manually enter Windows IP address in addon preferences
4. Restart Blender addon (disconnect/connect)

### High latency (>100ms)

**Problem**: USB transfer is slow

**Solutions**:
1. Use USB 3.0 cable/port (not 2.0)
2. Check no other USB devices competing for bandwidth
3. Update USB drivers on both machines
4. Try a different USB port

### Blender crashes on render

**Problem**: Remote render fails

**Solutions**:
1. Check `%TEMP%\usb_server.log` on Windows for Blender error
2. Ensure Blender is installed at expected path on Windows
3. Try manual render on Windows to verify Blender works
4. Verify scene file path is accessible from Windows

### "Scene not uploaded"

**Problem**: Addon can't send scene to server

**Solutions**:
1. Check connection status (should show CONNECTED)
2. Verify network path is accessible (Windows firewall)
3. Try uploading a simpler scene first
4. Check disk space on Windows

## Performance Tips

### Rendering
- Use **Live Preview** for iterative work (low latency)
- Use F12 for final full renders (utilizes all GPU power)
- Start with lower sample counts to test scene setup

### Optimization
- USB 3.0 achieves 400+ MB/s — more than enough for rendering
- Latency is typically 1-5ms (10-40x faster than HTTP)
- Multiple workers can render different jobs simultaneously

### Power Efficiency
- USB interrupt-driven (no polling drain on Mac)
- Server idles when no jobs pending
- Results cached for 5 minutes before cleanup

## Manual Deployment (Advanced)

If auto-discovery fails, manually configure IP/port:

### Windows
1. Open Command Prompt
2. Find IP address: `ipconfig` (look for "IPv4 Address")
3. Note the IP (e.g., `192.168.1.100`)

### Mac (Blender Addon)
1. Open Blender Preferences
2. Find Remote GPU addon
3. Under "Dispatcher":
   - IP: `192.168.1.100` (Windows IP from above)
   - Port: `11434` (default Ollama/render server port)
4. Click Connect

## Command Reference

### Windows Server

```bash
# Start server
python3 usb_server.py

# With verbose logging
PYTHONUNBUFFERED=1 python3 usb_server.py

# With max 5 workers (more parallel jobs)
# (Edit usb_server.py, line 542: USBServer(max_workers=5))
```

### Mac Client

```bash
# Check USB client log
tail -f /tmp/usb_client.log

# Restart addon
# In Blender: Disable/Enable Remote GPU addon in Preferences
```

## Next Steps

- Check USB_ARCHITECTURE.md for technical details
- Review DEPLOYMENT_GUIDE_V4.md for advanced configuration
- Monitor logs while rendering to understand system behavior
- Submit feedback/issues to the project GitHub

## Support

If you encounter issues:

1. **Check logs** (always the first step)
2. **Verify hardware** (cable, USB port, Thunderbolt settings)
3. **Test connectivity** (ping, manual USB scan)
4. **Review architecture** (understand protocol limitations)
5. **Report with logs** (include relevant log excerpts)

Happy rendering!
