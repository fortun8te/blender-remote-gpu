# Windows GPU Render Server — Setup & Run

This is the **server side** (RTX 5090 machine). The Mac runs the Blender addon that connects to this server.

## What You Need

1. **Windows 10/11** (your RTX 5090 machine)
2. **Blender 4.0+** installed (from blender.org)
3. **Tailscale** installed and running (free from tailscale.com)
4. **Python 3.10+** (optional — `start_server.bat` uses bundled Python if available)

## Step 1: Install Blender on Windows

1. Download Blender from https://www.blender.org/download/
2. Run the installer (any 4.0+ version)
3. Install normally — **you do NOT need to open Blender**, the server runs headless

## Step 2: Install Tailscale on Windows

1. Download Tailscale from https://tailscale.com/download/windows
2. Install and run it
3. Sign in with your account (same account as Mac)
4. **Important:** Note your Windows Tailscale IP address
   - Right-click Tailscale in taskbar → Show status
   - Look for something like `100.74.135.83`

## Step 3: Download & Setup Server Code

1. Clone this repo OR download the ZIP:
   ```
   git clone https://github.com/fortun8te/blender-remote-gpu.git
   ```
2. Extract to a folder on Windows, e.g.: `C:\blender-remote-gpu\`

3. **Update the Tailscale IPs** (if different from defaults):
   - Open: `C:\blender-remote-gpu\shared\dev_config.py`
   - Find these lines:
     ```python
     REMOTE_SERVER_IP = "100.74.135.83"  # Your Windows IP from Tailscale
     LOCAL_CLIENT_IP = "100.119.65.49"   # Your Mac's Tailscale IP
     ```
   - Replace with YOUR actual Tailscale IPs from Step 2
   - Save the file

## Step 4: Run the Server

1. Open File Explorer
2. Navigate to `C:\blender-remote-gpu\`
3. **Double-click `start_server.bat`**
4. A command window will open
5. **Wait for this message:**
   ```
   GPU access OK: NVIDIA RTX 5090
   Server listening on 0.0.0.0:9876 (Tailscale)
   Waiting for connections...
   ```
6. **Leave this window open** — the server runs in the foreground

## Step 5: Test Connection (from Mac)

1. On Mac, open Blender
2. Go to: **Render Properties** (camera icon) → select **"Cycles GPU"**
3. You should see the status change to **"Connected"** with the GPU info
4. Press **F12** to test a render — it should render on the Windows GPU
5. Check the Windows command window — you should see connection logs

## Troubleshooting

### Server won't start
- Make sure Blender is installed on Windows
- Try running `start_server.bat` as Administrator
- Check that port 9876 isn't blocked by Windows Firewall

### "Missing dependency" error
- Run this in PowerShell (as Administrator):
  ```powershell
  pip install websockets msgpack Pillow numpy
  ```

### Mac says "Connection refused"
- Check Tailscale is running on both machines
- Verify the IP addresses in `dev_config.py` match your Tailscale IPs
- Make sure the Windows firewall allows connections on port 9876

### GPU not detected
- Check that NVIDIA/AMD drivers are installed on Windows
- Make sure Blender can access your GPU (test in regular Blender render)

## What's Running

When `start_server.bat` is active:
- **WebSocket server** listens on port 9876 (Tailscale VPN)
- **Blender headless process** launches when Mac sends a render
- **GPU rendering** happens on your RTX card
- **Frame compression** (JPEG for viewport, EXR for final)
- **Results stream back** to Mac in real-time (<50ms latency)

## Stop the Server

Simply close the command window or press `Ctrl+C`.

---

**Next:** Go back to your Mac and install the addon from `blender_remote_gpu_addon.zip` in Blender Preferences.
