# Addon Development Workflow

## Stop doing this ❌
- Package ZIPs every time
- Transfer files back and forth
- Keep saying "it's done" when it's not tested

## Do this instead ✅

### On Your Local Machine (where you edit code)

```bash
# 1. Make changes to addon code
nano addon/preferences.py  # or your editor

# 2. Quick test in local Blender
# - Open Blender
# - Preferences → Add-ons → Install from Disk
# - Select: ~/Downloads/blender-remote-gpu/blender_remote_gpu_addon_b4.zip
# - Test it

# 3. Commit when it works
git add addon/
git commit -m "Clear message about what changed"
git push
```

### On Remote Computer (with GPU)

```bash
# 1. One-time setup (first time only)
cd /path/to/blender-remote-gpu
git clone https://github.com/fortun8te/blender-remote-gpu.git

# 2. Every time local code changes
./update.sh

# 3. In Blender:
# - Go to: Edit → Preferences → Add-ons
# - Find "Remote GPU Renderer"
# - Toggle OFF then back ON (this reloads the addon)
# - Check Preferences tab to see connection status
```

## How to Know If It's Actually Working

The **new** preferences panel shows:

```
✓ CONNECTED
  GPU: RTX 4090
  VRAM: 24000 MB available
  [Disconnect button]
```

OR

```
✗ NOT CONNECTED
  Error: Connection refused — is server running?
  [Connect button]
  [Test Connection button] ← Click this to debug
```

## If "Test Connection" Fails

1. **Server not running?** Start it on the remote machine
2. **Wrong IP/Port?** Check `server_ip` and `server_port` in Preferences
3. **Firewall?** Check that port 9876 is open
4. **Tailscale issues?** Run `tailscale status` to verify VPN

The error message will tell you EXACTLY what's wrong. That's the point.

## Git Workflow

```bash
# Local development
git branch feature/new-feature
git add addon/
git commit -m "Descriptive message"
git push

# Then on remote:
./update.sh
# Test in Blender
```

## Key Point

You should **never** need to manually copy files or reinstall addons.

- Changes = `git push`
- Update = `./update.sh` + toggle addon in Blender
- Status = Look at Preferences (it shows the truth now)
