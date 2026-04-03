# Quick Reference - Diagnostics & Testing

One-page reference for all diagnostic tools and commands.

## Tools Overview

| Tool | Purpose | Command |
|------|---------|---------|
| **Connectivity Diagnostics** | Test network & WebSocket | `python tools/connectivity_diagnostics.py` |
| **Server Health Check** | Verify server readiness | `python tools/server_health_check.py` |
| **Addon Unit Tests** | Test connection module | `pytest tests/test_addon_connection.py -v` |
| **Addon Logging** | Structured logging system | Built into addon |

## Common Commands

### Quick Diagnostics

```bash
# Test connection to server
python tools/connectivity_diagnostics.py

# Check server health
python tools/server_health_check.py

# Run addon tests
pytest tests/ -v
```

### Export Diagnostics

```bash
# Connectivity report (JSON + HTML)
python tools/connectivity_diagnostics.py --json conn.json --html conn.html

# Server health report (JSON)
python tools/server_health_check.py --json health.json

# Addon logs (from within Blender)
# See "Logging & Telemetry" section below
```

### Custom Server

```bash
# Test specific host/port
python tools/connectivity_diagnostics.py --host 192.168.1.100 --port 9876

# With custom timeout
python tools/connectivity_diagnostics.py --timeout 20
```

## Test Coverage

### Connectivity Tests (6 tests)

1. ✅ **Tailscale Status** - Is Tailscale running?
2. ✅ **TCP Connectivity** - Can TCP connection be established?
3. ✅ **WebSocket Handshake** - Does WebSocket protocol work?
4. ✅ **PING/PONG Protocol** - Can messages be exchanged?
5. ✅ **Timeout Recovery** - Can connection recover from timeouts?
6. ✅ **Connection Flakiness** - Is connection stable?

### Server Health Tests (5 tests)

1. ✅ **Blender Executable** - Is Blender installed?
2. ✅ **GPU Presence** - Is NVIDIA GPU available?
3. ✅ **Temp Permissions** - Can write to temp directory?
4. ✅ **Scene File** - Can open .blend files?
5. ✅ **Render Benchmark** - How fast can GPU render?

## Interpreting Results

### Success Rate

| Rate | Meaning | Action |
|------|---------|--------|
| 100% | Perfect | ✅ Everything works |
| 80-99% | Good | ⚠️ Minor issues only |
| < 80% | Bad | ❌ Major issues to fix |

### Readiness Score

| Score | Status | Can Render? |
|-------|--------|-----------|
| 90-100 | Ready | ✅ Yes |
| 70-89 | Partial | ⚠️ Probably |
| < 70 | Not Ready | ❌ No |

## Logging

### Location

```
~/.blender/remote-gpu/logs/remote_gpu_YYYY-MM-DD.log
```

### Format

Each line is JSON:
```json
{
  "timestamp": "2026-04-03T14:22:15.123456",
  "severity": "INFO",
  "component": "Connection",
  "message": "Connection established",
  "details": { "host": "100.74.135.83" }
}
```

### Enable Debug Logging

```python
from addon.logging_system import get_logger
logger = get_logger(debug=True)
```

## Troubleshooting Matrix

### Problem: Can't connect

| Check | Command |
|-------|---------|
| Network reachable? | `ping 100.74.135.83` |
| Port open? | `netstat -an \| grep 9876` |
| Tailscale active? | `tailscale status` |
| WebSocket working? | `python tools/connectivity_diagnostics.py` |

### Problem: Render fails

| Check | Command |
|-------|---------|
| Server ready? | `python tools/server_health_check.py` |
| GPU present? | `nvidia-smi` |
| Blender works? | `blender --version` |
| Disk space? | `df -h /tmp` |

### Problem: Slow rendering

| Check | Command |
|-------|---------|
| GPU in use? | `nvidia-smi` (look for process) |
| Low latency? | Check P95 in `connectivity_diagnostics.py` |
| High VRAM? | Check free memory in `nvidia-smi` |
| Network bandwidth? | Run diagnostics, check flakiness |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All tests passed ✅ |
| 1 | Some tests failed ❌ |

## File Locations

### Addon Files
```
addon/
├── __init__.py           # Addon entry point
├── connection.py         # WebSocket client
├── engine.py            # Render engine
├── operators.py         # UI operators
├── preferences.py       # Settings
└── logging_system.py    # Logging
```

### Tools
```
tools/
├── connectivity_diagnostics.py  # Network testing
└── server_health_check.py       # Server validation
```

### Tests
```
tests/
└── test_addon_connection.py     # Unit tests
```

## Quick Bug Report

When reporting a bug, include:

```bash
# Step 1: Connectivity test
python tools/connectivity_diagnostics.py --json bug_conn.json --html bug_conn.html

# Step 2: Server health
python tools/server_health_check.py --json bug_health.json

# Step 3: Logs (from Blender console)
# See addon/logging_system.py docs

# Step 4: Create GitHub issue with attachments
# - bug_conn.json
# - bug_health.json
# - session logs (*.json from ~/.blender/remote-gpu/logs/)
```

## Environment Variables

```bash
# Enable debug logging (addon)
export REMOTE_GPU_DEBUG=1

# Custom Tailscale interface
export REMOTE_GPU_TAILNET=my-tailnet
```

## GitHub Actions

Tests run automatically on every commit. View results:
1. Go to GitHub repo
2. Click "Actions" tab
3. Find your commit
4. View test results

## Need Help?

1. Run diagnostic: `python tools/connectivity_diagnostics.py`
2. Check logs: `cat ~/.blender/remote-gpu/logs/*.log`
3. Review DIAGNOSTICS_GUIDE.md for detailed help
4. Open GitHub issue with diagnostic JSON exports

---

**Version:** 1.0.4 | **Updated:** 2026-04-03
