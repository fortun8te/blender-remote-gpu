# Blender Remote GPU - Diagnostics & Testing Guide

Complete documentation for all diagnostic and testing tools included with the Blender Remote GPU addon.

## Table of Contents

1. [Quick Start](#quick-start)
2. [Connectivity Diagnostics](#connectivity-diagnostics)
3. [Server Health Check](#server-health-check)
4. [Addon Testing](#addon-testing)
5. [Logging & Telemetry](#logging--telemetry)
6. [Bug Reporting](#bug-reporting)
7. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Installation

All diagnostic tools are included in the addon package.

```bash
# From the project root
cd /path/to/blender-remote-gpu
```

### Run Connectivity Check

```bash
python tools/connectivity_diagnostics.py
```

### Run Server Health Check

```bash
python tools/server_health_check.py
```

### Export for Bug Report

```bash
# Export connectivity diagnostics as JSON
python tools/connectivity_diagnostics.py --json /tmp/connectivity_report.json --html /tmp/connectivity_report.html

# Export server health as JSON
python tools/server_health_check.py --json /tmp/health_report.json
```

---

## Connectivity Diagnostics

### Purpose

Verifies network connectivity and WebSocket protocol compliance between your client and the render server.

### Usage

#### Basic Check

```bash
python tools/connectivity_diagnostics.py
```

**Output:**
```
╔════════════════════════════════════════════════════════════╗
║  Blender Remote GPU — Connectivity Diagnostic Suite v2     ║
╚════════════════════════════════════════════════════════════╝

Target: 100.74.135.83:9876
Timeout: 10s
Start: 2026-04-03 14:22:15

[1/6] Testing Tailscale status...
[2/6] Testing TCP connectivity...
[3/6] Testing WebSocket handshake...
[4/6] Testing PING/PONG protocol...
[5/6] Testing timeout recovery...
[6/6] Testing connection flakiness...

============================================================
DIAGNOSTICS SUMMARY
============================================================
Tests Passed: 6/6
Success Rate: 100.0%
Total Duration: 45.23s

✅ All tests passed! Addon should work correctly.
```

#### Custom Server

```bash
python tools/connectivity_diagnostics.py --host 192.168.1.100 --port 9876 --timeout 15
```

#### Export Results

```bash
# JSON export for bug reports
python tools/connectivity_diagnostics.py --json results.json

# HTML report for sharing with developers
python tools/connectivity_diagnostics.py --html results.html

# Both
python tools/connectivity_diagnostics.py --json results.json --html results.html
```

### Tests Performed

#### 1. Tailscale Status
- **What it checks:** Is Tailscale running and is the server machine active?
- **Why it matters:** Tailscale provides the encrypted tunnel. If it's not active, WebSocket won't connect.
- **Failure causes:**
  - Tailscale daemon not running
  - Server IP not in Tailscale network
  - Server machine offline

#### 2. TCP Connectivity
- **What it checks:** Can the client reach the server on the specified port?
- **Why it matters:** WebSocket runs on top of TCP. If TCP fails, WebSocket will fail.
- **Failure causes:**
  - Firewall blocking the port
  - Server not listening
  - Network unreachable

#### 3. WebSocket Handshake
- **What it checks:** Can a WebSocket connection be established?
- **Why it matters:** Verifies the WebSocket protocol negotiation succeeds.
- **Failure causes:**
  - Server not running
  - Port mismatch
  - Incompatible WebSocket versions

#### 4. PING/PONG Protocol
- **What it checks:** Can the client send 5 PING messages and receive PONG responses?
- **Latency metrics:**
  - Min/Max/Mean latency in milliseconds
  - P95/P99 percentiles
  - Standard deviation (jitter)
- **Failure causes:**
  - Server not responding
  - Protocol mismatch
  - Network congestion

#### 5. Timeout Recovery
- **What it checks:** Can the connection gracefully recover from timeouts?
- **Why it matters:** Real network conditions sometimes have brief outages. Recovery is essential.
- **Failure causes:**
  - Connection state not properly reset
  - Server not accepting reconnections

#### 6. Connection Flakiness
- **What it checks:** Connection stability over 30 seconds
- **Metrics collected:**
  - Success rate (should be ≥95%)
  - Max consecutive failures
  - Latency jitter
  - Statistics for all latency samples
- **Failure causes:**
  - Intermittent network issues
  - WiFi interference
  - Server overload

### Interpreting Results

#### Success Rate 100%
```
✅ All tests passed! Addon should work correctly.
```
Your connection is perfect. Rendering should work.

#### Success Rate 80-99%
```
⚠️  Most tests passed. Check details above for warnings.
```
Some tests failed but core functionality should work. Review the detailed output to fix non-critical issues.

#### Success Rate < 80%
```
❌ Multiple failures detected. See details for troubleshooting.
```
Multiple tests failed. Review the detailed output and troubleshooting section.

### JSON Export Format

```json
{
  "summary": {
    "total_tests": 6,
    "passed": 6,
    "failed": 0,
    "success_rate": 100.0,
    "total_duration": 45.23,
    "timestamp": "2026-04-03T14:22:15.123456"
  },
  "results": [
    {
      "name": "Tailscale Status",
      "passed": true,
      "duration": 0.234,
      "message": "Tailscale running, server 100.74.135.83 is active",
      "details": {
        "output_lines": 5
      },
      "timestamp": "2026-04-03T14:22:15.234567"
    }
  ]
}
```

---

## Server Health Check

### Purpose

Validates that the **render server** (the Windows/Linux machine) is properly configured and ready to render.

### Usage

#### Basic Check

```bash
python tools/server_health_check.py
```

**Output:**
```
╔════════════════════════════════════════════════════════════╗
║       Blender Remote GPU — Server Health Check Tool        ║
╚════════════════════════════════════════════════════════════╝

Platform: Linux-5.15.0-1234-generic-x86_64-generic
Start: 2026-04-03 14:25:00

[1/5] Checking Blender executable...
[2/5] Checking GPU presence...
[3/5] Checking temp directory permissions...
[4/5] Testing Blender scene file...
[5/5] Running render benchmark...

============================================================
SERVER HEALTH CHECK SUMMARY
============================================================
✅ PASS | Blender Executable              | 1.234s
✅ PASS | GPU Presence                    | 0.456s
✅ PASS | Temp Directory Permissions      | 0.123s
✅ PASS | Blender Scene File              | 2.345s
✅ PASS | Render Benchmark                | 15.678s

============================================================
Readiness Score: 100.0%
============================================================

✅ Server is ready for rendering!
```

#### Specify Blender Path

```bash
python tools/server_health_check.py --blender /usr/bin/blender
```

#### Export Results

```bash
python tools/server_health_check.py --json health_report.json
```

### Checks Performed

#### 1. Blender Executable
- **What it checks:** Is Blender installed and executable?
- **Details collected:**
  - Blender path
  - Version string
- **Failure causes:**
  - Blender not installed
  - Blender not in PATH
  - Missing execute permissions

#### 2. GPU Presence
- **What it checks:** Is an NVIDIA GPU detected with CUDA drivers?
- **Details collected:**
  - GPU name(s)
  - Total VRAM
  - Free VRAM
  - Driver version
- **Failure causes:**
  - No NVIDIA GPU
  - Missing nvidia-smi (CUDA drivers not installed)
  - GPU not properly detected

#### 3. Temp Directory Permissions
- **What it checks:** Can the server write to temporary files?
- **Details collected:**
  - Temp directory path
  - Read/write capabilities
- **Failure causes:**
  - Insufficient permissions
  - Disk full
  - Mounted read-only filesystem

#### 4. Blender Scene File
- **What it checks:** Can Blender create and open .blend files?
- **Why it matters:** The server must save scenes to disk.
- **Failure causes:**
  - Blender installation corrupt
  - File permission issues
  - Insufficient disk space

#### 5. Render Benchmark
- **What it checks:** How fast can the GPU render?
- **Details collected:**
  - Render duration (seconds)
  - Frames per second
  - Output file size
- **Failure causes:**
  - GPU not properly configured for CUDA
  - Insufficient VRAM for rendering
  - Cycles engine issues

### Readiness Score

The health check calculates a readiness score (0-100%):

| Score  | Status | Action |
|--------|--------|--------|
| 90-100 | Ready  | ✅ Can render |
| 70-89  | Partial | ⚠️ Fix warnings first |
| < 70   | Not Ready | ❌ Major issues to fix |

### JSON Export Format

```json
{
  "timestamp": "2026-04-03T14:25:00.123456",
  "readiness_score": 100.0,
  "results": [
    {
      "component": "Blender Executable",
      "healthy": true,
      "message": "Blender found and accessible",
      "details": {
        "path": "/usr/bin/blender",
        "version_output": "Blender 4.0.0 (hash abc123def456)"
      },
      "duration": 1.234,
      "timestamp": "2026-04-03T14:25:00.234567"
    }
  ]
}
```

---

## Addon Testing

### Unit Tests

Run tests for the addon connection module:

```bash
cd tests
pytest test_addon_connection.py -v
```

**Test coverage:**
- Connection initialization
- Connection timeout handling
- Message queueing (FIFO order)
- Thread-safe concurrent sends/receives
- Error handling and edge cases
- Module import and caching

### Running Tests in Blender

If you have Blender installed, you can run tests from within Blender:

```python
# In Blender Python console
import sys
sys.path.append('/path/to/blender-remote-gpu/tests')
import unittest
loader = unittest.TestLoader()
suite = loader.discover('.', pattern='test_*.py')
runner = unittest.TextTestRunner(verbosity=2)
runner.run(suite)
```

---

## Logging & Telemetry

### Enabling Logging

The addon includes a comprehensive structured logging system.

#### In Addon Code

```python
from addon.logging_system import get_logger, Severity

logger = get_logger(debug=True)

# Log events
logger.info("Component", "Message", details={"key": "value"})
logger.warning("Component", "Warning message")
logger.error("Component", "Error message", details={"exception": str(e)})
```

#### Log Locations

- **macOS:** `~/.blender/remote-gpu/logs/`
- **Linux:** `~/.blender/remote-gpu/logs/`
- **Windows:** `%APPDATA%\Blender\scripts\addons\remote-gpu-render\logs\`

#### Log File Format

Each line is a JSON object:

```json
{
  "timestamp": "2026-04-03T14:22:15.123456",
  "severity": "INFO",
  "component": "Connection",
  "message": "Connection established",
  "details": {
    "host": "100.74.135.83",
    "port": 9876,
    "gpu_name": "NVIDIA RTX 3090"
  },
  "context_id": "a1b2c3d4"
}
```

### Exporting Logs

```python
# Get current session log
logger = get_logger()
logs = logger.get_session_log()

# Export to file
path = logger.export_session_log()
print(f"Exported to: {path}")

# Print statistics
logger.print_statistics()
```

### Log Statistics

```
============================================================
SESSION LOG STATISTICS
============================================================
Session ID: 550e8400-e29b
Total Entries: 124
Duration: 45.3s

Severity Counts:
  DEBUG: 42
  INFO: 64
  WARNING: 12
  ERROR: 6
  CRITICAL: 0

Component Counts:
  Connection: 28
  Render: 45
  Network: 31
  Other: 20
```

### Optional Telemetry

The addon can send anonymized telemetry data to help with development (opt-in):

```python
from addon.logging_system import TelemetryCollector

telemetry = TelemetryCollector(enabled=True, logger=logger)

# Record events
telemetry.record_connection_attempt(success=True, duration_ms=234.5)
telemetry.record_render_attempt(success=True, duration_ms=15000)

# Export
telemetry.export_telemetry("telemetry.json")
```

**Telemetry data includes:**
- Anonymous machine ID (hashed)
- Event type (connection, render)
- Success/failure status
- Duration in milliseconds
- Timestamps

**Telemetry does NOT include:**
- Personal information
- File paths
- Scene content
- Server addresses
- User identity

---

## Bug Reporting

### Gathering Diagnostics for a Bug Report

Follow these steps to collect all necessary information:

#### 1. Connectivity Check

```bash
python tools/connectivity_diagnostics.py \
  --json /tmp/connectivity.json \
  --html /tmp/connectivity.html
```

#### 2. Server Health Check

```bash
python tools/server_health_check.py \
  --json /tmp/health.json
```

#### 3. Export Addon Logs

```python
# In Blender Python console or as a script
from addon.logging_system import get_logger

logger = get_logger()
log_path = logger.export_session_log()
print(f"Logs exported to: {log_path}")
```

#### 4. Create Bug Report

Include the following when reporting bugs:

```markdown
## Bug Report: [Title]

### Environment
- **Blender Version:** 4.0.0
- **OS:** macOS 13.2 / Windows 11 / Ubuntu 22.04
- **Addon Version:** 1.0.4

### Description
[Describe the issue]

### Steps to Reproduce
1. [First step]
2. [Second step]
3. [Expected result]
4. [Actual result]

### Attached Diagnostic Reports
1. **connectivity.json** - Network connectivity test results
2. **connectivity.html** - Formatted network report
3. **health.json** - Server health check results
4. **addon_logs_*.json** - Addon session logs
5. **blender_output.log** - Blender console output (if applicable)

### Additional Info
[Any additional context]
```

---

## Troubleshooting

### Connection Issues

#### "Connection refused"
**Causes:**
- Server not running
- Wrong host/port
- Firewall blocking connection

**Fix:**
1. Verify server is running: `netstat -an | grep 9876`
2. Check firewall: `sudo iptables -L` (Linux) or Windows Defender settings
3. Verify Tailscale is active: `tailscale status`

#### "Connection timeout"
**Causes:**
- Server not responding
- Network latency too high
- Firewall silently dropping packets

**Fix:**
1. Check network latency: `ping 100.74.135.83`
2. Run connectivity diagnostics to check each layer
3. Review server logs for errors

#### "WebSocket handshake failed"
**Causes:**
- WebSocket module not installed
- Protocol version mismatch
- Server websocket library incompatible

**Fix:**
```bash
# Ensure websockets is installed
pip install websockets
```

### Rendering Issues

#### "Scene upload failed"
**Causes:**
- Server out of disk space
- Network timeout during upload
- Blender scene file too large

**Fix:**
1. Check server disk: `df -h`
2. Verify scene file is under 2GB
3. Check network bandwidth with diagnostics tool

#### "GPU out of memory"
**Causes:**
- Scene too complex
- Render resolution too high
- Multiple renders running

**Fix:**
1. Check available VRAM: `nvidia-smi`
2. Reduce render samples or resolution
3. Wait for other renders to complete

#### "Render never completes"
**Causes:**
- Scene has infinite samples
- GPU driver crash
- Network disconnection during render

**Fix:**
1. Set sample limit in scene settings
2. Update GPU drivers: `nvidia-smi --query-gpu=driver_version`
3. Check for network flakiness with diagnostics

### File Permission Issues

#### "Cannot write to temp directory"
**Causes:**
- Disk full
- Wrong filesystem permissions
- Running as different user

**Fix:**
```bash
# Check disk space
df -h

# Fix permissions
chmod 755 /tmp

# Run as same user as Blender
```

### Performance Issues

#### "Rendering is very slow"
**Causes:**
- GPU not being used (CPU fallback)
- Low VRAM (swap overhead)
- Network bottleneck

**Fix:**
1. Verify GPU is being used: `nvidia-smi` (should show process)
2. Check VRAM: ensure scene fits in VRAM
3. Run bandwidth test in diagnostics

#### "High network latency"
**Causes:**
- WiFi interference
- Network congestion
- Large packet loss

**Fix:**
1. Check latency: `ping -c 100 100.74.135.83`
2. Switch to wired connection if possible
3. Check for other network-heavy applications

---

## Advanced Usage

### Custom Diagnostic Scripts

Create your own diagnostic script:

```python
from tools.connectivity_diagnostics import ConnectivityDiagnostics

# Create diagnostics with custom settings
diag = ConnectivityDiagnostics(
    host="192.168.1.100",
    port=9876,
    timeout=20
)

# Run all tests
summary = diag.run_full_suite()

# Export results
diag.export_json("results.json")
diag.export_html("results.html")

# Print summary
diag.print_summary()
```

### Continuous Monitoring

Monitor connection health over time:

```bash
#!/bin/bash
# Monitor every 5 minutes
while true; do
    python tools/connectivity_diagnostics.py \
        --json "logs/diag_$(date +%s).json"
    sleep 300
done
```

### CI/CD Integration

The GitHub Actions workflow automatically runs tests on every commit. View results in the Actions tab of the repository.

---

## Support

For issues or questions:

1. Check this guide's troubleshooting section
2. Review the diagnostic reports (JSON/HTML)
3. Check addon logs in `~/.blender/remote-gpu/logs/`
4. Open an issue on GitHub with diagnostic reports attached

---

**Last Updated:** 2026-04-03
**Addon Version:** 1.0.4
