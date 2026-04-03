# Windows Deployment Checklist — Blender Remote GPU b37

**Build:** b37 (1.0.37)
**Release Date:** 2026-04-03
**Target Platform:** Windows 10/11 with RTX GPU

---

## Table of Contents
1. [Pre-Deployment Setup](#pre-deployment-setup)
2. [Installation Steps](#installation-steps)
3. [Configuration](#configuration)
4. [Testing & Verification](#testing--verification)
5. [Monitoring & Troubleshooting](#monitoring--troubleshooting)
6. [Rollback Procedure](#rollback-procedure)
7. [Long-Term Operations](#long-term-operations)

---

## Pre-Deployment Setup

### System Requirements

**GPU Machine (Render Worker):**
- Windows 10 or later
- NVIDIA RTX GPU (RTX 3060 or higher recommended)
- NVIDIA CUDA 11.x+ and cuDNN installed
- Python 3.10 or later
- At least 8GB free disk space for temp renders
- Network connectivity to coordinator machine (port 6969)

**Coordinator Machine (Server):**
- Windows 10 or later
- Python 3.10 or later
- Network connectivity to GPU machine (port 6969)
- Blender 4.0+ for addon testing
- At least 1GB free disk space for logs

### Pre-Deployment Checklist

- [ ] Verify Python version: `python --version` (must be 3.10+)
- [ ] Verify GPU drivers: `nvidia-smi` shows RTX GPU with CUDA support
- [ ] Check disk space: `diskpart` → `list disk` → verify 8GB+ free on render disk
- [ ] Check network: `ping <coordinator_ip>` should respond
- [ ] Disable antivirus scanning of work directories (optional, for performance)
- [ ] Create service user account (optional, for running as service)

---

## Installation Steps

### Step 1: Download Build b37

```powershell
# Download b37 release
# File: remote_gpu_render_b37.zip (if available from repo)
# Or extract from git: git clone ... && git checkout b37

$InstallDir = "C:\BlenderRemoteGPU"
New-Item -ItemType Directory -Path $InstallDir -Force
```

### Step 2: Extract and Verify Files

```powershell
# Extract zip to installation directory
Expand-Archive -Path "remote_gpu_render_b37.zip" -DestinationPath $InstallDir

# Verify key files exist
$RequiredFiles = @(
    "render_worker.py",
    "server.py",
    "remote_gpu_render\__init__.py",
    "test_b35_fixes.py"
)

foreach ($file in $RequiredFiles) {
    if (Test-Path "$InstallDir\$file") {
        Write-Host "✓ $file found" -ForegroundColor Green
    } else {
        Write-Host "✗ $file MISSING" -ForegroundColor Red
        exit 1
    }
}
```

### Step 3: Install Python Dependencies

```powershell
# Navigate to install directory
cd $InstallDir

# Create virtual environment (recommended)
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install --upgrade pip setuptools wheel
pip install numpy pillow bpy  # Blender API
pip install requests          # HTTP client
pip install pycryptodome       # Optional, for future security

# Verify installations
python -c "import bpy; print(f'Blender API v{bpy.app.version_string}')"
python -c "import numpy; print(f'NumPy v{numpy.__version__}')"
```

### Step 4: Verify Build Syntax

```powershell
# Test Python syntax
python -m py_compile render_worker.py
python -m py_compile server.py

# Should complete with no output (no syntax errors)
Write-Host "✓ Syntax verification passed" -ForegroundColor Green
```

### Step 5: Run Test Suite

```powershell
# Run comprehensive test suite
python test_b35_fixes.py

# Expected output:
# [PASS] render_worker.py improvements (12/13 - 1 test outdated)
# [PASS] server.py polling logic (8/8)
# [PASS] Logging structure (12/12)
# [PASS] Context override safety
# [PASS] Thread safety
# 4/5 test groups passed
```

---

## Configuration

### Step 1: Configure Network Addresses

**On GPU Worker Machine:**

```powershell
# Determine local IP address
ipconfig /all | findstr "IPv4 Address"

# Note the IP (e.g., 192.168.1.100)
# This IP is used by coordinator to connect to worker
```

**On Coordinator Machine:**

Create or edit `server_config.txt`:
```
[server]
port = 8888
host = 0.0.0.0  # Listen on all interfaces

[worker]
ip = 192.168.1.100       # GPU machine IP
port = 6969              # Worker port (hardcoded in render_worker.py)
max_wait = 120           # Wait up to 120s for scene load
```

### Step 2: Configure Firewall Rules

```powershell
# On GPU Worker Machine
# Allow inbound on port 6969
New-NetFirewallRule -DisplayName "Blender Remote GPU Worker" `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 6969

# On Coordinator Machine
# Allow outbound to 192.168.1.100:6969
New-NetFirewallRule -DisplayName "Blender Remote GPU Client" `
    -Direction Outbound -Action Allow -Protocol TCP `
    -RemoteAddress 192.168.1.100 -RemotePort 6969
```

### Step 3: Configure Logging (Optional)

Create `logging_config.json`:
```json
{
  "log_dir": "C:\\BlenderRemoteGPU\\logs",
  "log_level": "INFO",
  "max_log_size_mb": 100,
  "rotation_count": 10,
  "structured_tags": [
    "[LOAD_START]",
    "[LOAD_COMPLETE]",
    "[LOAD_ERROR]",
    "[TIMEOUT]",
    "[RETRY]",
    "[RECOVERY]",
    "[SHUTDOWN]"
  ]
}
```

Create log directory:
```powershell
$LogDir = "C:\BlenderRemoteGPU\logs"
New-Item -ItemType Directory -Path $LogDir -Force
```

---

## Testing & Verification

### Test 1: Worker Startup Test

**On GPU Worker Machine:**

```powershell
cd C:\BlenderRemoteGPU
.\venv\Scripts\Activate.ps1

# Start worker in debug mode
python render_worker.py

# Expected output (within 10 seconds):
# [LOAD_START] Loading scene...
# [HTTP_STARTUP] HTTP server listening on 0.0.0.0:6969
# [PING] Worker ready, awaiting commands
```

**Watch for in logs:**
- ✓ `[HTTP_STARTUP]` — Worker is listening
- ✗ `[TIMEOUT]` — Worker timed out (check Blender installation)
- ✗ `[LOAD_ERROR]` — Scene load failed (check permissions)

### Test 2: Connectivity Test

**On Coordinator Machine:**

```powershell
# Test connectivity to worker
$worker_ip = "192.168.1.100"
$worker_port = 6969

# TCP connection test
$tcp = New-Object System.Net.Sockets.TcpClient
try {
    $tcp.Connect($worker_ip, $worker_port)
    Write-Host "✓ TCP connection successful" -ForegroundColor Green
} catch {
    Write-Host "✗ TCP connection failed: $_" -ForegroundColor Red
    exit 1
}

# HTTP health check (requires worker running)
$health = Invoke-WebRequest -Uri "http://$worker_ip:$worker_port/health" -ErrorAction SilentlyContinue
if ($health.StatusCode -eq 200) {
    $status = $health.Content | ConvertFrom-Json
    Write-Host "✓ Worker health check passed" -ForegroundColor Green
    Write-Host "  GPU: $($status.gpu_name)"
    Write-Host "  Scene Loaded: $($status.scene_loaded)"
} else {
    Write-Host "✗ Health check failed" -ForegroundColor Red
}
```

### Test 3: Simple Scene Load Test

**Scene File:** Create simple test.blend (single cube, default camera)

```powershell
# On Coordinator Machine
cd C:\BlenderRemoteGPU
.\venv\Scripts\Activate.ps1

# Upload and render test scene
python -c "
import requests
import json

worker = 'http://192.168.1.100:6969'

# Load scene
scene_data = {
    'filepath': 'C:/temp/test.blend',
    'start_frame': 1,
    'end_frame': 1
}

resp = requests.post(f'{worker}/load_scene', json=scene_data)
if resp.status_code == 200:
    print('✓ Scene load successful')
    result = resp.json()
    print(f'  Scene loaded: {result.get(\"scene_loaded\")}')
else:
    print(f'✗ Scene load failed: {resp.status_code}')
    print(resp.text)
"
```

**Watch logs for:**
- `[LOAD_START]` — Scene loading began
- `[LOAD_MAINFILE_START]` — Opening .blend file
- `[LOAD_MAINFILE_OK]` — File opened successfully
- `[LOAD_GPU_SETUP_START]` — GPU configuration
- `[LOAD_GPU_SETUP_OK]` — GPU ready
- `[LOAD_COMPLETE]` — Scene fully loaded
- `[LOAD_ERROR]` — Any error during load

### Test 4: Render Test

```powershell
# Render single frame on worker
python -c "
import requests

worker = 'http://192.168.1.100:6969'

render_data = {
    'frame': 1,
    'samples': 64,
    'width': 1920,
    'height': 1080
}

resp = requests.post(f'{worker}/render_frame', json=render_data)
if resp.status_code == 200:
    print('✓ Render successful')
    result = resp.json()
    print(f'  Samples: {result.get(\"samples_completed\")}')
    print(f'  Time: {result.get(\"render_time_sec\")}s')
else:
    print(f'✗ Render failed: {resp.status_code}')
    print(resp.text)
"
```

**Watch logs for:**
- `[RENDER_START]` — Render started
- `[RENDER_SAMPLE]` — Progress updates (optional)
- `[RENDER_COMPLETE]` — Render finished
- `[CLEANUP]` — Temp files cleaned up
- `[TIMEOUT]` — If render exceeds 300s
- `[RECOVERY]` — If fallback to CPU

### Test 5: Error Recovery Test

```powershell
# Test retry behavior by stopping worker mid-load
# Start worker, begin scene load, wait 2s, stop worker, restart

# Expected behavior:
# First load attempt fails → [LOAD_ERROR]
# Queues retry with 0.5s delay
# After 0.5s → retry dequeued → [RETRY] Attempt 1
# Succeeds on retry

# Check logs for:
# [RETRY] Dequeued scene load (attempt 1 of 5)
# [LOAD_START] Retrying scene load...
```

---

## Monitoring & Troubleshooting

### Log File Locations

```
GPU Worker:      C:\BlenderRemoteGPU\logs\render_worker.log
Coordinator:     C:\BlenderRemoteGPU\logs\server.log
```

### Important Log Tags and Their Meanings

| Tag | Level | Meaning | Action |
|-----|-------|---------|--------|
| `[LOAD_COMPLETE]` | INFO | Scene loaded successfully | Normal operation |
| `[LOAD_ERROR]` | ERROR | Scene load failed | Check file path, permissions, GPU memory |
| `[TIMEOUT]` | WARN | Operation exceeded time limit | Increase timeout value or optimize scene |
| `[RETRY]` | INFO | Retrying failed operation | Normal, system is self-healing |
| `[RECOVERY]` | WARN | Fallback mechanism activated | GPU unavailable, using CPU (slower) |
| `[SHUTDOWN]` | INFO | Graceful shutdown in progress | Normal if stopping service |
| `[VALIDATION]` | ERROR | Input validation failed | Check request parameters |
| `[CLEANUP]` | INFO | Temp files cleaned up | Normal operation |

### Common Issues and Solutions

#### Issue: Worker Starts But Shows `[LOAD_ERROR]`

```
Logs show:
[LOAD_START] Loading scene...
[LOAD_ERROR] Failed to load scene: module not found

Solution:
1. Verify Blender is installed: "C:\Program Files\Blender Foundation\Blender"
2. Verify GPU drivers: nvidia-smi
3. Check render_worker.py line 50 for Blender path configuration
4. If issue persists, check System Console in Blender for details
```

#### Issue: Connectivity Fails (`Connection Refused`)

```
Logs show:
[HTTP_STARTUP] FAILED - Address already in use

Solution:
1. Check if port 6969 is already in use:
   netstat -ano | findstr "6969"
2. Kill existing process or use different port:
   taskkill /PID <pid> /F
3. Verify firewall:
   netsh advfirewall show allprofiles | findstr -i "6969"
4. Verify worker IP matches coordinator config
```

#### Issue: Render Timeout (Hangs for 300+ seconds)

```
Logs show:
[TIMEOUT] render_frame exceeded 300s: operation did not complete

Solution:
1. Scene is too complex for timeout window:
   - Reduce samples: samples = 64 instead of 256
   - Reduce resolution: 1280x720 instead of 3840x2160
   - Enable viewport simplification
2. GPU memory issue:
   - Check "nvidia-smi" for 98% utilization
   - Reduce texture resolution
   - Lower max workers count
3. Increase timeout permanently (line ~300 in render_worker.py):
   _TIMEOUT_RENDER = 600  # 10 minutes instead of 5
```

#### Issue: `[RECOVERY] Falling back to CPU rendering`

```
Logs show:
[LOAD_GPU_SETUP_OK] GPU setup exceeded 15s: operation did not complete

Solution:
1. GPU drivers may be unstable:
   - Update: https://www.nvidia.com/Download/driverDetails.aspx
   - Rollback to previous driver if update failed
2. GPU memory leak:
   - Restart worker process
   - Check for lingering Blender processes: tasklist | findstr blender
3. Temperature issue:
   - Check: nvidia-smi --query-gpu=temperature.gpu
   - Improve cooling (fans, ventilation)
4. Increase GPU timeout (temporary):
   _TIMEOUT_SETUP_GPU = 30  # 30 seconds instead of 15
```

#### Issue: Disk Space Fills Rapidly

```
Logs show:
[CLEANUP] Failed to remove temp directory: permission denied

Solution:
1. Check disk usage:
   dir C:\ /s /h | find "Total Files"
2. Manual cleanup if logs show `[CLEANUP]` failures:
   rmdir /s /q C:\BlenderRemoteGPU\temp\*
   dir C:\BlenderRemoteGPU\temp
3. Verify cleanup function is running:
   grep "[CLEANUP]" logs/render_worker.log | tail -20
4. If issues persist, check render_worker.py:_cleanup_temp_dir()
   - Verify shutil.rmtree() has permission to delete
   - Run as Administrator if needed
```

### Monitoring Best Practices

#### Daily Log Review

```powershell
# Check for errors
Select-String -Path "C:\BlenderRemoteGPU\logs\*.log" -Pattern "\[ERROR\]|\[TIMEOUT\]" | tail -50

# Check for recovery activations
Select-String -Path "C:\BlenderRemoteGPU\logs\*.log" -Pattern "\[RECOVERY\]" | tail -20

# Monitor for shutdown events (may indicate crashes)
Select-String -Path "C:\BlenderRemoteGPU\logs\*.log" -Pattern "\[SHUTDOWN\]" | tail -10
```

#### Performance Metrics

```powershell
# Calculate average render time
$lines = Select-String -Path "C:\BlenderRemoteGPU\logs\render_worker.log" -Pattern "render_time_sec"
$times = $lines | ForEach-Object { [double]($_.Line -replace '.*render_time_sec.:([\d.]+).*', '$1') }
$avg = ($times | Measure-Object -Average).Average
Write-Host "Average render time: $avg seconds"
```

#### Uptime Monitoring

```powershell
# Check process uptime
$process = Get-Process python | Where-Object { $_.CommandLine -like "*render_worker.py*" }
if ($process) {
    $uptime = (Get-Date) - $process.StartTime
    Write-Host "Worker uptime: $($uptime.Days)d $($uptime.Hours)h $($uptime.Minutes)m"
}
```

---

## Rollback Procedure

### If Issues Occur After Deployment

#### Immediate Mitigation (Keep b36 Running)

```powershell
# Stop b37 worker
taskkill /IM python.exe /F

# Start b36 from backup directory
cd C:\BlenderRemoteGPU_b36\
.\venv\Scripts\Activate.ps1
python render_worker.py

# Verify worker is responding
Invoke-WebRequest -Uri "http://localhost:6969/health"
```

#### Full Rollback Steps

```powershell
# 1. Backup b37 logs for debugging
Copy-Item "C:\BlenderRemoteGPU\logs\*" "C:\BlenderRemoteGPU_b37_logs_backup\" -Recurse

# 2. Stop b37 services
taskkill /IM python.exe /F

# 3. Restore b36 as active version
Remove-Item "C:\BlenderRemoteGPU\" -Recurse -Force
Rename-Item "C:\BlenderRemoteGPU_b36\" "C:\BlenderRemoteGPU"

# 4. Restart services
cd C:\BlenderRemoteGPU\
.\venv\Scripts\Activate.ps1
python render_worker.py

# 5. Verify functionality
python test_b35_fixes.py

# 6. Monitor logs for stability
Get-Content -Path "C:\BlenderRemoteGPU\logs\render_worker.log" -Tail 20 -Wait
```

#### Post-Rollback Analysis

```powershell
# Compare b37 logs with b36 to identify issue
Write-Host "Comparing error patterns..."

$b37_errors = Select-String -Path "C:\BlenderRemoteGPU_b37_logs_backup\*.log" -Pattern "\[ERROR\]" | Measure-Object | %Count
$b37_timeouts = Select-String -Path "C:\BlenderRemoteGPU_b37_logs_backup\*.log" -Pattern "\[TIMEOUT\]" | Measure-Object | %Count

Write-Host "b37 errors: $b37_errors"
Write-Host "b37 timeouts: $b37_timeouts"

if ($b37_errors -gt 50 -or $b37_timeouts -gt 20) {
    Write-Host "⚠️ Elevated error/timeout rates in b37, rollback confirmed justified"
}
```

### Critical Issues Requiring Immediate Rollback

- Worker crashes more than 5 times per hour
- Memory leak causing OOM errors
- GPU initialization fails consistently
- Network connectivity issues with specific worker
- File permission issues in temp cleanup

---

## Long-Term Operations

### Weekly Maintenance

```powershell
# Monday morning: Review logs for the week
$logs = Get-ChildItem "C:\BlenderRemoteGPU\logs\*.log" | Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-7) }
$error_count = $logs | Select-String "\[ERROR\]" | Measure-Object | %Count
$warning_count = $logs | Select-String "\[WARN\]|\[TIMEOUT\]" | Measure-Object | %Count

Write-Host "Weekly Summary:"
Write-Host "  Errors: $error_count"
Write-Host "  Warnings: $warning_count"
Write-Host "  Uptime: (check worker restart count)"

# Archive old logs
$ArchiveDir = "C:\BlenderRemoteGPU\logs\archive\$(Get-Date -Format 'yyyy-MM-dd')"
New-Item -ItemType Directory -Path $ArchiveDir -Force
Get-ChildItem "C:\BlenderRemoteGPU\logs\*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } | Move-Item -Destination $ArchiveDir
```

### Monthly Maintenance

```powershell
# First of month: Update drivers and OS patches
# 1. Check for Windows updates
Windows Update → Install → Restart

# 2. Update NVIDIA drivers
# Visit https://www.nvidia.com/Download/driverDetails.aspx
# Download and install latest driver

# 3. Run GPU diagnostics
nvidia-smi --query-gpu=memory.total,memory.used,temperature.gpu --format=csv

# 4. Check for GPU errors in logs
Select-String -Path "C:\BlenderRemoteGPU\logs\render_worker.log" -Pattern "GPU|CUDA|Error"

# 5. Verify no worker restarts are needed
tasklist | findstr "python" | find "render_worker"
```

### Quarterly Review

```powershell
# Every 3 months: Performance analysis and planning
# 1. Calculate metrics
$q_logs = Get-ChildItem "C:\BlenderRemoteGPU\logs\*.log" | Where-Object { $_.LastWriteTime -gt (Get-Date).AddMonths(-3) }
$total_renders = $q_logs | Select-String "RENDER_COMPLETE" | Measure-Object | %Count
$failed_renders = $q_logs | Select-String "LOAD_ERROR|TIMEOUT" | Measure-Object | %Count
$success_rate = [math]::Round((($total_renders - $failed_renders) / $total_renders) * 100, 2)

Write-Host "Q Metrics:"
Write-Host "  Total renders: $total_renders"
Write-Host "  Success rate: $success_rate%"
Write-Host "  Failed/timeout: $failed_renders"

# 2. Decision points
if ($success_rate -lt 95) {
    Write-Host "Action: Investigate and improve reliability"
} elseif ($success_rate -gt 99.5) {
    Write-Host "Action: Production stable, consider upgrading to higher spec"
}
```

### Annual Review

- Review build updates (new major version release)
- Plan hardware upgrades if utilization >80%
- Audit security settings and access controls
- Archive logs older than 1 year
- Document lessons learned and improvements

---

## Service Installation (Optional)

### Create Windows Service (NSSM Method)

```powershell
# Download NSSM (Non-Sucking Service Manager)
# https://nssm.cc/download

# Install as service
cd C:\nssm
.\nssm install BlenderGPUWorker `
    "C:\BlenderRemoteGPU\venv\Scripts\python.exe" `
    "C:\BlenderRemoteGPU\render_worker.py"

# Configure service
.\nssm set BlenderGPUWorker AppDirectory "C:\BlenderRemoteGPU"
.\nssm set BlenderGPUWorker AppStdout "C:\BlenderRemoteGPU\logs\stdout.log"
.\nssm set BlenderGPUWorker AppStderr "C:\BlenderRemoteGPU\logs\stderr.log"
.\nssm set BlenderGPUWorker AppRotateFiles 1
.\nssm set BlenderGPUWorker AppRotateSeconds 86400  # Daily rotation
.\nssm set BlenderGPUWorker AppRotateBytes 104857600  # 100MB rotation

# Start service
Start-Service BlenderGPUWorker

# Verify
Get-Service BlenderGPUWorker | Select-Object Status, StartType
```

---

## Support and Escalation

### Log Output for Support

When reporting issues, include:
1. Full render_worker.log (or last 1000 lines if large)
2. Full server.log (or last 1000 lines if large)
3. System info: `systeminfo > sysinfo.txt`
4. GPU info: `nvidia-smi >> gpu_info.txt`
5. Network config: `ipconfig /all > network_info.txt`

### Debug Mode (Verbose Logging)

```powershell
# Enable debug mode by modifying render_worker.py line ~50:
# logging.basicConfig(level=logging.DEBUG)  # Instead of logging.INFO

# Then restart worker:
taskkill /IM python.exe /F
cd C:\BlenderRemoteGPU
python render_worker.py

# Debug logs will show every operation in detail
```

---

## Conclusion

This checklist covers deployment, verification, monitoring, and rollback procedures for b37. Follow the testing steps carefully before marking production-ready, and monitor logs daily for the first week post-deployment.

**Key Contacts:**
- Issues: Review logs, check logs for `[ERROR]` and `[TIMEOUT]` tags
- Emergency: taskkill /IM python.exe /F (stops worker, safe to restart)
- Severe Degradation: Follow rollback procedure in section 5

**Ready for Production:** Yes, pending completion of all tests in "Testing & Verification" section.
