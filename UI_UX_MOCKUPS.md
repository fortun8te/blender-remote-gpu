# Blender Remote GPU — UI/UX Mockups & Visual Reference

ASCII mockups showing before/after for each major improvement.

---

## 1. CONNECTION STATUS PANEL

### CURRENT STATE

```
┌─ Remote GPU ─────────────────────────────┐
│ Remote GPU b4 (2026-04-03)                │
├───────────────────────────────────────────
│ Server                                    │
│  IP: [100.74.135.83     ]  Port: [9876]  │
│  [Disconnect]                             │
│ Connected                    ← checkmark  │
│ GPU: RTX 4090                             │
│ VRAM Free: 23456 MB                       │
│                                           │
│ (Not connected)                   [INFO]  │
└───────────────────────────────────────────
```

**Issues:**
- No time tracking (elapsed, latency)
- No server version info
- No visual distinction between states
- Basic layout, low information density

---

### IMPROVED STATE

#### Connected
```
┌─ Remote GPU ─────────────────────────────┐
│ Remote GPU b4 (2026-04-03)                │
├───────────────────────────────────────────
│ 🟢 CONNECTION STATUS                     │
│ ┌───────────────────────────────────────┐
│ │ 🟢 CONNECTED (3m 42s elapsed)          │
│ │ Server: ws://100.74.135.83:9876        │
│ │ Latency: 12ms                          │
│ │ GPU: RTX 4090                          │
│ │ VRAM Free: 23,456 MB                   │
│ │ Server: v1.0.4 (b4)                    │
│ └───────────────────────────────────────┘
│ [Reconnect]  [Copy URL]  [ⓘ Info]       │
├───────────────────────────────────────────
│ ⚙️ SERVER SETTINGS                       │
│ IP: [100.74.135.83     ]  Port: [9876]  │
│                [Quick Test ▶]            │
│ IP: Server IP or hostname                │
│ Port: WebSocket port (default 9876)      │
└───────────────────────────────────────────
```

**Improvements:**
✓ Elapsed time tracking (3m 42s)
✓ Latency displayed (12ms)
✓ Server version visible (v1.0.4 b4)
✓ Color-coded status (🟢 green)
✓ Additional action buttons
✓ Better spacing and grouping

---

#### Connecting
```
┌─ Remote GPU ─────────────────────────────┐
│ Remote GPU b4 (2026-04-03)                │
├───────────────────────────────────────────
│ 🟡 CONNECTION STATUS                     │
│ ┌───────────────────────────────────────┐
│ │ 🟡 CONNECTING... (4s elapsed)          │
│ │ Server: ws://100.74.135.83:9876        │
│ │ Attempting to reach server...           │
│ └───────────────────────────────────────┘
│                          [Stop]          │
└───────────────────────────────────────────
```

---

#### Connection Failed (With Recovery Steps)
```
┌─ Remote GPU ─────────────────────────────┐
│ Remote GPU b4 (2026-04-03)                │
├───────────────────────────────────────────
│ 🔴 CONNECTION STATUS                     │
│ ┌───────────────────────────────────────┐
│ │ 🔴 CONNECTION FAILED                   │
│ │ Error: Server rejected connection      │
│ │        (check IP/port)                 │
│ │                                         │
│ │ Recovery steps:                         │
│ │ 1. Check IP and port are correct       │
│ │ 2. Verify server is running            │
│ │ 3. Check firewall settings             │
│ └───────────────────────────────────────┘
│                 [Try Again]              │
├───────────────────────────────────────────
│ ⚙️ SERVER SETTINGS                       │
│ IP: [100.74.135.83     ]  Port: [9876]  │
│                [Quick Test ▶]            │
│ ⚠️ Invalid IP/hostname format            │
└───────────────────────────────────────────
```

**Improvements:**
✓ Clear error title (CONNECTION FAILED)
✓ User-friendly explanation (not technical)
✓ Actionable recovery steps (numbered)
✓ IP validation indicator
✓ Clear call-to-action (Try Again)

---

