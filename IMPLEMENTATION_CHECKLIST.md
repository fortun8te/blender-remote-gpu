# Implementation Checklist - Diagnostics & Testing Suite

A practical guide for integrating and using all the new diagnostic tools.

## Phase 1: Installation & Setup

### 1.1 Verify File Structure
- [ ] `tools/connectivity_diagnostics.py` exists (560 lines)
- [ ] `tools/server_health_check.py` exists (550 lines)
- [ ] `addon/logging_system.py` exists (450 lines)
- [ ] `tests/test_addon_connection.py` exists (450 lines)
- [ ] `.github/workflows/tests.yml` exists
- [ ] `docs/DIAGNOSTICS_GUIDE.md` exists
- [ ] `docs/QUICK_REFERENCE.md` exists
- [ ] `tools/README.md` exists
- [ ] `DIAGNOSTICS_SUMMARY.md` exists

### 1.2 Install Dependencies
```bash
# Required
pip install websockets

# For testing
pip install pytest pytest-cov

# For code quality
pip install black flake8 isort
```
- [ ] websockets installed
- [ ] pytest installed (if running tests)
- [ ] Code quality tools installed (if using)

### 1.3 Make Scripts Executable
```bash
chmod +x tools/connectivity_diagnostics.py
chmod +x tools/server_health_check.py
```
- [ ] Scripts are executable
- [ ] Scripts can be run directly

## Phase 2: Initial Testing

### 2.1 Test Connectivity Diagnostics
```bash
cd /path/to/blender-remote-gpu
python tools/connectivity_diagnostics.py
```
- [ ] Script runs without errors
- [ ] Shows all 6 tests
- [ ] Displays summary
- [ ] Exit code is reasonable (0 or 1)

### 2.2 Test Server Health Check
```bash
python tools/server_health_check.py
```
- [ ] Script runs without errors
- [ ] Shows all 5 checks
- [ ] Calculates readiness score
- [ ] Displays results

### 2.3 Test Unit Tests
```bash
cd tests
pytest test_addon_connection.py -v
```
- [ ] All tests pass (or show expected failures)
- [ ] No import errors
- [ ] Test summary shows results

### 2.4 Test Logging System
```python
# In Python console or script
from addon.logging_system import get_logger

logger = get_logger(debug=True)
logger.info("Test", "Testing logging system")

# Check log file
cat ~/.blender/remote-gpu/logs/remote_gpu_*.log
```
- [ ] Log file created
- [ ] Log entries in JSON format
- [ ] Debug output visible

## Phase 3: Export & Reporting

### 3.1 Generate Connectivity Report
```bash
python tools/connectivity_diagnostics.py \
  --json connectivity_report.json \
  --html connectivity_report.html
```
- [ ] JSON file created
- [ ] HTML file created
- [ ] Files contain valid data
- [ ] Can open HTML in browser

### 3.2 Generate Health Report
```bash
python tools/server_health_check.py \
  --json health_report.json
```
- [ ] JSON file created
- [ ] Contains all check results
- [ ] Includes readiness score

### 3.3 Export Addon Logs
```python
from addon.logging_system import get_logger

logger = get_logger()
export_path = logger.export_session_log()
print(f"Logs exported to: {export_path}")
```
- [ ] Log file exported successfully
- [ ] JSON format is valid
- [ ] All session data included

### 3.4 Test JSON Validity
```bash
python -m json.tool connectivity_report.json > /dev/null
python -m json.tool health_report.json > /dev/null
```
- [ ] All JSON files are valid
- [ ] No formatting errors

## Phase 4: CI/CD Integration

### 4.1 Verify GitHub Actions Workflow
- [ ] `.github/workflows/tests.yml` file exists
- [ ] Workflow file is valid YAML
- [ ] Can view in GitHub Actions tab

### 4.2 Trigger First Workflow Run
```bash
git add .
git commit -m "Add diagnostics and testing suite"
git push origin main
```
- [ ] Push triggers GitHub Actions
- [ ] All jobs run (Python Quality, Unit Tests, Addon Validation, etc.)
- [ ] Jobs complete (pass or fail)
- [ ] Results visible in Actions tab

