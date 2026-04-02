# Logging and Error Handling System

This document describes the comprehensive logging and error tracking system for the Blender Remote GPU addon and server.

## Overview

The system provides:

- **Error Codes** — 40+ error codes covering all failure modes (connection, rendering, GPU, etc.)
- **Structured Logging** — JSON and human-readable formats with context (session ID, operation ID, duration)
- **User-Friendly Errors** — Technical errors mapped to clear messages + recovery suggestions
- **Performance Tracking** — Automatic timing and metrics logging
- **Full Traceability** — Every operation has a unique ID for debugging

## Architecture

### Files

```
shared/
  error_codes.py        - 40+ error codes with metadata
  logging_config.py     - Centralized logging setup
addon/
  error_handler.py      - UI error display + tracking
  engine.py             - Instrumented with logging calls
server/
  server.py             - Instrumented with logging calls
```

## Error Codes

All error codes follow this pattern: `CATEGORY_NUMBER` (e.g., `CONN_001`)

### Categories

| Category | Codes | Examples |
|----------|-------|----------|
| **CONN** | 001-006 | Connection timeout, refused, lost, WebSocket upgrade failed |
| **SYNC** | 001-006 | Scene corrupt, texture missing, size exceeded, load failed |
| **RENDER** | 001-007 | Scene load failed, GPU unavailable, timeout, invalid settings |
| **SCENE** | 001-004 | Invalid geometry, missing files, unsupported features, no camera |
| **GPU** | 001-005 | Out of VRAM, driver crash, thermal throttle, not found, fragmentation |
| **ENCODE** | 001-004 | JPEG decode failed, EXR decode failed, encode failed, unsupported format |
| **PROTO** | 001-004 | Version mismatch, invalid format, size exceeded, serialization failed |
| **SYS** | 001-005 | Missing dependency, Blender not found, disk full, out of RAM, high load |
| **PERF** | 001-004 | Slow network, high latency, low frame rate, slow encoding |
| **AUTH** | 001-003 | Permission denied, addon not enabled, auth failed |
| **INFO** | 001-003 | Connected, synced, render complete (success messages) |

### Error Code Structure

Each error code has:

```python
ErrorCode(
    code="CONN_001",                                    # Unique ID
    category="connection",                               # Grouping
    severity="CRITICAL",                                 # CRITICAL/ERROR/WARNING/INFO
    message="Connection timeout while connecting to...", # Technical description
    user_message="Could not reach the server...",       # User-friendly message
    recovery_suggestions=[                              # Recovery steps
        "Check that the server IP and port are correct",
        "Verify the Windows machine with GPU is powered on",
        ...
    ]
)
```

### Looking Up Error Codes

```python
from shared.error_codes import ErrorCodes

# By code string
error = ErrorCodes.by_code("CONN_001")
print(error.user_message)  # User-friendly message
print(error.recovery_suggestions)  # List of recovery steps

# By category
conn_errors = ErrorCodes.by_category("connection")
for error in conn_errors:
    print(f"{error.code}: {error.message}")

# Get all codes
all_codes = ErrorCodes.get_all_codes()
```

## Logging Configuration

### Setup (in addon `__init__.py` or server `server.py`)

```python
from shared.logging_config import setup_logging

# One-time setup
setup_logging(
    name="remote-gpu.addon",        # Logger name prefix
    log_file="~/.blender/addon.log", # Optional file logging
    log_level="INFO",                # DEBUG/INFO/WARNING/ERROR/CRITICAL
    use_json=False,                  # JSON or human-readable format
    max_bytes=10*1024*1024,          # Rotate when log reaches 10MB
    backup_count=5,                  # Keep 5 rotated logs
)
```

### Output Formats

#### Human-Readable (default)

```
2026-04-02 15:42:31.123 [ERROR   ] remote-gpu.addon         view_update          L 184 (CONN_003) op=a1b2c3d4 Connection lost
2026-04-02 15:42:32.456 [INFO    ] remote-gpu.addon         render               L 50  op=a1b2c3d4 Render completed successfully
```

#### JSON (use_json=True)

```json
{
  "timestamp": "2026-04-02T15:42:31.123456",
  "level": "ERROR",
  "logger": "remote-gpu.addon",
  "message": "Connection lost",
  "module": "engine",
  "function": "view_update",
  "line": 184,
  "error_code": "CONN_003",
  "operation_id": "a1b2c3d4",
  "session_id": "sess_1234",
  "duration_ms": 1234.5
}
```

## Using the Logger

### Get a Logger

```python
from shared.logging_config import get_logger

logger = get_logger("module_name")
logger.debug("Debug message")
logger.info("Info message")
logger.warning("Warning message")
logger.error("Error message")
logger.critical("Critical message")
```

### LogContext — Track Operations

Automatically logs entry, exit, duration, and success/failure:

