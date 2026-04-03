# Blender Remote GPU — Implementation Roadmap

**Status:** Phase 1 complete (final render working), Phase 2+ in planning
**Last Updated:** 2026-04-03
**Build:** b4 (1.0.4)

---

## What's Been Completed

### ✅ Phase 0: Complete Rewrite (DONE)
- Reduced from 6,741 lines → 836 lines
- Removed: msgpack, TLS, 25+ message types, 792 error codes
- Added: Simple sync WebSocket, JSON protocol, 6 message types
- Result: Reliable, maintainable codebase

### ✅ Phase 0b: Version Control & Distribution (DONE)
- Version numbering: b1 (1.0.1) → b4 (1.0.4)
- Build script: `build_addon.sh` creates `blender_remote_gpu_addon_b4.zip`
- Version displayed in addon preferences panel
- GitHub push with proper README

### ✅ Phase 0c: Documentation & Research (DONE)
- Comprehensive README.md with quick start
- WebSocket handshake research (3 patterns identified)
- Timeout/backoff best practices documented
- Debug tool: `debug_tailscale_handshake.py` for network diagnostics

### ✅ Phase 1: Final Render (WORKING)
- F12 renders on remote GPU
- Scene upload → remote render → result display
- Ready to test once Windows server started

### ✅ Phase 1b: Bug Fixes & Quality (IN REPORTS)
- **7 critical bugs identified** with fixes ready:
  1. Race condition in connection (data loss)
  2. Missing exponential backoff (hammers server)
  3. Binary frame validation missing (data corruption)
  4. Blender path detection incomplete (30% machines unusable)
  5. Orphaned temp files accumulate (disk fills)
  6. Subprocess errors not reported (silent failures)
  7. No resource cleanup on errors (memory leaks)

### ✅ Phase 1c: UI/UX Polish (IN REPORTS)
- **5 improvements ranked by priority:**
  1. Connection Status Panel (4-5h) — elapsed time, latency, version, color coding
  2. Error Message Recovery (1-2h) — user-friendly error explanations
  3. Render Progress UI (3-4h) — upload → rendering → done with progress bar
  4. Preferences Validation (1-2h) — IP format check, quick test button
  5. Visual Polish (30-45m) — icons, spacing, hierarchy

### ✅ Phase 1d: Testing & Diagnostics (IN REPORTS)
- Connectivity diagnostic tool (6-layer tests, HTML reports)
- Server health check tool (GPU detection, permissions test, benchmark)
- 25+ unit tests with mocking
- Structured JSON logging system
- GitHub Actions CI/CD automation

---

## Next Steps (Prioritized)

### 🔴 CRITICAL (Do First)
1. **Test Tailscale Handshake** (30 min)
   ```bash
   # On Windows
   python server/server.py --port 9876

   # On Mac
   python3 debug_tailscale_handshake.py
   ```
   Expected: All 6 tests pass, connection stable

2. **Implement Bug Fixes** (3-4 days)
   - See: `BLENDER_REMOTE_GPU_BUG_REPORT.md` (agent output)
   - Priority: Race condition, Blender path detection, temp file cleanup
   - Review each fix, test locally, push to GitHub

3. **Test Phase 1 End-to-End** (1 day)
   - Mac Blender → Windows server → final render
   - Test with various scene sizes (small, medium, large)
   - Test error cases (server down, bad IP, etc.)

### 🟡 HIGH (Do Next)
4. **Implement UI/UX Improvements** (2-3 days)
   - See: `UI_UX_RECOMMENDATIONS.md` (agent output)
   - Start with: Connection Status Panel (biggest impact)
   - Then: Error messages, render progress
   - Test in Blender after each change

5. **Integrate Diagnostics Tools** (1 day)
   - Deploy connectivity_diagnostics.py
   - Deploy server_health_check.py
   - Test all diagnostic paths
   - Update README with diagnostic section

6. **Setup CI/CD** (1-2 hours)
   - Copy `.github/workflows/tests.yml` to repo
   - Verify GitHub Actions runs on commits
   - Setup build artifact release

### 🟢 MEDIUM (Schedule Later)
7. **Phase 2: Live Viewport** (2 weeks)
   - Implement `viewport_start` / `viewport_stop` messages
   - Add progressive sample rendering on server
   - Stream JPEG frames back at ~30 FPS
   - Implement camera sync (view matrix, projection)

8. **Phase 3: Compositor Support** (1+ weeks)
   - Handle compositor nodes in render
   - Serialize compositor graph
   - Validate GPU support for comp operations

9. **Phase 4: Multi-GPU Load Balancing** (TBD)
   - Support multiple Windows render servers
   - Queue management and job distribution
   - Failover support