### 4.3 Verify Workflow Results
- [ ] Python Quality check passes
- [ ] Unit Tests pass or show expected results
- [ ] Addon Validation passes
- [ ] Diagnostic Tools Validation passes
- [ ] Mock Connection Test passes
- [ ] Build Addon Package completes

## Phase 5: Documentation Review

### 5.1 Read Quick Reference
```bash
cat docs/QUICK_REFERENCE.md
```
- [ ] Understand tool purposes
- [ ] Know common commands
- [ ] Familiar with troubleshooting matrix

### 5.2 Read Full Diagnostics Guide
```bash
cat docs/DIAGNOSTICS_GUIDE.md
```
- [ ] Understand all test categories
- [ ] Know how to interpret results
- [ ] Familiar with JSON/HTML formats
- [ ] Understand troubleshooting process

### 5.3 Read Tools Documentation
```bash
cat tools/README.md
```
- [ ] Understand architecture
- [ ] Know output formats
- [ ] Familiar with error handling
- [ ] Know CI/CD integration

### 5.4 Review Implementation Summary
```bash
cat DIAGNOSTICS_SUMMARY.md
```
- [ ] Understand what was implemented
- [ ] Know file locations
- [ ] Familiar with key features

## Phase 6: Integration with Addon

### 6.1 Update Addon to Use Logging
```python
# In addon/__init__.py or other modules
from addon.logging_system import get_logger, Severity

logger = get_logger()

# Use in connection
logger.info("Connection", "Connected to server",
           details={"host": "100.74.135.83", "gpu": "RTX 3090"})

# Use in engine
logger.error("Render", "Render failed",
            details={"error": str(exception)})
```
- [ ] Import logging_system in addon modules
- [ ] Add logging calls to connection.py
- [ ] Add logging calls to engine.py
- [ ] Test that logs are created

### 6.2 Update Preferences for Diagnostics
```python
# In addon/preferences.py, consider adding:
# - Debug mode toggle
# - Log level selection
# - Enable telemetry checkbox
```
- [ ] Consider adding to preferences panel
- [ ] Document new options

### 6.3 Add Diagnostic Menu Item (Optional)
```python
# Could add operator to run diagnostics
class REMOTEGPU_OT_run_diagnostics(bpy.types.Operator):
    bl_idname = "remote_gpu.run_diagnostics"
    bl_label = "Run Diagnostics"

    def execute(self, context):
        # Run connectivity_diagnostics.py
        return {'FINISHED'}
```
- [ ] Consider adding to addon UI
- [ ] Test integration

## Phase 7: Testing Workflow

### 7.1 Use Diagnostics for Troubleshooting
When users report connection issues:
```bash
# Step 1: Collect diagnostics
python tools/connectivity_diagnostics.py --json issue.json --html issue.html
python tools/server_health_check.py --json health.json

# Step 2: Review logs
cat ~/.blender/remote-gpu/logs/remote_gpu_*.log

# Step 3: Ask user to attach JSON/HTML files
```
- [ ] Can run diagnostics quickly
- [ ] Can collect comprehensive data
- [ ] Can generate shareable reports

### 7.2 Use Tests for Development
When making changes:
```bash
# Run tests before committing
pytest tests/ -v

# Run quality checks
black addon/
flake8 addon/
isort addon/
```
- [ ] Tests pass before commit
- [ ] Code follows style guidelines
- [ ] No import errors

### 7.3 Monitor CI/CD
For each commit:
- [ ] Check GitHub Actions results
- [ ] Review test coverage
- [ ] Fix any failures immediately
- [ ] Celebrate green builds!

## Phase 8: User Documentation

### 8.1 Update Addon README
Add section to main README:
```markdown
## Diagnostics & Testing

Run diagnostics if you experience connection issues:

```bash
python tools/connectivity_diagnostics.py --json report.json
python tools/server_health_check.py --json health.json
```

See `docs/DIAGNOSTICS_GUIDE.md` for detailed help.
```
- [ ] README updated with diagnostics section
- [ ] Link to comprehensive guide