#### Not Connected (Initial State)
```
┌─ Remote GPU ─────────────────────────────┐
│ Remote GPU b4 (2026-04-03)                │
├───────────────────────────────────────────
│ ⚫ CONNECTION STATUS                     │
│ ┌───────────────────────────────────────┐
│ │ ⚫ NOT CONNECTED                        │
│ └───────────────────────────────────────┘
│                                           │
│         [Connect to Server ▶]            │
├───────────────────────────────────────────
│ ⚙️ SERVER SETTINGS                       │
│ IP: [100.74.135.83     ]  Port: [9876]  │
│                [Quick Test ▶]            │
│ IP: Server IP or hostname                │
│ Port: WebSocket port (default 9876)      │
└───────────────────────────────────────────
```

---

## 2. ERROR MESSAGE RECOVERY

### Comparison Matrix

```
┌──────────────────┬──────────────────────┬──────────────────────┐
│ Error Type       │ BEFORE (v1.0.4)      │ AFTER (Improved)     │
├──────────────────┼──────────────────────┼──────────────────────┤
│ Connection       │ "Connection refused" │ "Server rejected     │
│ refused          │                      │ connection           │
│ (errno 61)       │ [no next steps]      │ (check IP/port)"     │
│                  │                      │                      │
│                  │                      │ Steps:               │
│                  │                      │ 1. Verify IP/port    │
│                  │                      │ 2. Server running?   │
│                  │                      │ 3. Firewall ok?      │
├──────────────────┼──────────────────────┼──────────────────────┤
│ Timeout          │ "Connection timed    │ "Server not          │
│ (10s no response)│ out"                 │ responding           │
│                  │                      │ (check if running)"  │
│                  │ [no next steps]      │                      │
│                  │                      │ Steps:               │
│                  │                      │ 1. Is server on?     │
│                  │                      │ 2. Tailscale active? │
│                  │                      │ 3. Network OK?       │
├──────────────────┼──────────────────────┼──────────────────────┤
│ Invalid response │ "Unexpected server   │ "Server sent         │
│                  │ response"            │ invalid data         │
│                  │                      │ (version mismatch?)" │
│                  │ [no next steps]      │                      │
│                  │                      │ Steps:               │
│                  │                      │ 1. Check versions    │
│                  │                      │ 2. Restart server    │
│                  │                      │ 3. Check logs        │
├──────────────────┼──────────────────────┼──────────────────────┤
│ Reset by peer    │ "Connection reset    │ "Server disconnected │
│                  │ by peer"             │ unexpectedly"        │
│                  │                      │                      │
│                  │ [no next steps]      │ Steps:               │
│                  │                      │ 1. Check server logs │
│                  │                      │ 2. Network stable?   │
│                  │                      │ 3. Try again         │
└──────────────────┴──────────────────────┴──────────────────────┘
```

---

### Error Display in UI

```
BEFORE:
┌────────────────────────────────────┐
│ Error: Connection refused          │
│ Error: Connection timed out        │
│ Error: Connection reset by peer    │
└────────────────────────────────────┘
(User is stuck, doesn't know what to do)

AFTER:
┌────────────────────────────────────┐
│ 🔴 CONNECTION FAILED               │
│                                    │
│ Error: Server rejected connection  │
│        (check IP/port)             │
│                                    │
│ Recovery steps:                    │
│ 1. Check IP and port are correct   │
│ 2. Verify server is running        │
│ 3. Check firewall settings         │
│                                    │
│         [Try Again]                │
└────────────────────────────────────┘
(User has clear path forward)
```

---

## 3. RENDER PROGRESS UI

### Panel Placement

```
Blender UI Layout:
┌─────────────────────────────────────────┐
│ [Properties Tabs]                       │
│ [Render] [Output] [Bake] [Cycles] ...   │ ← Render properties
│                                         │
├─────────────────────────────────────────┤
│ Remote GPU                              │ ← Existing panel
│ ┌─────────────────────────────────────┐ │
│ │ 🟢 CONNECTED (2m 15s)               │ │
│ │ GPU: RTX 4090                       │ │
│ │ [Reconnect] [Copy URL]              │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ Render Progress                    ← NEW! │
│ ┌─────────────────────────────────────┐ │
│ │ Status: Rendering samples          │ │
│ │ Samples: 64 / 128 (50%)            │ │
│ │ ████████████████░░░░░░░░░░░░░░     │ │
│ │                                     │ │
│ │ Time: 2m 15s                       │ │
│ │ Est. remaining: ~2m 10s             │ │
│ │                                     │ │
│ │            [Cancel Render]         │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ [Other panels...]                       │
└─────────────────────────────────────────┘
```

