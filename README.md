# Blender Remote GPU

**Run Cycles on a remote Windows GPU from Blender on another machine** (e.g. Mac + RTX box). A Blender addon talks over **WebSockets** (optionally **TLS** + **Tailscale**) to a small Python server that drives **Blender headless** on the GPU host.

| Role | Where |
|------|--------|
| **Render server** | Windows PC with NVIDIA GPU — run `start_server.bat` or `python server/server.py` |
| **Blender client** | Your workstation — install the addon from `addon/` or use `blender_remote_gpu_addon.zip` |

## Quick start

1. **GPU machine:** Install [Blender](https://www.blender.org/download/) 4.x, [Python 3.10+](https://www.python.org/), clone this repo, `pip install -r requirements.txt`, then run **`start_server.bat`** (Windows) or **`start_server.sh`** (Linux).
2. **VPN:** Put both machines on the same [Tailscale](https://tailscale.com/) network (recommended).
3. **Blender:** Install the addon, set server IP (Tailscale IP of the Windows box) and port **9876**, connect, choose **Cycles REMOTE GPU** as the render engine.

Detailed steps: **[docs/setup/WINDOWS_SETUP.md](docs/setup/WINDOWS_SETUP.md)** · **[docs/setup/TAILSCALE_SETUP.md](docs/setup/TAILSCALE_SETUP.md)** · **[docs/setup/QUICKSTART.md](docs/setup/QUICKSTART.md)**

## Repo layout

```
addon/          # Blender addon (copy or zip for Preferences → Install)
server/         # WebSocket render server
shared/         # Protocol, constants, dev_config (client + server)
tests/          # Unit tests
examples/       # Small Python examples
scripts/        # dev_reload, e2e_simulation, validate_integration
docs/setup/     # Install & network guides
docs/reference/ # Architecture, API, delta sync, logging, …
docs/archive/   # Older reports, audits, E2E notes (historical)
```

## Documentation index

See **[docs/README.md](docs/README.md)** for a full list of guides.

## Requirements

- **Server:** `websockets`, `msgpack`, `Pillow`, `numpy` (see `requirements.txt`)
- **Client (Blender):** same stack if you install deps for the addon manually; many setups bundle or install via Blender’s Python.

## Security note

Default dev **API key** and self-signed **TLS** are for private networks. Change `API_KEY`, use real certificates, and restrict access before exposing to the internet.

---

**Suggested GitHub “About” description** (copy into the repository description field):

> Blender addon + WebSocket server — stream Cycles viewport and final renders to a remote Windows GPU over Tailscale (TLS).

**Suggested topics:** `blender`, `cycles`, `remote-rendering`, `websocket`, `gpu`, `tailscale`, `python`