### 8.2 Create Troubleshooting FAQ
- [ ] Add common issues and solutions
- [ ] Link to diagnostic tools
- [ ] Provide example outputs

### 8.3 Update Installation Guide
- [ ] Add optional dependencies (websockets)
- [ ] Document tools installation
- [ ] Explain how to run diagnostics

## Phase 9: Advanced Features (Optional)

### 9.1 Continuous Monitoring Script
```bash
#!/bin/bash
# Save as monitor.sh
while true; do
    python tools/connectivity_diagnostics.py \
        --json "logs/diag_$(date +%s).json"
    sleep 300
done
```
- [ ] Create monitoring script
- [ ] Document usage
- [ ] Set up cron job if needed

### 9.2 Automated Issue Creation
```python
# Script to create GitHub issue with diagnostics
# Would attach JSON/HTML automatically
```
- [ ] Consider implementing automated issue creation
- [ ] Would help with debugging

### 9.3 Performance Tracking
- [ ] Archive diagnostics from each test run
- [ ] Track latency trends
- [ ] Monitor GPU performance
- [ ] Generate performance reports

## Phase 10: Maintenance

### 10.1 Monitor GitHub Actions
- [ ] Check Actions tab regularly
- [ ] Review test results
- [ ] Fix any failures immediately
- [ ] Update CI/CD as needed

### 10.2 Review Logs
- [ ] Periodically review addon logs
- [ ] Look for patterns in failures
- [ ] Update troubleshooting guide
- [ ] Consider new diagnostics

### 10.3 Update Documentation
- [ ] Keep guides current
- [ ] Add examples from real issues
- [ ] Expand troubleshooting section
- [ ] Update quick reference

### 10.4 Version Maintenance
- [ ] Update version in addon as needed
- [ ] Keep tools in sync with addon
- [ ] Update documentation versions
- [ ] Maintain changelog

## Success Criteria

### For Users ✅
- [ ] Can quickly diagnose connection issues
- [ ] Can get professional bug report ready
- [ ] Can understand what's happening
- [ ] Know where to find help

### For Developers ✅
- [ ] Can identify issues in CI/CD
- [ ] Can debug user problems easily
- [ ] Can monitor code quality
- [ ] Can track performance trends

### For Project ✅
- [ ] Fewer support requests
- [ ] Better quality reports
- [ ] Faster issue resolution
- [ ] Higher user satisfaction

## Rollback Plan

If issues occur:
- [ ] Can disable GitHub Actions (remove .yml file)
- [ ] Can remove diagnostic tools (keep addon working)
- [ ] Can disable logging (no dependencies)
- [ ] Can revert to previous version

---

## Completion Checklist

- [ ] Phase 1: Installation complete
- [ ] Phase 2: Initial testing done
- [ ] Phase 3: Reporting tested
- [ ] Phase 4: CI/CD working
- [ ] Phase 5: Documentation reviewed
- [ ] Phase 6: Addon integration done
- [ ] Phase 7: Testing workflow established
- [ ] Phase 8: User docs updated
- [ ] Phase 9: Optional features implemented (as desired)
- [ ] Phase 10: Maintenance plan in place

---

## Quick Command Reference

```bash
# Test connectivity
python tools/connectivity_diagnostics.py

# Export connectivity report
python tools/connectivity_diagnostics.py --json conn.json --html conn.html

# Test server health
python tools/server_health_check.py

# Export server report
python tools/server_health_check.py --json health.json

# Run tests
pytest tests/ -v

# Check code quality
black --check addon/
flake8 addon/
isort --check addon/

# View logs
cat ~/.blender/remote-gpu/logs/remote_gpu_*.log

# Export addon logs
python -c "from addon.logging_system import get_logger; logger = get_logger(); print(logger.export_session_log())"
```

---

**Implementation Status:** Ready to Deploy
**Estimated Setup Time:** 30-60 minutes
**Maintenance Time:** 5-10 minutes per week