---

### Render States Flow

```
RENDERING STATE MACHINE:

START
  │
  ├─→ idle ────────────────────────────────┐
  │   "Not rendering"                      │
  │                                        │
  └─→ uploading ──────────────────────┐    │
      "Uploading scene..."              │    │
      [████████░░░░░░░░░░░░░░ 33%]     │    │
      │                                 │    │
      └─→ waiting ────────────────────┐ │    │
          "Waiting for server..."      │ │    │
          │                           │ │    │
          └─→ rendering ─────────────┐│ │    │
              "Rendering..."          ││ │    │
              [████████████░░░░░░░ 60%]││ │    │
              "Samples: 64/128"       ││ │    │
              │                       ││ │    │
              └─→ denoising ──────────┐││ │    │
                  "Denoising..."      │││ │    │
                  [████████████████░░░]││ │    │
                  │                   │││ │    │
                  └─→ receiving ───────┐││ │    │
                      "Downloading..." │││ │    │
                      │               │││ │    │
                      └─→ processing──┐│││ │    │
                          (internal)  ││││ │    │
                          │           ││││ │    │
                          └──────────→ ├──┘    │
                                      └───────→ idle
```

---

### Progress Panel States

#### State: Uploading
```
┌─ Render Progress ─────────────────────────┐
│ Status: Uploading scene (2/3)             │
│ ╔════════════════════════════════════╗    │
│ ║████████░░░░░░░░░░░░░░░░░░░░░░░░║    │
│ ╚════════════════════════════════════╝    │
│ Progress: 33%                             │
│                                           │
│ Time: 2s                                  │
│                              [Cancel]     │
└───────────────────────────────────────────┘
```

#### State: Rendering
```
┌─ Render Progress ─────────────────────────┐
│ Status: Rendering samples                 │
│ ╔════════════════════════════════════╗    │
│ ║████████████████░░░░░░░░░░░░░░░░░║    │
│ ╚════════════════════════════════════╝    │
│ Progress: 50%                             │
│                                           │
│ Samples: 64 / 128 (50%)                   │
│ ╔════════════════════════════════════╗    │
│ ║████████████████░░░░░░░░░░░░░░░░░║    │
│ ╚════════════════════════════════════╝    │
│                                           │
│ Time: 2m 15s                              │
│ Est. remaining: ~2m 10s                   │
│                              [Cancel]     │
└───────────────────────────────────────────┘
```

#### State: Denoising
```
┌─ Render Progress ─────────────────────────┐
│ Status: Denoising                         │
│ ╔════════════════════════════════════╗    │
│ ║████████████████████░░░░░░░░░░░░░║    │
│ ╚════════════════════════════════════╝    │
│ Progress: 90%                             │
│                                           │
│ Time: 4m 42s                              │
│ Est. remaining: ~30s                      │
│                              [Cancel]     │
└───────────────────────────────────────────┘
```

#### State: Complete (Auto-Hide)
```
(Panel disappears or collapses)
Last message in main UI:
"Render complete ✓"
```

---

## 4. PREFERENCES PANEL

### Current Layout
```
┌─ Addon Preferences ───────────────────────┐
│                                           │
│ Server IP: [100.74.135.83     ]          │
│ Port:      [9876              ]          │
│                                           │
│ (Just two fields, minimal context)       │
│                                           │
└───────────────────────────────────────────┘
```

---

### Improved Layout
```
┌─ Addon Preferences ───────────────────────┐
│                                           │
│ Configure your remote GPU server          │ ← Help text
│ connection                                │
│                                           │
├─ SERVER ADDRESS ──────────────────────────│
│                                           │
│ IP Address: [100.74.135.83    ]          │
│ Port:       [9876             ]          │
│                                           │
│ Examples:                                 │ ← Context
│ • Tailscale: 100.x.x.x                   │
│ • Hostname: myserver.local                │
│ • Local: 192.168.x.x                     │
│                                           │
│ [Quick Test Connection ▶]                │
│                                           │
├─ CONNECTION OPTIONS ──────────────────────│
│                                           │
│ ☑ Auto-reconnect on disconnect           │
│                                           │
│ Last working: 100.74.135.80:9876          │
│              [Restore Last Good] ↶       │
│                                           │
├─ TESTING ────────────────────────────────│
│                                           │
│ Quick connection test available in:      │
│ Render Properties > Remote GPU >          │
│ Quick Test                                │
│                                           │
└───────────────────────────────────────────┘
```

