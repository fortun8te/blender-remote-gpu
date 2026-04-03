"""Minimal WebSocket render server. Receives scenes, renders on GPU, streams frames back."""

import asyncio
import json
import os
import sys
import time
import tempfile
import subprocess
import shutil

# Add shared to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import websockets
    from websockets.asyncio.server import serve
except ImportError:
    print("[Server] ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

from shared.protocol import (
    PING, PONG, SCENE_UPLOAD, SCENE_ACK, FRAME, PROGRESS, ERROR,
    RENDER_START, RENDER_CANCEL, VIEWPORT_START, VIEWPORT_STOP,
    DEFAULT_HOST, DEFAULT_PORT,
)

# Detect GPU name
GPU_NAME = "Unknown GPU"
try:
    import subprocess as sp
    result = sp.run(
        ["nvidia-smi", "--query-gpu=name,memory.free", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode == 0:
        parts = result.stdout.strip().split(", ")
        GPU_NAME = parts[0] if parts else "NVIDIA GPU"
        GPU_VRAM_FREE = int(parts[1]) if len(parts) > 1 else 0
    else:
        GPU_VRAM_FREE = 0
except Exception:
    GPU_VRAM_FREE = 0

# Scene storage
SCENE_DIR = os.path.join(tempfile.gettempdir(), "blender-remote-gpu")
os.makedirs(SCENE_DIR, exist_ok=True)


class RenderServer:
    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self.clients = set()
        self.current_scene = None  # Path to latest .blend file

    async def handle_client(self, websocket):
        addr = websocket.remote_address
        print(f"[Server] Client connected: {addr}")
        self.clients.add(websocket)

        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # Binary frame -- scene data
                    await self._handle_binary(websocket, message)
                else:
                    # Text frame -- JSON command
                    await self._handle_json(websocket, message)
        except websockets.exceptions.ConnectionClosed as e:
            print(f"[Server] Client disconnected: {addr} ({e})")
        except Exception as e:
            print(f"[Server] Error with {addr}: {e}")
        finally:
            self.clients.discard(websocket)
            print(f"[Server] Client removed: {addr}")

    async def _handle_json(self, ws, raw):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_error(ws, "Invalid JSON")
            return

        msg_type = msg.get("type", "")
        print(f"[Server] Received: {msg_type}")

        if msg_type == PING:
            await ws.send(json.dumps({
                "type": PONG,
                "gpu": GPU_NAME,
                "vram_free": GPU_VRAM_FREE,
                "timestamp": time.time(),
            }))

        elif msg_type == SCENE_UPLOAD:
            # Next binary message will be the .blend file
            self._pending_scene_name = msg.get("filename", "scene.blend")
            self._pending_scene_size = msg.get("size", 0)
            print(f"[Server] Expecting scene: {self._pending_scene_name} ({self._pending_scene_size} bytes)")

        elif msg_type == RENDER_START:
            if not self.current_scene:
                await self._send_error(ws, "No scene uploaded yet")
                return
            width = msg.get("width", 1920)
            height = msg.get("height", 1080)
            samples = msg.get("samples", 128)
            await self._do_render(ws, width, height, samples)

        elif msg_type == RENDER_CANCEL:
            print("[Server] Render cancelled by client")

        elif msg_type == VIEWPORT_START:
            print("[Server] Viewport start (not yet implemented)")

        elif msg_type == VIEWPORT_STOP:
            print("[Server] Viewport stop")

        else:
            print(f"[Server] Unknown message type: {msg_type}")

    async def _handle_binary(self, ws, data):
        """Receive binary .blend file data."""
        scene_name = getattr(self, "_pending_scene_name", "scene.blend")
        scene_path = os.path.join(SCENE_DIR, scene_name)

        with open(scene_path, "wb") as f:
            f.write(data)

        self.current_scene = scene_path
        print(f"[Server] Scene saved: {scene_path} ({len(data)} bytes)")

        await ws.send(json.dumps({"type": SCENE_ACK}))

    async def _do_render(self, ws, width, height, samples):
        """Render the current scene using Blender CLI and return JPEG."""
        print(f"[Server] Rendering {width}x{height} @ {samples} samples...")

        # Find Blender executable
        blender_path = self._find_blender()
        if not blender_path:
            await self._send_error(ws, "Blender not found on this machine")
            return

        # Output path
        output_path = os.path.join(SCENE_DIR, "render_output.png")

        # Send progress
        await ws.send(json.dumps({
            "type": PROGRESS,
            "samples_done": 0,
            "samples_total": samples,
            "message": "Starting render...",
        }))

        # Run Blender headless render
        cmd = [
            blender_path, "-b", self.current_scene,
            "-o", os.path.join(SCENE_DIR, "render_output"),
            "-F", "PNG",
            "-x", "1",
            "--python-expr",
            f"import bpy; s=bpy.context.scene; s.render.resolution_x={width}; s.render.resolution_y={height}; s.cycles.samples={samples}; s.cycles.device='GPU'",
            "-f", "1",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

            if proc.returncode != 0:
                error_msg = stderr.decode()[-200:] if stderr else "Unknown error"
                await self._send_error(ws, f"Render failed: {error_msg}")
                return

            # Read the rendered image
            # Blender appends frame number: render_output0001.png
            actual_output = os.path.join(SCENE_DIR, "render_output0001.png")
            if not os.path.exists(actual_output):
                await self._send_error(ws, "Render output not found")
                return

            with open(actual_output, "rb") as f:
                image_data = f.read()

            # Send frame metadata then binary
            await ws.send(json.dumps({
                "type": FRAME,
                "width": width,
                "height": height,
                "format": "png",
            }))
            await ws.send(image_data)

            print(f"[Server] Render complete, sent {len(image_data)} bytes")

            # Cleanup
            os.remove(actual_output)

        except asyncio.TimeoutError:
            await self._send_error(ws, "Render timed out (5 min limit)")
        except Exception as e:
            await self._send_error(ws, f"Render error: {str(e)}")

    def _find_blender(self):
        """Find Blender executable on this system."""
        candidates = [
            shutil.which("blender"),
            r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
            "/Applications/Blender.app/Contents/MacOS/Blender",
        ]
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    async def _send_error(self, ws, message):
        print(f"[Server] ERROR: {message}")
        try:
            await ws.send(json.dumps({"type": ERROR, "message": message}))
        except Exception:
            pass

    async def run(self):
        print(f"[Server] Starting on 0.0.0.0:{self.port}")
        print(f"[Server] GPU: {GPU_NAME} ({GPU_VRAM_FREE}MB free)")
        print(f"[Server] Scene dir: {SCENE_DIR}")
        print(f"[Server] Waiting for client connections...")

        async with serve(
            self.handle_client,
            DEFAULT_HOST,
            self.port,
            ping_interval=30,
            ping_timeout=10,
            max_size=500 * 1024 * 1024,  # 500 MB for .blend files
        ):
            await asyncio.Future()  # Run forever


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Blender Remote GPU Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = RenderServer(port=args.port)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\n[Server] Shutting down.")


if __name__ == "__main__":
    main()
