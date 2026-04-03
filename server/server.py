"""Minimal WebSocket render server. Receives scenes, renders on GPU, streams frames back."""

import asyncio
import json
import os
import sys
import time
import tempfile
import subprocess
import shutil
import platform

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

# UI/UX: Server version constants (sync with addon __init__.py)
SERVER_VERSION = "1.0.4"
SERVER_BUILD = "b4"

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

        # FIX #5: Track uploaded files for cleanup
        self._uploaded_scenes = {}  # {client_addr: [paths]}
        self._cleanup_interval = 3600  # Cleanup every hour
        self._last_cleanup = time.time()

    def _cleanup_client_files(self, client_addr):
        """Remove all uploaded files for a disconnected client (FIX #5)."""
        if client_addr not in self._uploaded_scenes:
            return

        files = self._uploaded_scenes.pop(client_addr, [])
        for filepath in files:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    print(f"[Server] Cleaned up: {filepath}")
            except Exception as e:
                print(f"[Server] Failed to clean {filepath}: {e}")

    def _cleanup_old_files(self, max_age=86400):
        """Remove scene files older than max_age seconds (default: 24 hours) (FIX #5)."""
        print(f"[Server] Running periodic cleanup (files older than {max_age}s)...")
        current_time = time.time()

        try:
            for filename in os.listdir(SCENE_DIR):
                filepath = os.path.join(SCENE_DIR, filename)
                if not os.path.isfile(filepath):
                    continue

                if filepath == self.current_scene:
                    continue

                age = current_time - os.path.getmtime(filepath)
                if age > max_age:
                    try:
                        os.remove(filepath)
                        print(f"[Server] Cleaned up old file: {filename} (age: {age:.0f}s)")
                    except Exception as e:
                        print(f"[Server] Failed to remove {filename}: {e}")
        except Exception as e:
            print(f"[Server] Cleanup error: {e}")

    async def handle_client(self, websocket):
        addr = websocket.remote_address
        print(f"[Server] Client connected: {addr}")
        self.clients.add(websocket)

        # FIX #5: Initialize cleanup list for this client
        self._uploaded_scenes[addr] = []

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

            # FIX #5: Cleanup all files from this client
            self._cleanup_client_files(addr)

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
                # UI/UX: Include version/build for connection status display
                "version": SERVER_VERSION,
                "build": SERVER_BUILD,
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
        """Receive binary .blend file data with automatic cleanup (FIX #5)."""
        scene_name = getattr(self, "_pending_scene_name", "scene.blend")
        scene_path = os.path.join(SCENE_DIR, scene_name)

        try:
            with open(scene_path, "wb") as f:
                f.write(data)

            self.current_scene = scene_path

            # FIX #5: Track file for cleanup on disconnect
            client_addr = ws.remote_address
            if client_addr not in self._uploaded_scenes:
                self._uploaded_scenes[client_addr] = []
            self._uploaded_scenes[client_addr].append(scene_path)

            print(f"[Server] Scene saved: {scene_path} ({len(data)} bytes)")
            await ws.send(json.dumps({"type": SCENE_ACK}))

        except IOError as e:
            await self._send_error(ws, f"Failed to save scene: {e}")

    async def _do_render(self, ws, width, height, samples):
        """Render the current scene using Blender CLI and return PNG (FIX #6, #7)."""
        print(f"[Server] Rendering {width}x{height} @ {samples} samples...")

        blender_path = self._find_blender()
        if not blender_path:
            await self._send_error(ws, "Blender not found on this machine")
            return

        output_files = []

        try:
            await ws.send(json.dumps({
                "type": PROGRESS,
                "samples_done": 0,
                "samples_total": samples,
                "message": "Starting render...",
            }))

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
                # FIX #6: Detailed error on subprocess creation
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                except FileNotFoundError as e:
                    await self._send_error(ws, f"Blender not found: {blender_path}\nError: {e}")
                    return
                except Exception as e:
                    await self._send_error(ws, f"Failed to start Blender: {e}")
                    return

                # FIX #6: Capture full output
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                        await proc.wait()
                    except:
                        pass
                    await self._send_error(ws, f"Render timed out (5 min)\nCommand: {' '.join(cmd)}")
                    return

                # FIX #6: Decode with error handling
                try:
                    stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
                    stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""
                except Exception as e:
                    await self._send_error(ws, f"Failed to decode output: {e}")
                    return

                # Log full output
                print(f"[Server] Blender stdout:\n{stdout_str}")
                if stderr_str:
                    print(f"[Server] Blender stderr:\n{stderr_str}")

                # FIX #6: Detailed error on failure
                if proc.returncode != 0:
                    error_lines = []
                    error_lines.append(f"Blender failed with exit code {proc.returncode}")
                    error_lines.append(f"Command: {' '.join(cmd)}")

                    if stderr_str:
                        stderr_lines = stderr_str.strip().split("\n")
                        relevant_error = "\n".join(stderr_lines[-10:])
                        error_lines.append(f"Error:\n{relevant_error}")
                    elif stdout_str:
                        stdout_lines = stdout_str.strip().split("\n")
                        relevant_output = "\n".join(stdout_lines[-10:])
                        error_lines.append(f"Output:\n{relevant_output}")

                    error_msg = "\n".join(error_lines)
                    await self._send_error(ws, error_msg)
                    return

                # Check for GPU warnings
                if "GPU" in stderr_str or "device" in stderr_str.lower():
                    print(f"[Server] GPU info:\n{stderr_str}")

                # FIX #6: Check output exists
                actual_output = os.path.join(SCENE_DIR, "render_output0001.png")
                if not os.path.exists(actual_output):
                    await self._send_error(
                        ws,
                        f"Render output not found: {actual_output}\n"
                        f"Blender exited successfully but no image was created."
                    )
                    return

                output_files.append(actual_output)

                with open(actual_output, "rb") as f:
                    image_data = f.read()

                if not image_data:
                    await self._send_error(ws, f"Render file is empty: {actual_output}")
                    return

                # Send result
                await ws.send(json.dumps({
                    "type": FRAME,
                    "width": width,
                    "height": height,
                    "format": "png",
                }))
                await ws.send(image_data)

                print(f"[Server] Render complete, sent {len(image_data)} bytes")

            except asyncio.TimeoutError:
                await self._send_error(ws, "Render timed out (5 min limit)")
            except Exception as e:
                import traceback
                await self._send_error(ws, f"Unexpected error: {e}\nTraceback: {traceback.format_exc()}")

        finally:
            # FIX #7: Cleanup all output files on all error paths
            for filepath in output_files:
                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                        print(f"[Server] Cleaned up render output: {filepath}")
                except Exception as e:
                    print(f"[Server] Failed to cleanup {filepath}: {e}")

            # FIX #5: Periodic cleanup
            if time.time() - self._last_cleanup > self._cleanup_interval:
                self._cleanup_old_files()
                self._last_cleanup = time.time()

    def _find_blender(self):
        """Find Blender executable on this system (FIX #4).

        Tries in order:
        1. PATH environment variable (shutil.which)
        2. Windows: Registry HKLM/HKCU (uninstall paths)
        3. Windows: Standard program files paths (all versions 4.x-5.x)
        4. Windows: Program Files (x86) for 32-bit installs
        5. macOS: /Applications/Blender.app
        6. Linux: /usr/bin, /opt, /usr/local/bin
        """
        system = platform.system()

        # 1. Try PATH first (most reliable)
        path = shutil.which("blender")
        if path:
            print(f"[Server] Found Blender via PATH: {path}")
            return path

        # 2. Windows registry lookup
        if system == "Windows":
            try:
                import winreg
                reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
                key = winreg.OpenKey(reg, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")

                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        if "Blender" not in subkey_name:
                            continue

                        subkey = winreg.OpenKey(key, subkey_name)
                        try:
                            install_path = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                            blender_exe = os.path.join(install_path, "blender.exe")
                            if os.path.exists(blender_exe):
                                print(f"[Server] Found Blender via registry: {blender_exe}")
                                return blender_exe
                        finally:
                            winreg.CloseKey(subkey)
                    except Exception:
                        continue

                winreg.CloseKey(key)
            except Exception as e:
                print(f"[Server] Registry lookup failed: {e}")

        # 3. Platform-specific hardcoded paths
        candidates = []

        if system == "Windows":
            base_paths = [
                r"C:\Program Files\Blender Foundation",
                r"C:\Program Files (x86)\Blender Foundation",
            ]
            for base in base_paths:
                # Generate versions 4.0-5.3
                for major in [4, 5]:
                    for minor in range(10):
                        version = f"{major}.{minor}"
                        candidates.append(
                            os.path.join(base, f"Blender {version}", "blender.exe")
                        )

            # AppData and home directory
            appdata = os.getenv("APPDATA")
            if appdata:
                candidates.append(os.path.join(appdata, "Blender", "blender.exe"))

            home = os.path.expanduser("~")
            candidates.extend([
                os.path.join(home, "Blender", "blender.exe"),
                os.path.join(home, "Documents", "Blender", "blender.exe"),
            ])

        elif system == "Darwin":  # macOS
            candidates.extend([
                "/Applications/Blender.app/Contents/MacOS/Blender",
                os.path.expanduser("~/Applications/Blender.app/Contents/MacOS/Blender"),
                "/opt/homebrew/bin/blender",
                "/usr/local/bin/blender",
            ])

        elif system == "Linux":
            candidates.extend([
                "/usr/bin/blender",
                "/usr/local/bin/blender",
                "/opt/blender/blender",
                "/snap/bin/blender",
                os.path.expanduser("~/blender/blender"),
            ])

        # Try all candidates
        for candidate in candidates:
            try:
                if candidate and os.path.exists(candidate):
                    if os.access(candidate, os.X_OK):
                        print(f"[Server] Found Blender: {candidate}")
                        return candidate
            except Exception:
                continue

        print("[Server] ERROR: Blender not found. Checked:")
        print("  - PATH via shutil.which()")
        if system == "Windows":
            print("  - Windows registry")
            print("  - C:\\Program Files\\Blender Foundation\\Blender *.*")
            print("  - C:\\Program Files (x86)\\Blender Foundation\\Blender *.*")
        elif system == "Darwin":
            print("  - /Applications/Blender.app")
            print("  - /opt/homebrew/bin/blender")
            print("  - /usr/local/bin/blender")
        elif system == "Linux":
            print("  - /usr/bin/blender")
            print("  - /usr/local/bin/blender")
            print("  - /opt/blender/blender")
            print("  - /snap/bin/blender")

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
