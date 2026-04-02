# Quick Logging Reference

## Import Statements

```python
# Logging
from shared.logging_config import get_logger, LogContext, PerformanceLogger, log_error_with_code, log_performance

# Error handling (addon only)
from addon.error_handler import show_error, show_warning, show_info, record_error

# Error codes
from shared.error_codes import ErrorCodes
```

## Basic Logging

```python
logger = get_logger("module_name")

logger.debug("Debug message")
logger.info("Info message")
logger.warning("Warning message")
logger.error("Error message")
logger.critical("Critical message")
logger.exception("Error with traceback", exc_info=exc)
```

## Track an Operation

```python
import uuid

operation_id = str(uuid.uuid4())[:12]

with LogContext("operation_name", logger, operation_id) as ctx:
    ctx.log_info("Starting...")
    try:
        # Do work
        pass
    except Exception as e:
        ctx.log_error(f"Failed: {e}")
    # Automatically logs duration and success/failure on exit
```

## Measure Performance

```python
perf = PerformanceLogger(logger, "operation_name")

perf.mark("step1_start")
# ... work ...
perf.mark("step2_start")
# ... work ...
perf.log_summary()
# Output: "step1_start=0.0ms, step2_start=45.2ms"
```

## Log Error with Code

```python
log_error_with_code(
    logger,
    operation_id="abc123",
    error_code="RENDER_001",
    message="Render failed",
    exception=e,
    extra={"resolution": "1920x1080"}
)
```

## Log Performance Metrics

```python
log_performance(
    logger,
    operation="scene_sync",
    duration_ms=elapsed * 1000,
    success=True,
    metrics={"size_mb": 125.5, "speed_mbps": 45.0}
)
```

## Show Error to User (Addon Only)

```python
# Show error with recovery suggestions
show_error("CONN_001", "Server not responding", exception)

# Show warning
show_warning("PERF_002", "High latency detected")

# Show info
show_info("Operation completed successfully")

# Record error for session tracking
record_error("CONN_001", "connection", duration_ms=1234)

# Get summary of errors in session
print(get_error_summary())
```

## Common Error Codes

| Code | Meaning |
|------|---------|
| CONN_001 | Connection timeout |
| CONN_002 | Connection refused |
| CONN_003 | Connection lost |
| RENDER_001 | Scene load failed |
| RENDER_002 | GPU not available |
| RENDER_003 | Render timeout |
| SYNC_001 | Scene corrupt |
| SYNC_002 | Texture missing |
| SYNC_004 | Scene load failed |
| GPU_001 | GPU out of VRAM |
| GPU_002 | GPU driver error |

See `shared/error_codes.py` for all 40+ codes.

## Server Startup

```bash
# Default (console, INFO level)
python server.py --port 9876

# With file logging
python server.py --port 9876 --log-file server.log

# Debug with JSON format
python server.py --log-level DEBUG --json-logs --log-file server.json

# Custom Blender path
python server.py --blender /path/to/blender --log-file server.log
```

## Set Up Logging (Addon)

In addon `__init__.py`:

```python
from shared.logging_config import setup_logging

setup_logging(
    name="remote-gpu.addon",
    log_file="~/.blender/addon.log",
    log_level="INFO",
    use_json=False,
)
```

## Session ID Tracking

Every client session gets a unique ID automatically logged:

```python
# In server session handler
operation_id = str(uuid.uuid4())[:12]  # Per operation
session_id = str(uuid.uuid4())[:8]     # Per client session

logger.info(f"[{session_id}] op={operation_id} Starting render...")
# Later: grep "session_abc1" server.log  (to see all ops in session)
# Or:    grep "op=render_op1" server.log (to trace specific operation)
```

## Example: Complete Instrumented Function

```python
import uuid
import time
from shared.logging_config import get_logger, LogContext, PerformanceLogger

logger = get_logger("example")

def process_scene(scene_data):
    operation_id = str(uuid.uuid4())[:12]
    perf = PerformanceLogger(logger, "process_scene")

    with LogContext("process_scene", logger, operation_id) as ctx:
        ctx.log_info(f"Processing {len(scene_data)} bytes")

        try:
            # Phase 1
            perf.mark("parse")
            scene = parse_scene(scene_data)
            ctx.log_info("Scene parsed")

            # Phase 2
            perf.mark("validate")
            errors = validate_scene(scene)
            if errors:
                ctx.log_warning(f"{len(errors)} validation issues")

            # Phase 3
            perf.mark("optimize")
            scene = optimize_scene(scene)
            ctx.log_info("Scene optimized")

            perf.mark("complete")
            perf.log_summary()

            log_performance(logger, "process_scene", perf.metrics["complete"], True, {
                "input_size": len(scene_data),
                "validation_issues": len(errors),
            })

            return scene

        except Exception as e:
            ctx.log_error(f"Processing failed: {e}")
            from addon.error_handler import show_error
            show_error("SYNC_001", "Scene parsing failed", e)
            raise
```

## Viewing Logs

### Console (Real-time)

```bash
# Follow server logs
tail -f server.log

# Follow with errors highlighted
tail -f server.log | grep --color=always "ERROR\|WARNING\|$"
```

### Analysis

```bash
# Count error codes
grep ERROR server.log | grep -oP '\[\K[A-Z]+_\d+' | sort | uniq -c

# Errors by category
grep ERROR server.log | grep -oP '\[\K[A-Z]+' | sort | uniq -c

# Sessions and durations
grep "operation\|duration_ms" server.json.log | jq '.session_id, .duration_ms'

# Performance outliers
jq 'select(.duration_ms > 5000)' server.json.log
```

## Integration Checklist

- [x] Error codes defined (`shared/error_codes.py`)
- [x] Logging configured (`shared/logging_config.py`)
- [x] Error handler UI (`addon/error_handler.py`)
- [x] Engine instrumented (`addon/engine.py`)
- [x] Server instrumented (`server/server.py`)
- [ ] Add logging calls to other modules as needed:
  - `addon/connection.py` — Add logging for connect/disconnect/send/receive
  - `addon/sync.py` — Add logging for scene encoding/decoding
  - `addon/frame.py` — Add logging for frame encoding/decoding
  - `server/scene_manager.py` — Add logging for scene management
  - `server/renderer.py` — Add logging for render operations
  - `server/viewport_live.py` — Add logging for viewport rendering

## Troubleshooting

### No logs appearing

```python
# Check logging is initialized
from shared.logging_config import setup_logging
setup_logging(name="remote-gpu.addon", log_level="DEBUG")

# Check logger is created correctly
logger = logging.getLogger("remote-gpu.addon")
logger.info("Test message")  # Should appear
```

### Logs not rotating

```python
# Verify in setup_logging call
setup_logging(
    ...
    max_bytes=10*1024*1024,  # Rotate at 10MB
    backup_count=5,          # Keep 5 files
)
```

### Too much logging

```python
# Lower log level
setup_logging(..., log_level="WARNING")

# Filter in addon
logger.setLevel(logging.WARNING)
```

### Can't find operation in logs

```bash
# Use operation ID
grep "op=abc123def456" server.log

# Or session ID
grep "sess_xyz" server.log

# Show last 100 operations
tail -100 server.log | grep "op="
```

---

**Version**: 1.0
**Date**: 2026-04-02
