# Diagnostic Tools for Blender Remote GPU

Production-ready diagnostic and testing utilities for the Blender Remote GPU addon.

## Overview

This directory contains comprehensive tools for:

- **Connectivity Diagnostics** - Network and WebSocket protocol testing
- **Server Health Check** - Render server validation and benchmarking
- **Logging System** - Structured JSON logging for debugging
- **Unit Tests** - Automated testing of addon components

## Tools

### 1. Connectivity Diagnostics

**File:** `connectivity_diagnostics.py`

**Purpose:** Comprehensive network and WebSocket connectivity testing.

**What it tests:**
- Tailscale tunnel status
- TCP socket connectivity
- WebSocket handshake
- PING/PONG protocol
- Timeout recovery behavior
- Connection flakiness over time

**Usage:**

```bash
# Basic test
python connectivity_diagnostics.py

# With custom server
python connectivity_diagnostics.py --host 192.168.1.100 --port 9876

# Export reports
python connectivity_diagnostics.py --json report.json --html report.html

# Custom timeout
python connectivity_diagnostics.py --timeout 20
```

**Output:**
- Console summary with test results
- Optional JSON export for programmatic use
- Optional HTML report for sharing
- Latency statistics (min/max/mean/P95/P99)
- Network flakiness metrics

**Key Classes:**
- `DiagnosticReporter` - Collects and formats test results
- `LatencyHistogram` - Tracks latency statistics
- `BandwidthTester` - Estimates connection bandwidth
- `ConnectivityDiagnostics` - Main test suite

### 2. Server Health Check

**File:** `server_health_check.py`

**Purpose:** Validates that the render server is properly configured.

**What it checks:**
- Blender executable availability
- NVIDIA GPU presence and VRAM
- Temp directory write permissions
- Ability to create and open .blend files
- GPU render performance benchmark

**Usage:**

```bash
# Basic check
python server_health_check.py

# Specify Blender path
python server_health_check.py --blender /usr/bin/blender

# Export results
python server_health_check.py --json health_report.json
```

**Output:**
- Console summary with pass/fail for each check
- Readiness score (0-100%)
- GPU information (name, VRAM, driver version)
- Render benchmark results (fps, duration)
- Optional JSON export

**Key Classes:**
- `ServerHealthChecker` - Main health check system

### 3. Addon Logging System

**File:** `../addon/logging_system.py`

**Purpose:** Structured JSON logging for the addon.

**Features:**
- Structured JSON format for all logs
- Automatic log rotation (keeps last 10 files)
- Debug mode with verbose output
- Thread-safe logging
- Session tracking and export
- Optional anonymized telemetry
- Log statistics and analysis

**Usage:**

```python
# Get logger
from addon.logging_system import get_logger, Severity

logger = get_logger(debug=True)

# Log events
logger.info("Connection", "Server connected", details={"gpu": "RTX 3090"})
logger.warning("Memory", "Low VRAM warning")
logger.error("Render", "Render failed", details={"error": "GPU error"})

# Export logs
path = logger.export_session_log()
print(f"Logs exported to: {path}")

# Get statistics
stats = logger.get_statistics()
logger.print_statistics()
```

**Log Location:**
```
~/.blender/remote-gpu/logs/remote_gpu_YYYY-MM-DD.log
```

**Key Classes:**
- `StructuredLogger` - Main logging system
- `LogEntry` - Single log entry
- `LogRotationHandler` - File rotation management
- `TelemetryCollector` - Optional telemetry

### 4. Unit Tests

**File:** `../tests/test_addon_connection.py`

**Purpose:** Automated testing of addon components.

**Test Coverage:**
- Connection class initialization
- Message queueing (FIFO order)
- Thread-safe concurrent operations
- Error handling and edge cases
- WebSocket module import and caching
- Successful connection flow
- Message send/receive cycles

**Usage:**

```bash
# Run tests
pytest tests/test_addon_connection.py -v

# With coverage
pytest tests/test_addon_connection.py --cov=addon --cov-report=html

# Run specific test
pytest tests/test_addon_connection.py::TestConnectionClass::test_connection_initialization -v
```

**Key Test Classes:**
- `TestConnectionClass` - Connection initialization and state
- `TestConnectionQueueing` - Message queue behavior
- `TestConnectionErrorHandling` - Error handling
- `TestWebSocketModuleImport` - Module import logic
- `TestConnectionIntegration` - End-to-end flows

## Installation

### Requirements

```bash
pip install websockets pytest pytest-cov
```

### Optional

```bash
# For advanced diagnostics
pip install numpy scipy

# For better HTML reports
pip install jinja2
```

## Usage Examples

### Example 1: Basic Connectivity Check

```bash
$ python connectivity_diagnostics.py

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

### Example 2: Export for Bug Report

```bash
# Collect all diagnostics
python connectivity_diagnostics.py --json conn.json --html conn.html
python server_health_check.py --json health.json

