# Diagnostics & Testing Suite - Complete Index

Quick navigation guide to all diagnostic tools, tests, and documentation.

## Start Here

1. **First Time?** → Read `docs/QUICK_REFERENCE.md` (5 minutes)
2. **Need Help?** → Read `docs/DIAGNOSTICS_GUIDE.md` (complete guide)
3. **Want to Deploy?** → Follow `IMPLEMENTATION_CHECKLIST.md` (step-by-step)
4. **Want Details?** → Read `DIAGNOSTICS_SUMMARY.md` (what was built)

---

## Tools & Commands

### Connectivity Testing

**Command:** `python tools/connectivity_diagnostics.py`

**Options:**
```bash
--host HOST          Server IP (default: 100.74.135.83)
--port PORT          Server port (default: 9876)
--timeout SECONDS    Test timeout (default: 10)
--json FILE          Export JSON report
--html FILE          Export HTML report
```

**Example:**
```bash
python tools/connectivity_diagnostics.py \
  --json connectivity.json \
  --html connectivity.html
```

**What it tests:**
- Tailscale tunnel status
- TCP socket connectivity
- WebSocket handshake
- PING/PONG protocol (5 cycles)
- Timeout recovery behavior
- Connection stability over 30 seconds

**Documentation:** `tools/README.md` (Connectivity Diagnostics section)

---

### Server Health Check

**Command:** `python tools/server_health_check.py`

**Options:**
```bash
--blender PATH      Path to Blender executable
--json FILE         Export JSON report
```

**Example:**
```bash
python tools/server_health_check.py \
  --blender /usr/bin/blender \
  --json health.json
```

**What it checks:**
- Blender executable availability and version
- NVIDIA GPU presence and VRAM
- Temporary directory permissions
- Scene file creation and opening
- Render performance (GPU benchmark)

**Documentation:** `tools/README.md` (Server Health Check section)

---

### Unit Tests

**Command:** `pytest tests/test_addon_connection.py -v`

**Options:**
```bash
-v              Verbose output
--tb=short      Short traceback format
--cov=addon     Coverage report
-k TEST_NAME    Run specific test
```

**Example:**
```bash
pytest tests/test_addon_connection.py -v --cov=addon
```

**What it tests:**
- Connection initialization
- Message queueing (FIFO)
- Binary data handling
- Thread safety
- Error handling
- Module import caching
- End-to-end message flow

**Documentation:** `tests/test_addon_connection.py` (inline comments)

---

### Logging System

**Usage in Code:**
```python
from addon.logging_system import get_logger, Severity

logger = get_logger(debug=True)

# Log events
logger.info("Component", "Message", details={"key": "value"})
logger.warning("Component", "Warning")
logger.error("Component", "Error", details={"exception": str(e)})

# Export
path = logger.export_session_log()
logger.print_statistics()
```

**Log Location:** `~/.blender/remote-gpu/logs/remote_gpu_YYYY-MM-DD.log`

**Documentation:** `addon/logging_system.py` (docstrings and comments)

---

### GitHub Actions Workflow

**Trigger:** Automatically on every commit

**View Results:**
1. Go to GitHub repository
2. Click "Actions" tab
3. Find your commit
4. View test results

**What it checks:**
- Python syntax (flake8)
- Code style (black, isort)
- Unit tests
- Addon structure
- Diagnostic tools validation
- Build artifacts

**Configuration:** `.github/workflows/tests.yml`

---

## Documentation Files

### Quick Start (5-10 minutes)

| File | Length | Purpose |
|------|--------|---------|
| `CREATED_FILES_SUMMARY.txt` | 300 lines | What was created |
| `docs/QUICK_REFERENCE.md` | 200 lines | Essential commands & reference |

**Start with:** `docs/QUICK_REFERENCE.md`

---

### Complete Guides (30-60 minutes)

| File | Length | Purpose |
|------|--------|---------|
| `docs/DIAGNOSTICS_GUIDE.md` | 2000+ lines | Complete usage & troubleshooting |
| `tools/README.md` | 300 lines | Tool architecture & integration |

**Then read:** `docs/DIAGNOSTICS_GUIDE.md`

---

### Implementation & Overview (20-30 minutes)

| File | Length | Purpose |
|------|--------|---------|
| `DIAGNOSTICS_SUMMARY.md` | 400 lines | What was implemented & features |
| `IMPLEMENTATION_CHECKLIST.md` | 300 lines | 10-phase deployment guide |

**For deployment:** `IMPLEMENTATION_CHECKLIST.md`
**For overview:** `DIAGNOSTICS_SUMMARY.md`

---

## File Locations

### Diagnostic Tools
```
tools/
├── connectivity_diagnostics.py    Network testing tool
├── server_health_check.py        Server validation tool
└── README.md                     Tools documentation
```

