#!/usr/bin/env python3
"""
Blender Remote GPU Render Server
Listens on WebSocket for render jobs
"""

import asyncio
import json
import logging
import sys
from datetime import datetime

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [Render] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

RENDER_PORT = 9876


async def handle_client(websocket):
    """Handle WebSocket client connection"""
    try:
        client_ip = websocket.remote_address[0]
    except:
        client_ip = 'unknown'
    logger.info(f"Client connected from {client_ip}")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get('type', 'unknown')
                logger.info(f"Received {msg_type} from {client_ip}")

                if msg_type == 'ping':
                    await websocket.send(json.dumps({'type': 'pong'}))

                elif msg_type == 'render_request':
                    job_id = data.get('jobId', 'unknown')
                    logger.info(f"Render job {job_id}: would process .blend file")
                    # Echo back for testing
                    response = {
                        'type': 'render_result',
                        'jobId': job_id,
                        'success': False,
                        'error': 'Server in test mode - not rendering'
                    }
                    await websocket.send(json.dumps(response))
                    logger.info(f"Sent response for job {job_id}")

                else:
                    logger.warning(f"Unknown message type: {msg_type}")

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON from {client_ip}: {e}")
                try:
                    await websocket.send(json.dumps({'type': 'error', 'message': 'Invalid JSON'}))
                except:
                    pass
            except Exception as e:
                logger.error(f"Error processing message: {e}")

    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Client {client_ip} disconnected")
    except Exception as e:
        logger.error(f"Client error: {e}")


async def main():
    """Start WebSocket server"""
    logger.info(f"Starting Blender Render Server on port {RENDER_PORT}")

    server = await websockets.serve(handle_client, '0.0.0.0', RENDER_PORT)
    logger.info(f"Server listening on 0.0.0.0:{RENDER_PORT}")
    logger.info(f"✓ Server ready, listening on port {RENDER_PORT}")
    logger.info("Waiting for render jobs...")

    try:
        await asyncio.Future()  # run forever
    except KeyboardInterrupt:
        logger.info("Server stopped")
    finally:
        server.close()
        await server.wait_closed()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server interrupted")