# Addon logs (from Blender)
# See addon/logging_system.py

# Create GitHub issue with attachments:
# - conn.json
# - conn.html
# - health.json
# - session logs from ~/.blender/remote-gpu/logs/
```

### Example 3: CI/CD Integration

```bash
# GitHub Actions automatically runs:
# - Python syntax validation
# - Addon structure validation
# - Addon import tests
# - Unit tests
# - Diagnostic tool validation

# View results: GitHub Actions tab
```

### Example 4: Continuous Monitoring

```bash
#!/bin/bash
# Monitor server connectivity every 5 minutes
while true; do
    python connectivity_diagnostics.py \
        --json "logs/diag_$(date +%s).json"
    sleep 300
done
```

## Output Formats

### JSON Export

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
      "message": "Tailscale running, server active",
      "details": {
        "output_lines": 5
      },
      "timestamp": "2026-04-03T14:22:15.234567"
    }
  ]
}
```

### HTML Report

Generated HTML report with:
- Summary statistics
- Color-coded test results (pass/fail)
- Detailed test information
- Downloadable JSON data
- Professional styling

### Log Format

```json
{
  "timestamp": "2026-04-03T14:22:15.123456",
  "severity": "INFO",
  "component": "Connection",
  "message": "Connection established",
  "details": {
    "host": "100.74.135.83",
    "port": 9876,
    "gpu_name": "NVIDIA RTX 3090",
    "latency_ms": 45.23
  },
  "context_id": "a1b2c3d4"
}
```

## Architecture

### Connectivity Diagnostics Flow

```
ConnectivityDiagnostics (main class)
├── test_tailscale_status()
├── test_tcp_connectivity()
├── test_websocket_handshake()
├── test_ping_pong_protocol()
├── test_timeout_recovery()
└── test_connection_flakiness()
    └── LatencyHistogram (statistics)
        └── BandwidthTester (calculations)
            └── DiagnosticReporter (results)
                ├── to_json()
                ├── to_html()
                └── export_*()
```

### Server Health Check Flow

```
ServerHealthChecker (main class)
├── check_blender_executable()
├── check_gpu_presence()
├── check_temp_directory_permissions()
├── check_blender_scene_file()
├── benchmark_render_performance()
└── calculate_readiness_score()
```

### Logging System Flow

```
StructuredLogger
├── LogEntry (individual log)
├── LogRotationHandler (file management)
├── TelemetryCollector (optional)
└── Export/Statistics
    ├── export_session_log()
    ├── get_statistics()
    └── to_json()
```

## Error Handling

All tools implement robust error handling:

1. **Timeout handling** - Graceful timeout with clear error messages
2. **Connection failures** - Retry logic with exponential backoff
3. **File I/O errors** - Fallback to in-memory buffering
4. **Permission errors** - Detailed error reporting
5. **Module import errors** - Helpful installation instructions

## Performance

### Typical Execution Times

| Tool | Duration | Notes |
|------|----------|-------|
| Connectivity Diagnostics | 45-60s | Includes 30s flakiness test |
| Server Health Check | 30-60s | Includes render benchmark |
| Unit Tests | 5-10s | Full test suite |

### Resource Usage

- **Memory:** < 50MB for all tools
- **CPU:** Minimal (mostly I/O bound)
- **Network:** ~100KB traffic for diagnostics

## Troubleshooting

### "websockets module not installed"

```bash
pip install websockets
```

### "nvidia-smi not found"

NVIDIA GPU drivers not installed. Install from: https://www.nvidia.com/Download/driverDetails.aspx

### "Blender executable not found"

```bash
# Install Blender or specify path
python server_health_check.py --blender /path/to/blender
```

### Permission Denied

```bash
# Make scripts executable
chmod +x connectivity_diagnostics.py
chmod +x server_health_check.py
```

## Contributing

To add new diagnostic tests:

1. Add test method to appropriate class
2. Use `add_result()` to record result
3. Update test count in documentation
4. Add unit test if applicable

## CI/CD Integration

The GitHub Actions workflow automatically:

1. Validates Python syntax
2. Runs unit tests
3. Checks addon structure
4. Validates diagnostic tools
5. Tests mock connections

View results in: GitHub Repo → Actions tab

## Support

For issues or questions:

1. Run diagnostic: `python connectivity_diagnostics.py`
2. Check logs: `cat ~/.blender/remote-gpu/logs/*.log`
3. Review documentation in `../docs/DIAGNOSTICS_GUIDE.md`
4. Open GitHub issue with diagnostic exports

## License

Same as Blender Remote GPU addon

## Authors

Blender Remote GPU Development Team

---

**Version:** 1.0.4 | **Updated:** 2026-04-03
