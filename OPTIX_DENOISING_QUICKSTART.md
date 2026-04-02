# OptiX Denoising — Quick Start Guide

## What Was Implemented

OptiX/OIDN denoising integration for Blender Remote GPU, enabling real-time AI noise reduction on the render server with automatic AOV creation and GPU status display.

---

## User Workflow

### 1. Enable Denoising (Mac)
1. Open Blender render properties
2. Select "Remote Cycles" engine
3. Scroll to "Render Devices" panel
4. Choose denoiser: **OFF** | **OIDN** | **OptiX**
5. Adjust "Denoiser Intensity" slider (0-1, default 0.8)
6. Check "Auto AOV Creation" (recommended)

### 2. Render
1. Press **F12** (final render) or set up in viewport
2. Engine automatically:
   - Sends denoiser config to server
   - Creates Normal/Albedo AOVs on server
   - Renders with denoising enabled
   - Returns denoised image to Blender

### 3. Monitor Progress
- Viewport header shows: "**RTX GPU Active (RTX 5090)**"
- Server logs: "OptiX denoising applied (intensity=0.8, took 234ms)"

---

## Configuration Options

### Denoiser Type

| Type | Use Case | Requirements |
|------|----------|--------------|
| **OFF** | Disable denoising | None |
| **OIDN** | CPU-based, universal | All GPUs (Blender bundled) |
| **OptiX** | GPU-accelerated (fastest) | NVIDIA RTX GPU + driver |

### Intensity (0.0 - 1.0)

- **0.0** - No denoising
- **0.3** - Subtle noise reduction
- **0.5-0.8** - Balanced (recommended)
- **0.9-1.0** - Aggressive denoising (may blur details)

### Auto AOV Creation

- **Enabled (default)** - Server creates Normal/Diffuse Color passes automatically
- **Disabled** - Rely on existing AOVs in .blend (advanced)

---

## Server Configuration (Windows)

### Automatic Detection
- OptiX: Detected if NVIDIA GPU + driver present
- OIDN: Always available (bundled with Blender)

### Fallback Behavior
If OptiX requested but unavailable → automatically uses OIDN
If OIDN requested but unavailable → render proceeds undenoised (warns in log)

### Logs Location
- **Windows**: `C:\Temp\remote_gpu_*.log` (or custom via `--log-file`)
- Search for: `"OptiX denoising applied"`

---

## Testing Checklist

### Mac Addon
```
[ ] Render Properties show Denoiser dropdown
[ ] Intensity slider ranges 0-1
[ ] Auto AOV Creation toggle visible
[ ] Server GPU info displays (when connected)
[ ] OptiX warning shows "Requires NVIDIA RTX GPU"
```

### Server
```
[ ] RENDER_SETTINGS message received before RENDER_START
[ ] Denoiser settings logged: "[session] Render settings updated: OPTIX denoising (intensity=0.8)"
[ ] Blender script includes denoising config
[ ] Render completes with denoising: "OptiX denoising applied (intensity=0.8, took 234ms)"
[ ] Final image shows reduced noise
```

### Fallback
```
[ ] Request OptiX on non-RTX GPU → logs "falling back to OIDN"
[ ] Request unsupported denoiser → logs warning, renders undenoised
[ ] Disable denoising (OFF) → normal render without denoising
```

---

## Troubleshooting

### "OptiX not available" message
**Cause**: NVIDIA GPU or driver missing
**Fix**:
1. Verify GPU: `nvidia-smi` on Windows
2. Update NVIDIA driver
3. Use OIDN instead (fallback automatic)

### Render hangs during denoising
**Cause**: VRAM exhaustion (OptiX needs extra memory for AOVs)
**Fix**:
1. Reduce render resolution
2. Reduce samples
3. Switch to OIDN (CPU-based, less VRAM)
4. Disable auto AOV creation (if custom AOVs exist)

### Denoised image looks blurry
**Cause**: Intensity too high
**Fix**: Lower intensity slider (try 0.5-0.6)

### No denoising in output
**Cause**:
1. Denoiser type set to "OFF"
2. Denoiser unavailable (check logs)
3. AOVs not created (check "Auto AOV Creation" enabled)

**Fix**:
1. Verify denoiser_type != "OFF"
2. Check server logs: `grep "denoising" remote_gpu_*.log`
3. Enable auto AOV creation

---

## Performance Notes

### OptiX (RTX GPUs)
- Speed: ~150-300ms per render (varies with resolution)
- Memory: Adds ~500MB-1GB for AOVs
- Quality: GPU-optimized, best for RTX

### OIDN (All GPUs)
- Speed: ~100-500ms (CPU-based)
- Memory: Lower VRAM usage
- Quality: High-quality, slightly slower

### No Denoising
- Speed: Baseline render time
- Memory: Minimal overhead
- Quality: Raw render output

---

## Advanced Usage

### Custom Denoiser Intensity Per Render
```python
# From Python console (if integrated):
prefs = bpy.context.preferences.addons['blender_remote_gpu'].preferences
prefs.denoiser_intensity = 0.7  # Change before render
```

### Disable AOV Auto-Creation
```python
# If you have custom AOVs in your .blend:
prefs.auto_aov_creation = False
# Server will use existing AOVs instead
```

### Check Available Denoisers
Server logs on startup:
```
[startup] Available denoisers: ['OFF', 'OIDN', 'OPTIX']
```

---

## Integration Points

### Client → Server Messages
1. **RENDER_SETTINGS** (0x03) — Sent before render, contains denoiser config
2. **RENDER_START** (0x10) — Sent after settings, triggers render

### Server Logging
```
[render_settings_handler]
  "Render settings updated: OPTIX denoising (intensity=0.8)"

[blender_render]
  "OptiX denoising enabled (intensity=0.8)"

[post_render]
  "OptiX denoising applied (intensity=0.8, took 234ms)"
```

### Viewport Status
```
Disconnected:     "Disconnected"
Rendering:        "Rendering on GPU..."
Active + GPU:     "RTX GPU Active (RTX 5090)"
```

---

## File Structure

```
blender-remote-gpu/
├── addon/
│   ├── preferences.py          # ← Denoiser UI panel
│   ├── connection.py           # ← send_render_settings()
│   ├── engine.py               # ← GPU status badge
│   └── ...
├── server/
│   ├── renderer.py             # ← OptiX/OIDN script building
│   ├── server.py               # ← RENDER_SETTINGS handler
│   ├── denoiser.py             # ← NEW: Denoiser manager
│   └── ...
├── shared/
│   ├── protocol.py             # ← RENDER_SETTINGS message type
│   └── ...
└── OPTIX_DENOISING_IMPLEMENTATION.md  # ← Full technical docs
```

---

## Next Steps

1. **Test OptiX denoising**:
   - Set denoiser to "OptiX"
   - Adjust intensity to 0.8
   - Render a test scene

2. **Verify logs**:
   - Check server output for: `"OptiX denoising applied"`
   - Look for timing: `"took XXXms"`

3. **Adjust settings**:
   - Lower intensity if result too blurry
   - Increase samples if still noisy
   - Enable auto AOV creation if unsupported AOVs error

4. **Monitor performance**:
   - Compare render times: OptiX vs OIDN vs OFF
   - Adjust resolution/samples based on GPU VRAM
   - Use lower intensity for faster convergence

---

## Support Resources

- **Full Implementation Docs**: `OPTIX_DENOISING_IMPLEMENTATION.md`
- **Server Logs**: Check for denoiser warnings/errors
- **Blender OptiX Docs**: https://docs.blender.org/manual/en/latest/render/cycles/render_settings/denoising.html