---

## 5. COMPARISON: All States Side-by-Side

```
CONNECTION STATES:

CONNECTED              CONNECTING           FAILED
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ 🟢 CONNECTED    │   │ 🟡 CONNECTING   │   │ 🔴 FAILED       │
│ 3m 42s elapsed  │   │ 4s ...          │   │                 │
│ Latency: 12ms   │   │                 │   │ Server rejected │
│ GPU: RTX 4090   │   │                 │   │ connection      │
│ VRAM: 23GB      │   │ Reaching...     │   │                 │
│ Server v1.0.4   │   │                 │   │ Recovery:       │
│                 │   │       [Stop]    │   │ 1. Check IP     │
│ [Reconnect]     │   │                 │   │ 2. Firewall     │
└─────────────────┘   └─────────────────┘   └─────────────────┘
      ✓ Ready          Transitioning         ✗ Action needed
       to render                              to reconnect
```

---

## 6. Information Density Comparison

### Before (Current)
```
Lines of content: 5-6
Information pieces: 3
User actions available: 1 (Connect)

╔═════════════════╗
║ Connected       ║  ← Very minimal
║ GPU: RTX 4090   ║
║ VRAM: 23456 MB  ║
╚═════════════════╝
```

### After (Improved)
```
Lines of content: 12-15
Information pieces: 8+
User actions available: 5+ (Reconnect, Copy, Test, etc.)

╔═════════════════════════════════╗
║ 🟢 CONNECTED (3m 42s elapsed)  ║
║ Server: ws://100.74.135.83:9876║  ← Rich context
║ Latency: 12ms                   ║
║ GPU: RTX 4090                   ║
║ VRAM: 23,456 MB                 ║
║ Server: v1.0.4 (b4)             ║
║                                 ║
║ [Reconnect] [Copy] [Info]      ║  ← Quick actions
╚═════════════════════════════════╝

Still fits in Blender's compact panel!
```

---

## 7. Color/Icon Legend

```
Status Indicators:

🟢 = Connected / OK / Success
   Icon: CHECKMARK (green tint)
   Use: "🟢 CONNECTED", "✓ Test passed"

🟡 = Connecting / Warning / In Progress
   Icon: TIME (yellow tint)
   Use: "🟡 CONNECTING...", "⚠️ Checking..."

🔴 = Disconnected / Error / Failed
   Icon: ERROR (red tint)
   Use: "🔴 FAILED", "✗ Test failed"

⚫ = Idle / Neutral / Unknown
   Icon: BLANK1 (gray tint)
   Use: "⚫ NOT CONNECTED", "○ Ready"

⚙️ = Settings / Configuration
   Icon: PREFERENCES
   Use: "⚙️ SERVER SETTINGS"

📡 = Network / Connection
   Icon: URL
   Use: "📡 CONNECTION STATUS"

▶ / ⏸ = Play / Pause / Control
   Icon: PLAY / PAUSE
   Use: "[▶ Connect]", "[⏸ Cancel]"

✓ = Done / Confirmation
   Icon: CHECKMARK
   Use: "✓ Step 1 complete"

ⓘ = Information / Help
   Icon: QUESTION or INFO
   Use: "[ⓘ Details]"

↻ = Refresh / Retry
   Icon: FILE_REFRESH
   Use: "[↻ Reconnect]"
```

---

## 8. UI Flow Diagram

```
USER STARTS ADDON
        │
        ├─ First time?
        │  └─→ Show preferences (Server IP, Port)
        │
        ├─→ [Connect to Server]
        │
        ├─→ Connecting...
        │  │
        │  ├─ Success?
        │  │  └─→ Show 🟢 CONNECTED panel
        │  │     └─→ Can render now
        │  │
        │  └─ Failed?
        │     └─→ Show 🔴 FAILED panel
        │        └─→ Show recovery steps
        │        └─→ [Try Again]
        │
        ├─→ User clicks [Render]
        │
        ├─→ Show Render Progress panel
        │  │
        │  ├─ Uploading: ████░░░░░░ 30%
        │  ├─ Rendering: ████████░░░░░░ 60%
        │  │  └─ Update samples: 64/128
        │  ├─ Denoising: ██████████░░░░ 80%
        │  └─ Receiving: ███████████░░ 95%
        │
        └─→ Render complete (auto-hide progress)

USER EXPERIENCE:
- Always knows what's happening
- Clear next steps on any error
- No silent failures
- Full render visibility
```