### Addon Components
```
addon/
└── logging_system.py             Structured JSON logging

tests/
└── test_addon_connection.py      Unit tests (25+ tests)
```

### Automation
```
.github/
└── workflows/
    └── tests.yml                 GitHub Actions CI/CD
```

### Documentation
```
docs/
├── DIAGNOSTICS_GUIDE.md          Complete guide (2000+ lines)
└── QUICK_REFERENCE.md            Quick reference (200 lines)

Root:
├── DIAGNOSTICS_SUMMARY.md        Implementation summary
├── IMPLEMENTATION_CHECKLIST.md   10-phase setup
└── CREATED_FILES_SUMMARY.txt     What was created
```

### Log Storage
```
~/.blender/remote-gpu/logs/
├── remote_gpu_2026-04-03.log     Today's log
├── remote_gpu_2026-04-02.log     Previous log
└── ... (auto-rotated, keeps last 10)
```

---

## Common Workflows

### "I have a connection problem"

1. Run: `python tools/connectivity_diagnostics.py`
2. Review console output
3. If still unclear, export: `python tools/connectivity_diagnostics.py --json issue.json --html issue.html`
4. Attach JSON/HTML to GitHub issue
5. Read troubleshooting section in `docs/DIAGNOSTICS_GUIDE.md`

---

### "The render server won't work"

1. Run: `python tools/server_health_check.py`
2. Review readiness score
3. Fix any failed checks
4. Re-run to verify

---

### "I want to verify code quality"

1. Run tests: `pytest tests/ -v`
2. Check style: `black --check addon/`
3. Check lint: `flake8 addon/`
4. Check imports: `isort --check addon/`

---

### "I need to report a bug"

1. Run diagnostics:
   ```bash
   python tools/connectivity_diagnostics.py --json conn.json --html conn.html
   python tools/server_health_check.py --json health.json
   ```

2. Export addon logs:
   ```python
   from addon.logging_system import get_logger
   logger = get_logger()
   logger.export_session_log()
   ```

3. Attach to GitHub issue:
   - conn.json & conn.html
   - health.json
   - session logs
   - Steps to reproduce
   - Expected vs actual behavior

---

### "I want to monitor connection health"

```bash
#!/bin/bash
# Save as monitor.sh
while true; do
    python tools/connectivity_diagnostics.py \
        --json "logs/diag_$(date +%s).json"
    sleep 300  # Every 5 minutes
done
```

---

## Test Coverage

### Unit Tests (pytest)
- Connection initialization
- Message queueing (FIFO)
- Thread-safe operations
- Error handling
- Module import caching
- End-to-end flows
- **Total:** 25+ test cases

### Integration Tests (manual)
- Real server connectivity
- Network latency measurement
- Timeout recovery
- Connection stability over time

### CI/CD Tests (GitHub Actions)
- Python syntax validation
- Code style checking
- Addon structure verification
- Diagnostic tool validation
- Build artifact verification

---

## Performance Characteristics

| Operation | Time | Memory | Network |
|-----------|------|--------|---------|
| Connectivity Diagnostics | 45-60s | <20MB | 100KB |
| Server Health Check | 30-60s | <20MB | None |
| Unit Tests | 5-10s | <30MB | None |
| Logging | Negligible | <10MB | None |
| Export Report | 1-2s | <10MB | None |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All tests passed ✅ |
| 1 | Some tests failed ❌ |

**For GitHub Actions:** Green checkmark = all passed, Red X = something failed

---

## Environment Variables (Optional)

```bash
# Enable debug logging in addon
export REMOTE_GPU_DEBUG=1

# Custom Blender path
export REMOTE_GPU_BLENDER=/path/to/blender
```

---

## Getting Help

1. **Quick answers:** `docs/QUICK_REFERENCE.md`
2. **Detailed help:** `docs/DIAGNOSTICS_GUIDE.md`
3. **How to deploy:** `IMPLEMENTATION_CHECKLIST.md`
4. **What's included:** `DIAGNOSTICS_SUMMARY.md`
5. **Tool internals:** `tools/README.md`

---

## Version Information

- **Addon Version:** 1.0.4
- **Diagnostics Suite:** 1.0 (Initial Release)
- **Created:** 2026-04-03
- **Status:** Production Ready ✅

---

## Quick Links

- Start here → `docs/QUICK_REFERENCE.md`
- Setup → `IMPLEMENTATION_CHECKLIST.md`
- Complete guide → `docs/DIAGNOSTICS_GUIDE.md`
- What's built → `DIAGNOSTICS_SUMMARY.md`
- Tool details → `tools/README.md`
- File summary → `CREATED_FILES_SUMMARY.txt`

---

**Last Updated:** 2026-04-03
**Diagnostics Suite Version:** 1.0