```python
from shared.logging_config import LogContext
import uuid

operation_id = str(uuid.uuid4())[:12]

with LogContext("render", logger, operation_id) as ctx:
    ctx.log_info("Starting render...")
    # ... do work ...
    ctx.log_info("Render complete")
    # Duration and success automatically logged on exit
```

### PerformanceLogger — Track Timings

```python
from shared.logging_config import PerformanceLogger

perf = PerformanceLogger(logger, "frame_decode")
perf.mark("start")
pixels = decode_frame(data)
perf.mark("decoded")
upload_to_gpu(pixels)
perf.mark("uploaded")
perf.log_summary()
# Output: "Performance summary for frame_decode: start=0.0ms, decoded=45.2ms, uploaded=67.5ms"
```

### Error with Code

```python
from shared.logging_config import log_error_with_code

try:
    result = render()
except Exception as e:
    log_error_with_code(
        logger,
        operation_id="abc123",
        error_code="RENDER_001",
        message="Render failed",
        exception=e,
        extra={"resolution": "1920x1080", "samples": 128}
    )
```

### Performance Metrics

```python
from shared.logging_config import log_performance

start = time.time()
result = do_work()
elapsed = time.time() - start

log_performance(
    logger,
    operation="scene_sync",
    duration_ms=elapsed * 1000,
    success=True,
    metrics={
        "size_mb": 500.5,
        "upload_speed_mbps": 45.2,
        "chunks": 20,
    }
)
```

## Error Handling in Addon

### Show Error to User

```python
from addon.error_handler import show_error

try:
    conn.connect()
except ConnectionError as e:
    show_error("CONN_001", "Server IP: 192.168.1.100", e)
    # Shows user-friendly message + recovery suggestions
    # Logs full traceback internally
```

### Show Warning

```python
from addon.error_handler import show_warning

if latency > 500:
    show_warning("PERF_002", f"Network latency: {latency}ms")
```

### Show Info

```python
from addon.error_handler import show_info

show_info("Render complete in 45 seconds")
```

### Record Error for Session Tracking

```python
from addon.error_handler import record_error

try:
    render()
except Exception as e:
    show_error("RENDER_001", str(e), e)
    record_error("RENDER_001", "final_render", duration_ms=5000)
```

### Get Error Summary

```python
from addon.error_handler import get_error_summary, get_error_log

# Display errors that occurred in session
summary = get_error_summary()
print(summary)
# Output: "Error summary: 3 total errors\n  CONN_003: 1x\n  PERF_002: 2x"

# Export for diagnostics
log_data = get_error_log()
# Send to server for debugging
```

## Server Logging

### Starting Server with Logging

```bash
# Console only (INFO level)
python server.py --port 9876

# With log file
python server.py --port 9876 --log-file server.log

# Debug mode with JSON logs
python server.py --log-level DEBUG --json-logs --log-file server.json.log

# Custom Blender path
python server.py --blender /opt/blender/blender --log-file server.log
```

### Server Instrumentation

Every operation logs:
- Entry with context (operation_id, session_id)
- Progress updates
- Exit with duration and metrics
- Any errors with error code

Example server output:

```
2026-04-02 15:42:30.123 [INFO] remote-gpu.server Client connected: 192.168.1.50:54321
2026-04-02 15:42:30.124 [INFO] remote-gpu.server [sess_abc1] Scene upload: 125.5 MB
2026-04-02 15:42:31.456 [INFO] remote-gpu.server [sess_abc1] Scene saved: 0.98s
2026-04-02 15:42:32.789 [INFO] remote-gpu.server [sess_abc1] Scene loaded: 0.45s
2026-04-02 15:42:35.012 [INFO] remote-gpu.server [sess_abc1] op=render_op1 Starting final render: 1920x1080, 128 samples
2026-04-02 15:42:45.234 [INFO] remote-gpu.server [sess_abc1] op=render_op1 Render complete: 2450.5 KB in 10.22s
2026-04-02 15:42:50.456 [INFO] remote-gpu.server [sess_abc1] Client disconnected: 192.168.1.50:54321
```

## Addon Logging

### Engine Logging

The `engine.py` file logs all render operations:

```python
# Final render
with LogContext("final_render", logger, operation_id) as ctx:
    ctx.log_info("Syncing scene...")
    conn.send_scene(blend_data)
    ctx.log_info("Scene synced")

    ctx.log_info("Starting render...")
    conn.send_render_start(...)

    result_data = conn.get_final_frame(timeout=1.0)
    if result_data:
        ctx.log_info("Render complete")
    else:
        ctx.log_error("Render timeout")
        show_error("RENDER_003", "No result after 300s")
```

### Viewport Logging

```python
# Viewport updates
with LogContext("view_update", logger, operation_id) as ctx:
    if not scene_synced:
        ctx.log_info("First sync: sending full scene...")
        conn.send_scene(blend_data)
    else:
        delta = compute_scene_delta(context)
        if delta:
            ctx.log_info(f"Delta sync: {delta_size} bytes (saved X MB)")
            conn.send(MsgType.SCENE_DELTA, delta_dict)
```