---

## 9. Responsive Design Notes

### Small Panel (400px width)
```
┌─ Remote GPU ─────────────────────┐
│ Remote GPU b4                     │
├──────────────────────────────────
│ 🟢 CONNECTION                    │
│ ┌──────────────────────────────┐
│ │ Connected (3m 42s)           │
│ │ Latency: 12ms               │
│ │ GPU: RTX 4090               │
│ │ VRAM: 23GB                  │
│ └──────────────────────────────┘
│ [Reconnect] [Copy] [Info]       │
├──────────────────────────────────
│ ⚙️ SERVER                        │
│ IP: [100.74.135.83]             │
│ Port: [9876]                    │
│ [Quick Test]                    │
└──────────────────────────────────
```

### Standard Panel (600px width)
```
┌─ Remote GPU ────────────────────────────────────┐
│ Remote GPU b4 (2026-04-03)                      │
├─────────────────────────────────────────────────
│ 🟢 CONNECTION STATUS                           │
│ ┌──────────────────────────────────────────────┐
│ │ 🟢 CONNECTED (3m 42s elapsed)                │
│ │ Server: ws://100.74.135.83:9876              │
│ │ Latency: 12ms                                │
│ │ GPU: RTX 4090                                │
│ │ VRAM Free: 23,456 MB                         │
│ │ Server: v1.0.4 (b4)                          │
│ └──────────────────────────────────────────────┘
│ [Reconnect] [Copy URL] [Connection Info]      │
├─────────────────────────────────────────────────
│ ⚙️ SERVER SETTINGS                             │
│ IP: [100.74.135.83     ] Port: [9876]         │
│ [Quick Test Connection ▶]                      │
│ IP: Server IP or hostname                      │
│ Port: WebSocket port (default 9876)            │
└─────────────────────────────────────────────────
```

---

## Implementation Priority Matrix

```
       IMPACT
         │
      H  │ ████████████ ████████████   Connection Status
      I  │ █████████████ ██████████████ Error Recovery
      G  │ ██████████ ████████████████   Render Progress
      H  │ ████████ ██████████████       Preferences Val.
         │ ██████ ████████████           Visual Polish
         │
         └─────────────────────────────── EFFORT →

Legend: ███ represents relative effort

Priority order:
1. Connection Status (high impact, medium effort) ← Start here
2. Error Recovery (medium impact, low effort)
3. Render Progress (high impact, medium effort)
4. Preferences (medium impact, low effort)
5. Visual Polish (low impact, low effort)
```

---

## Testing Scenarios

### Scenario 1: Happy Path
```
1. User opens addon for first time
2. Sees "NOT CONNECTED" with clear button
3. Clicks "Connect to Server"
4. See 🟢 CONNECTED appear with elapsed time
5. User renders (F12)
6. Sees "Uploading scene..." with progress
7. Sees "Rendering 64/128 samples" with bar
8. Render completes, shows result
9. Progress panel auto-hides

Result: User feels informed throughout
```

### Scenario 2: Wrong IP
```
1. User enters invalid IP (192.0.2.1)
2. Addon shows "⚠️ Invalid IP/hostname format"
3. User clicks "Quick Test"
4. Sees "Server not responding"
5. Addon shows recovery steps:
   - Check IP is correct
   - Verify server running
   - Try different network
6. User fixes IP, Quick Test works
7. Can now render

Result: User self-serves without support
```

### Scenario 3: Server Crash During Render
```
1. User renders
2. Sees "Rendering 64/128 samples..."
3. Server crashes
4. UI shows "Server disconnected unexpectedly"
5. Shows recovery steps
6. User can click [Reconnect] or [Cancel]
7. If server comes back, render resumes or restarts

Result: Clear feedback, user knows what happened
```

---

