# Blocking Issues for Production: Quick Reference

## The 5 Blockers

| # | Issue | Impact | Fix Time | Status |
|---|-------|--------|----------|--------|
| 1 | **No Docker** | Fragile deployment, hard to replicate, no auto-restart | 1-2 days | ❌ |
| 2 | **No multi-server** | Single GPU bottleneck, no failover | 2-3 days | ❌ |
| 3 | **Network unreliability** | VPN/Tailscale packet loss = silent fails | 1-2 days | ❌ |
| 4 | **No monitoring** | Failures undetected until production breaks | 1-2 days | ❌ |
| 5 | **Manual startup** | No systemd/launchd, crashes require manual restart | Included in #1 | ❌ |

**Total Effort:** 2-3 weeks (1 person) or 1 week (2 people)

---

## 1. Docker Containerization

### What's Broken?
- Deploy to Windows/Linux/macOS = environment differences
- No auto-restart if server crashes
- Hard to run multiple GPU servers side-by-side
- No reproducible CI/CD

### Solution
```bash
# Create these files:
Dockerfile              # Container image definition
docker-compose.yml      # Multi-server orchestration
.dockerignore          # Build optimization

# Deploy:
docker-compose up -d   # Starts servers + auto-restart
docker-compose logs -f # View logs
```

### Benefits
✅ Same environment everywhere (no "works on my machine")
✅ Auto-restart on crash (restart: unless-stopped)
✅ Easy to add more servers (copy service in docker-compose.yml)
✅ Ready for Kubernetes (future scaling)

### Est. Effort: **1-2 days**

---

## 2. Multi-Server Load Balancing

### What's Broken?
```
Client1 ─→ GPU Server 1 (BOTTLENECK — only 1 GPU)
Client2 ─→ WAITING...
Client3 ─→ WAITING...
```

### Solution
```bash
# Create:
shared/server_registry.py    # Track available servers + health
                             # Health checks, queue depth reporting

# Modify:
addon/preferences.py         # Add server list: "192.168.1.100:9876,192.168.1.101:9876"
addon/connection.py          # Try servers in order, pick healthiest

# Result:
Client1 ─→ GPU Server 1 (queue=0)
Client2 ─→ GPU Server 2 (queue=0)
Client3 ─→ GPU Server 1 (queue=1)
```

### Benefits
✅ No single point of failure
✅ Load distributed across GPUs
✅ Automatic failover (if server down, use next)
✅ Fair scheduling (renders go to least-busy server)

### Est. Effort: **2-3 days**

---

## 3. Network Resilience (Retry + Failover)

### What's Broken?
- VPN with 5% packet loss = render silently fails
- One lost message = entire render aborts
- No way to detect or recover

### Solution
```python
# Modify: addon/connection.py
class RobustConnection:
    async def send_with_retry(self, msg, max_retries=3):
        for attempt in range(max_retries):
            try:
                await self.ws.send(msg)
                return True
            except (ConnectionError, TimeoutError):
                if attempt < max_retries - 1:
                    delay = 2 ** attempt  # 1s, 2s, 4s
                    await asyncio.sleep(delay)
                else:
                    # Try next server
                    await self.connect_to_next_server()
                    return await self.send_with_retry(msg)
```

### Benefits
✅ Handles 5-10% packet loss without failure
✅ Automatic failover to backup server
✅ No silent failures (clear error if all servers down)
✅ Transparent to user (retries happen in background)

### Est. Effort: **1-2 days**

---

## 4. Monitoring & Alerting

### What's Broken?
- Server crashes silently
- GPU runs out of VRAM, no warning
- Network is slow, nobody notices
- Render farm broken for hours before anyone knows

### Solution
```bash
# Add Prometheus metrics:
server/metrics.py          # Counter/Gauge/Histogram definitions

# Expose metrics:
server/server.py --metrics-port 8000
curl localhost:8000/metrics  # Returns Prometheus format

# Monitor:
docker-compose up prometheus  # Scraped metrics every 15s

# Alert if:
- GPU temperature > 80°C
- VRAM usage > 90%
- Frame drop rate > 1/sec
- Server not responding to health check
```

### Benefits
✅ Early warning (know server is dying before it dies)
✅ Performance tracking (see latency trends)
✅ Capacity planning (when do you need more GPUs?)
✅ Post-mortem analysis (what happened during outage?)

### Est. Effort: **1-2 days**

---

## 5. Systemd/Auto-Restart (Included in Docker)

### What's Broken?
- Server crashes, stays down until someone manually restarts it
- No integration with OS service management
- Can't `systemctl status blender-gpu`