## Performance Metrics

All major operations log performance metrics:

| Operation | Metrics |
|-----------|---------|
| **Scene Upload** | size_mb, save_ms, load_ms |
| **Final Render** | resolution, samples, output_size_kb, duration_ms |
| **Viewport Render** | frames_delivered, avg_frame_time_ms, total_duration_ms |
| **Frame Decode** | decoded_ms, uploaded_ms, total_ms |

Example metrics output (JSON):

```json
{
  "timestamp": "2026-04-02T15:42:45.234",
  "level": "INFO",
  "message": "Performance summary for final_render",
  "duration_ms": 10220.5,
  "success": true,
  "metrics": {
    "resolution": "1920x1080",
    "samples": 128,
    "output_size_kb": 2450.5
  }
}
```

## Debugging with Logs

### Finding Errors

```bash
# Show all errors in log
grep ERROR server.log

# Show specific error code
grep CONN_001 server.log

# Show a session's activity
grep "sess_abc1" server.log

# Trace an operation
grep "op=render_op1" server.log
```

### JSON Log Analysis

```bash
# Pretty-print JSON logs
cat server.json.log | jq '.'

# Filter by error
cat server.json.log | jq 'select(.level == "ERROR")'

# Filter by operation
cat server.json.log | jq 'select(.operation_id == "render_op1")'

# Extract metrics
cat server.json.log | jq '.metrics'
```

### Common Error Resolution

| Error | Resolution |
|-------|-----------|
| CONN_001 (timeout) | Check server IP/port, verify server running |
| CONN_003 (lost) | Check network, look for server crashes in logs |
| RENDER_001 (load failed) | Check Blender version match, verify scene integrity |
| RENDER_002 (GPU unavailable) | Close GPU-using apps, check nvidia-smi |
| RENDER_003 (timeout) | Reduce samples/resolution, check GPU load |
| SYNC_001 (corrupt) | Re-save Blender file, check file size |
| SYNC_002 (texture missing) | Pack textures: File > External Data > Pack All |

## Best Practices

### In Addon Code

```python
import logging
from shared.logging_config import get_logger, LogContext, PerformanceLogger
from addon.error_handler import show_error, record_error

logger = get_logger("module_name")

def my_operation():
    operation_id = str(uuid.uuid4())[:12]
    perf = PerformanceLogger(logger, "my_operation")

    with LogContext("my_operation", logger, operation_id) as ctx:
        try:
            ctx.log_info("Starting operation...")
            perf.mark("phase1")

            # Do work...
            result = do_work()

            perf.mark("phase2")
            ctx.log_info(f"Operation complete: {result}")
            perf.log_summary()

        except Exception as e:
            elapsed = perf.mark("error")
            ctx.log_error(f"Operation failed: {e}")
            show_error("ERR_CODE", str(e), e)
            record_error("ERR_CODE", "my_operation", elapsed)
```

### In Server Code

```python
from shared.logging_config import LogContext, log_error_with_code, log_performance

async def handle_request(ws, data, session_id):
    operation_id = str(uuid.uuid4())[:12]

    with LogContext("operation_name", logger, operation_id) as ctx:
        ctx.log_info(f"[{session_id}] Starting operation...")

        try:
            start = time.time()
            result = await do_work(data)
            elapsed = time.time() - start

            ctx.log_info(f"Operation complete")
            log_performance(logger, "operation_name", elapsed * 1000, True, {
                "input_size": len(data),
                "output_size": len(result),
            })

        except Exception as e:
            elapsed = time.time() - start
            ctx.log_error(f"Operation failed: {e}")
            log_error_with_code(logger, operation_id, "ERROR_CODE", str(e), e)
            log_performance(logger, "operation_name", elapsed * 1000, False)
            await send_error(ws, "ERROR_CODE", str(e))
```

## Testing Error Handling

### Simulate Errors

```python
# In addon for testing
from addon.error_handler import show_error

# Simulate connection error
show_error("CONN_001", "Simulated timeout for testing")

# Simulate render error
show_error("RENDER_003", "Simulated render timeout (5 minutes)")

# Check error summary
from addon.error_handler import get_error_summary
print(get_error_summary())
```

### Check Logs

```bash
# Watch logs in real-time
tail -f server.log

# Watch JSON logs
tail -f server.json.log | jq '.'

# Parse errors by category
grep ERROR server.log | cut -d'[' -f2 | cut -d']' -f1 | sort | uniq -c
```

## Migration from Old print() Statements

### Before

```python
print("[Remote GPU] Connecting to server...")
print(f"[Remote GPU] Error: {e}")
```

### After

```python
import logging
from shared.logging_config import get_logger

logger = get_logger("connection")
logger.info("Connecting to server...")
logger.error(f"Error: {e}", exc_info=e)
```

---

**Last Updated**: 2026-04-02
**Version**: 1.0
**Status**: Production-Ready
