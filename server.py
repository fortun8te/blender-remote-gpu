#!/usr/bin/env python3
"""
Blender Remote GPU Render Server
HTTP server for render jobs (no external dependencies)
"""

import json
import logging
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [Render] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

RENDER_PORT = 9876


class RenderHandler(BaseHTTPRequestHandler):
    """HTTP request handler for render server"""

    def do_POST(self):
        """Handle POST requests"""
        print(f"[Server] Received POST request from {self.client_address[0]}", file=sys.stderr)

        try:
            client_ip = self.client_address[0]
        except Exception as e:
            client_ip = 'unknown'
            print(f"[Server] Error getting client IP: {e}", file=sys.stderr)

        try:
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            print(f"[Server] Content-Length: {content_length}", file=sys.stderr)

            body = self.rfile.read(content_length)
            print(f"[Server] Body: {body}", file=sys.stderr)

            data = json.loads(body.decode('utf-8'))
            msg_type = data.get('type', 'unknown')
            logger.info(f"Received {msg_type} from {client_ip}")
            print(f"[Server] Parsed message type: {msg_type}", file=sys.stderr)

            response = None

            if msg_type == 'ping':
                response = {
                    'type': 'pong',
                    'gpu': 'NVIDIA GeForce RTX 5080',
                    'vram_free': 14865,
                    'timestamp': datetime.now().timestamp(),
                    'version': '1.0.4',
                    'build': 'b4'
                }

            elif msg_type == 'render_request':
                job_id = data.get('jobId', 'unknown')
                logger.info(f"Render job {job_id}: would process .blend file")
                response = {
                    'type': 'render_result',
                    'jobId': job_id,
                    'success': False,
                    'error': 'Server in test mode - not rendering'
                }

            else:
                logger.warning(f"Unknown message type: {msg_type}")
                response = {
                    'type': 'error',
                    'message': f'Unknown message type: {msg_type}'
                }

            print(f"[Server] Sending response: {response}", file=sys.stderr)

            # Send response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(json.dumps(response).encode('utf-8')))
            self.end_headers()
            self.wfile.write(json.dumps(response).encode('utf-8'))
            self.wfile.flush()
            logger.info(f"Sent response for {msg_type}")
            print(f"[Server] Response sent successfully", file=sys.stderr)

        except json.JSONDecodeError as e:
            print(f"[Server] JSON decode error: {e}", file=sys.stderr)
            logger.error(f"Invalid JSON from {client_ip}: {e}")
            try:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode('utf-8'))
            except Exception as write_err:
                print(f"[Server] Error writing response: {write_err}", file=sys.stderr)

        except Exception as e:
            print(f"[Server] Unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
            logger.error(f"Error processing request: {e}")
            try:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))
            except Exception as write_err:
                print(f"[Server] Error writing error response: {write_err}", file=sys.stderr)

    def log_message(self, format, *args):
        """Suppress default HTTP logging"""
        pass


def main():
    """Start HTTP server"""
    logger.info(f"Starting Blender Render Server on port {RENDER_PORT}")

    server = HTTPServer(('0.0.0.0', RENDER_PORT), RenderHandler)
    logger.info(f"Server listening on 0.0.0.0:{RENDER_PORT}")
    logger.info(f"✓ Server ready, listening on port {RENDER_PORT}")
    logger.info("Waiting for render jobs...")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server interrupted")
    finally:
        server.server_close()
        logger.info("Server stopped")


if __name__ == '__main__':
    main()