### Solution (Via Docker)
```ini
# /etc/systemd/system/blender-gpu-render.service
[Unit]
Description=Blender Remote GPU
After=docker.service

[Service]
ExecStart=/usr/bin/docker-compose up
Restart=unless-stopped
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

```bash
# Install & start:
sudo systemctl enable blender-gpu-render.service
sudo systemctl start blender-gpu-render.service
sudo systemctl status blender-gpu-render.service
```

### Benefits
✅ Auto-restart on crash
✅ Standard OS integration (`systemctl`, `journalctl`)
✅ Respects system power state
✅ Startup on boot

### Est. Effort: **Included in Docker implementation**

---

## Implementation Order

### Week 1 (Critical Path)
1. **Create Dockerfile + docker-compose.yml** (1 day)
   - Makes local testing repeatable
   - Foundation for everything else

2. **Add retry logic to client** (1 day)
   - Handles VPN packet loss
   - No dependencies on other changes

3. **Create server registry + multi-server support** (2 days)
   - Integrates with Docker (multiple services in compose)
   - Tests with local servers (localhost:9876, localhost:9877)

### Week 2 (Polish)
4. **Add Prometheus metrics** (1 day)
   - Integrates with Docker (add prometheus service)
   - Alerts configured

5. **Set up systemd service** (0.5 days)
   - Uses Docker (includes auto-restart)

---

## Quick Win: Minimum to Deploy

If you had only 3 days:

1. **Dockerfile + docker-compose.yml** (1 day)
   - Auto-restart on crash
   - Repeatable deployment

2. **Retry logic** (0.5 days)
   - Handle VPN packet loss
   - Copy/paste from implementation guide

3. **Multi-server** (1.5 days)
   - Basic registry + failover
   - Simplified version without queue depth

**Result:** Can deploy to production with auto-restart + failover + resilience. Monitoring comes after.

---

## Red Flags: What Will Break in Production

### Without Docker:
- Server crashes → manual restart required
- Environment differences → "works on my machine"
- Multiple server setup is fragile (no orchestration)
- No health checks or auto-recovery

### Without Multi-Server:
- 10 clients waiting on 1 GPU = 2+ hours wait time
- If that GPU fails, entire farm down
- Can't do maintenance without shutting down all renders

### Without Network Resilience:
- VPN client with 10% packet loss = constant render failures
- Blender cloud/remote artist setup = unusable
- Restarts keep failing, looks like a bug

### Without Monitoring:
- Server out of memory → finds out when render fails
- GPU overheating → finds out when GPU crashes
- Network broken → finds out by asking users
- Render farm appears stable, actually silently losing frames

---

## Production Readiness Checklist

Before deploying:

- [ ] Dockerfile builds without errors
- [ ] `docker-compose up -d` starts cleanly
- [ ] Both servers respond to pings
- [ ] Client connects to server-1, second client → server-2
- [ ] Simulate packet loss → render retries and succeeds
- [ ] Metrics endpoint returns valid Prometheus format
- [ ] systemd service auto-restarts after crash
- [ ] Load test: 5 concurrent renders, no failures
- [ ] Stress test: VPN with 10% packet loss, still works
- [ ] Monitoring dashboard shows metrics

---

## Files to Create/Modify

```
CREATE:
  Dockerfile
  docker-compose.yml
  .dockerignore
  shared/server_registry.py
  server/metrics.py
  .github/workflows/test.yml
  monitoring/prometheus.yml
  monitoring/alerts.yml
  /etc/systemd/system/blender-gpu-render.service

MODIFY:
  addon/preferences.py (add server list)
  addon/connection.py (add retry + failover)
  server/server.py (add metrics)
  requirements.txt (add prometheus_client)
```

**Total:** ~10 new files, 5-6 files modified

---

## Cost-Benefit Analysis

### Cost: 2-3 weeks developer time

### Benefit: Production-Ready System
- Can scale from 1 → 10+ GPUs
- Auto-recovery from failures
- Works over VPN/Tailscale reliably
- Detects problems before they hit users
- Easy to deploy, update, rollback

### ROI
- Saves 2+ hours/week of manual intervention
- Prevents 1 day/quarter of unplanned downtime
- Enables remote artists (VPN-safe)
- Enables multi-studio scaling

**Break-even:** ~1 month of saved troubleshooting

---

## Final Recommendation

**DO NOT ship to production without:**
1. ✅ Docker
2. ✅ Multi-server setup
3. ✅ Network retry logic

**NICE TO HAVE, but can add later:**
4. Prometheus metrics
5. CI/CD pipeline
6. Performance benchmarks

**Current status:** 70% ready. The 5 blocking issues are all infrastructure, not code quality. Code is solid.
