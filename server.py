#!/usr/bin/env python3
"""
Blender Remote GPU Render Server
Listens on WebSocket for .blend files, renders them, sends back PNG results
"""

import asyncio
import json
import base64
import tempfile
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime
import logging

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [Render] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuration
BLENDER_PATH = "/Applications/Blender.app/Contents/MacOS/Blender"
RENDER_PORT = 9876
TEMP_DIR = tempfile.gettempdir()

# Render script template for Blender
BLENDER_RENDER_SCRIPT = '''
import bpy
import sys

try:
    # Parse arguments
    blend_file = sys.argv[-4]
    output_file = sys.argv[-3]
    width = int(sys.argv[-2])
    height = int(sys.argv[-1])

    # Open blend file
    bpy.ops.wm.open_mainfile(filepath=blend_file)

    # Configure render
    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.filepath = output_file

    # Use GPU if available (Cycles)
    if bpy.context.scene.cycles.device == 'CUDA' or bpy.context.scene.cycles.device == 'HIP':
        logger.info(f"Using GPU: {scene.cycles.device}")

    # Render
    bpy.ops.render.render(write_still=True)
    print("RENDER_SUCCESS")

except Exception as e:
    print(f"RENDER_ERROR: {e}")
    sys.exit(1)
'''


async def handle_render_request(websocket: WebSocketServerProtocol, message: dict):
    """Handle incoming render request"""
    job_id = message.get('jobId', 'unknown')

    try:
        logger.info(f"[{job_id}] Received render request")

        # Decode .blend file
        blend_data = base64.b64decode(message['blendFile'])
        width = message.get('width', 1920)
        height = message.get('height', 1080)
        samples = message.get('samples', 128)

        # Save blend file temporarily
        blend_path = os.path.join(TEMP_DIR, f"render_{job_id}.blend")
        with open(blend_path, 'wb') as f:
            f.write(blend_data)
        logger.info(f"[{job_id}] Saved blend file: {blend_path}")

        # Render
        output_path = os.path.join(TEMP_DIR, f"render_{job_id}.png")
        logger.info(f"[{job_id}] Starting Blender render ({width}x{height}, {samples} samples)...")

        # Write render script
        script_path = os.path.join(TEMP_DIR, f"render_{job_id}.py")
        with open(script_path, 'w') as f:
            f.write(BLENDER_RENDER_SCRIPT)

        # Run Blender
        cmd = [
            BLENDER_PATH,
            "--background",
            "--python", script_path,
            "--",
            blend_path,
            output_path,
            str(width),
            str(height),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        # Check result
        if "RENDER_SUCCESS" in result.stdout or os.path.exists(output_path):
            logger.info(f"[{job_id}] Render complete")

            # Read PNG
            with open(output_path, 'rb') as f:
                png_data = f.read()

            # Send back
            response = {
                'type': 'render_result',
                'jobId': job_id,
                'success': True,
                'pngBuffer': base64.b64encode(png_data).decode(),
            }
            await websocket.send(json.dumps(response))
            logger.info(f"[{job_id}] Sent result ({len(png_data)} bytes)")

            # Cleanup
            try:
                os.remove(blend_path)
                os.remove(output_path)
                os.remove(script_path)
            except:
                pass
        else:
            error = "Blender render failed"
            logger.error(f"[{job_id}] {error}\nstdout: {result.stdout}\nstderr: {result.stderr}")
            response = {
                'type': 'render_result',
                'jobId': job_id,
                'success': False,
                'error': error,
            }
            await websocket.send(json.dumps(response))

    except Exception as e:
        logger.error(f"[{job_id}] Error: {e}")
        try:
            response = {
                'type': 'render_result',
                'jobId': job_id,
                'success': False,
                'error': str(e),
            }
            await websocket.send(json.dumps(response))
        except:
            pass


async def handle_client(websocket: WebSocketServerProtocol, path: str):
    """Handle WebSocket client connection"""
    client_ip = websocket.remote_address[0]
    logger.info(f"Client connected from {client_ip}")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)

                if data.get('type') == 'render_request':
                    await handle_render_request(websocket, data)
                elif data.get('type') == 'ping':
                    await websocket.send(json.dumps({'type': 'pong'}))
                else:
                    logger.warning(f"Unknown message type: {data.get('type')}")

            except json.JSONDecodeError:
                logger.error("Invalid JSON received")
            except Exception as e:
                logger.error(f"Error handling message: {e}")

    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Client {client_ip} disconnected")
    except Exception as e:
        logger.error(f"Client error: {e}")


async def main():
    """Start WebSocket server"""
    logger.info(f"Starting Blender Render Server on port {RENDER_PORT}")
    logger.info(f"Blender path: {BLENDER_PATH}")

    if not os.path.exists(BLENDER_PATH):
        logger.error(f"Blender not found at {BLENDER_PATH}")
        sys.exit(1)

    async with websockets.serve(handle_client, "0.0.0.0", RENDER_PORT):
        logger.info(f"✓ Server ready, listening on port {RENDER_PORT}")
        logger.info("Waiting for render jobs...")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped")
