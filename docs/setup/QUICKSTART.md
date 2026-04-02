# ⚡ QUICKSTART — Get Running in 5 Minutes

## Windows (RTX 5090 Server)

1. **Find your IP:**
   ```
   ipconfig
   ```
   Look for "IPv4 Address" (e.g., `192.168.1.100`)

2. **Start server:**
   - Double-click `start_server.bat`
   - OR run: `python3 server/server.py --gpu optix`
   - Wait for: `✅ Server listening on port 9876`

3. **Done.** Server is ready.

---

## Mac (Blender UI)

1. **Edit config (one-time):**
   ```bash
   nano shared/dev_config.py
   ```
   Change:
   ```python
   REMOTE_SERVER_IP = "192.168.1.100"  # ← YOUR WINDOWS IP
   AUTO_CONNECT = True
   ```
   Save (Ctrl+O, Enter, Ctrl+X)

2. **Install addon:**
   - Open Blender 4.0+
   - Preferences → Add-ons → Install from File
   - Select `addon/` folder
   - Enable "Remote GPU Render"

3. **Connect:**
   - Should auto-connect immediately
   - Watch console: Window → Toggle System Console
   - Look for: `✅ Auto-connected successfully`

4. **Test:**
   - Open any .blend
   - Switch to "Remote Cycles" render engine (top right)
   - Viewport should show live preview from Windows GPU
   - Hit F12 for final render

5. **Done!**

---

## Fast Development

**Edit code → See changes instantly (no restart):**

```bash
# Edit addon code
# Then:
python scripts/dev_reload.py
```

Or auto-reload on every change:
```bash
python scripts/dev_reload.py --watch
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Connection refused | Check Windows server running (`start_server.bat`) |
| Auto-connect fails | Check IP is correct in `dev_config.py` |
| GPU not found on Windows | Run `nvidia-smi` — check RTX 5090 visible |
| Changes don't reload | Run `python scripts/dev_reload.py` or restart Blender |
| Viewport shows nothing | Check Render Engine is set to "Remote Cycles" |

---

## Performance Targets

- **Viewport:** 20-50ms per frame (live preview)
- **Multi-viewport:** 2-4 simultaneous at different resolutions
- **Animation:** Timeline playback at 30fps
- **Final render:** All passes streamed progressively (beauty first)

---

## Next Steps

1. ✅ Windows server running
2. ✅ Mac addon connected
3. ⏭️ Read `DEV_SETUP.md` for full dev workflow
4. ⏭️ Read `ARCHITECTURE.md` for technical details
5. ⏭️ Check `docs/` folder for protocol specs

---

**Questions?** Check logs in Blender: Window → Toggle System Console
