# Fast Dev Setup — One-Time Configuration

Get the addon up and running in 2 minutes with hardcoded settings.

## Step 1: Configure IP (Do This Once)

Edit `shared/dev_config.py`:

```python
REMOTE_SERVER_IP = "192.168.1.100"  # YOUR WINDOWS MACHINE IP
```

Find your Windows machine IP:
- **Windows:** `ipconfig` → look for "IPv4 Address" (e.g., 192.168.x.x)
- **Mac:** `ifconfig` → look for "inet" under active network

## Step 2: Install Addon

```bash
cd /Users/mk/Downloads/blender-remote-gpu
ln -s $(pwd)/addon ~/.config/blender/4.3/scripts/addons/remote_gpu
```

Or copy manually:
- Blender Preferences → Add-ons → Install from File → select `addon/` folder

## Step 3: Enable in Blender

- Blender Preferences → Add-ons → Search "Remote GPU"
- Click the checkbox to enable
- ✅ Should auto-connect immediately (watch Blender console for "✅ Auto-connected")

## Done! Now You Can:

### Edit & Reload (No Restart Needed)

```bash
# Edit addon code, then:
python3 dev_reload.py

# Or watch for changes automatically:
python3 dev_reload.py --watch
```

Changes appear instantly in viewport. No Blender restart required.

### Change Settings (Edit dev_config.py)

```python
DEFAULT_SAMPLES = 128          # Edit this
DEFAULT_DENOISER = "OPTIX"     # Edit this
VIEWPORT_FPS = 30              # Edit this
```

Restart Blender or run `python3 dev_reload.py` to apply.

### Use Tailscale Instead of LAN (Optional)

If you're away from the local network:

```python
USE_TAILSCALE = True
TAILSCALE_IP = "100.74.135.83"  # Your Windows Tailscale IP
```

Blender will auto-connect to the Tailscale IP instead.

---

## Troubleshooting

**Auto-connect fails?**
- Check Windows machine is running server: `python3 server/server.py --gpu optix`
- Check IP is correct: `ping <WINDOWS_IP>`
- Check firewall allows port 9876

**Changes don't reload?**
- Run `python3 dev_reload.py` manually
- Or restart Blender
- Check console for errors: Window → Toggle System Console

**GPU not detected on Windows?**
- Run `nvidia-smi` — should show RTX 5090
- Run `nvcc --version` — should show CUDA toolkit
- Check server logs for GPU detection

---

## Development Workflow

1. **Edit code** → changes auto-save
2. **Run `python3 dev_reload.py`** → addon reloads in Blender
3. **Test in viewport** → see changes instantly
4. **Debug via console** → Window → Toggle System Console

No restarts needed. No configuration needed. Just edit and reload.

---

## Production Deployment

Once you're happy:

```bash
git add -A
git commit -m "Production-ready remote GPU addon"
git push origin main
```

Remove `dev_config.py` in production:
```python
# Addon falls back to preferences panel UI
# Users configure IP/settings manually
```