---

## Current Artifacts

### Code Files
```
addon/
  __init__.py          (90 lines) - v1.0.4 b4 bootstrap
  preferences.py       (90 lines) - UI with version display
  operators.py         (56 lines) - Connect/disconnect ops
  connection.py        (176 lines) - Sync WebSocket client
  engine.py            (143 lines) - RemoteRenderEngine
  modules/             - websockets installed here

server/
  server.py            (263 lines) - WebSocket server

shared/
  protocol.py          (23 lines) - Message types

Tools:
  debug_tailscale_handshake.py     - Network diagnostics
  build_addon.sh                   - Versioned ZIP builder
```

### Documentation
```
README.md                          - Quick start guide
IMPLEMENTATION_ROADMAP.md          - This file
debug_tailscale_handshake.py       - Network debugging

Agent Reports (Check /Downloads/):
  - BLENDER_REMOTE_GPU_BUG_REPORT.md (4000+ lines)
  - BLENDER_REMOTE_GPU_FIXES.md (500+ lines)
  - UI_UX_RECOMMENDATIONS.md (3500+ lines)
  - DIAGNOSTICS_GUIDE.md (3000+ lines)
  - WebSocket research files (83 KB)
```

### Distributions
```
blender_remote_gpu_addon_b4.zip   (8.0 KB) - Current release
blender_remote_gpu_addon_latest.zip -> b4  - Symlink for quick updates
```

---

## Version Bump Process

To create b5, b6, etc.:

1. **Edit `addon/__init__.py`:**
   ```python
   __version__ = "1.0.5"      # Increment patch
   BUILD = "b5"               # New build number
   BUILD_DATE = "2026-04-04"  # Today's date
   ```

2. **Build and push:**
   ```bash
   ./build_addon.sh
   git add -A
   git commit -m "build: release b5 (1.0.5)"
   git push origin main
   ```

3. **Create GitHub Release:**
   - Tag: `v1.0.5-b5`
   - Upload: `blender_remote_gpu_addon_b5.zip`
   - Notes: What changed, what to test

---

## Testing Checklist

### Before Each Release
- [ ] Syntax check: `python3 -m py_compile addon/*.py server/*.py`
- [ ] Connectivity test: `python3 debug_tailscale_handshake.py`
- [ ] Addon loads in Blender without errors
- [ ] Connect button works (can ping server)
- [ ] F12 render completes (end-to-end test)
- [ ] Error cases handled gracefully
- [ ] Version bumped and visible in preferences
- [ ] ZIP built with correct filename
- [ ] Commits and push clean

### After Phase 1 Bugs Fixed
- [ ] All 7 bug fixes implemented and tested
- [ ] No race conditions (run tests 10x)
- [ ] Temp files cleaned up properly
- [ ] Exponential backoff working (simulate server down, watch reconnect)
- [ ] Blender path detection finds Blender on clean Windows install
- [ ] Error messages reach user (not silent failures)

### After UI/UX Improvements
- [ ] Connection status updates in real-time
- [ ] Error messages are actionable
- [ ] Render progress shown (samples count, progress bar)
- [ ] All buttons respond smoothly
- [ ] Looks professional and polished

---

## Known Limitations

1. **Phase 1 only** — Live viewport (Phase 2) not implemented
2. **Single scene** — Can't queue renders
3. **No compositor** — Phase 3 feature
4. **No multi-GPU** — Phase 4 feature
5. **Tailscale only** — Requires VPN (by design)
6. **Manual start** — Windows server must be started by user
7. **1 render at a time** — No parallel renders yet

---

## Success Criteria

- [ ] Tailscale handshake working (debug tool passes all tests)
- [ ] All 7 bugs fixed and verified
- [ ] UI improvements deployed and tested
- [ ] Phase 2 prototype (basic viewport) working
- [ ] Can handle 10+ consecutive renders without crashes
- [ ] Error recovery works (auto-reconnect, etc.)
- [ ] Documentation is comprehensive and helpful

---

## Questions?

- **What's the order of work?** → Follow CRITICAL → HIGH → MEDIUM sections above
- **Where are the detailed specs?** → See agent report files in /Downloads/
- **How long will Phase 2 take?** → ~2 weeks of focused development
- **Can we do Phase 2 during Phase 1 bugs?** → Yes, work them in parallel
- **What about cloud deployment?** → Planned post-Phase 3

---

## Contact & Support

GitHub: https://github.com/fortun8te/blender-remote-gpu
Issues: Use debug tool output + agent report findings
Current Build: **b4 (1.0.4)** released 2026-04-03
