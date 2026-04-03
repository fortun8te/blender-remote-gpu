#!/usr/bin/env python3
"""
Blender Remote GPU Render Server
Dual-mode: HTTP + raw TCP socket (no external dependencies)
"""

import json
import logging
import socket
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [Render] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

HTTP_PORT = 9876
SOCKET_PORT = 9877


def handle_message(data):
    """Process a message and return response dict."""
    msg_type = data.get('type', 'unknown')

    if msg_type == 'ping':
        return {
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
        return {
            'type': 'render_result',
            'jobId': job_id,
            'success': False,
            'error': 'Server in test mode - not rendering'
        }

    else:
        return {'type': 'error', 'message': f'Unknown: {msg_type}'}


# ── HTTP Server ──────────────────────────────────────────────

class RenderHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))
            logger.info(f"HTTP {data.get('type')} from {self.client_address[0]}")

            response = handle_message(data)
            response_bytes = json.dumps(response).encode('utf-8')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

        except Exception as e:
            logger.error(f"HTTP error: {e}")
            error_bytes = json.dumps({'error': str(e)}).encode('utf-8')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(error_bytes)))
            self.end_headers()
            self.wfile.write(error_bytes)

    def log_message(self, format, *args):
        pass  # Suppress default logging


def run_http_server():
    server = HTTPServer(('0.0.0.0', HTTP_PORT), RenderHandler)
    logger.info(f"HTTP server on port {HTTP_PORT}")
    server.serve_forever()


# ── Raw TCP Socket Server ────────────────────────────────────

def handle_socket_client(conn, addr):
    """Handle one TCP client: read length-prefixed JSON, respond, close."""
    try:
        # Read 4-byte length prefix
        length_bytes = b""
        while len(length_bytes) < 4:
            chunk = conn.recv(4 - len(length_bytes))
            if not chunk:
                return
            length_bytes += chunk

        length = int.from_bytes(length_bytes, 'big')
        if length > 10_000_000:  # 10MB max
            return

        # Read payload
        payload = b""
        while len(payload) < length:
            chunk = conn.recv(min(4096, length - len(payload)))
            if not chunk:
                return
            payload += chunk

        data = json.loads(payload.decode('utf-8'))
        logger.info(f"TCP {data.get('type')} from {addr[0]}")

        response = handle_message(data)
        response_bytes = json.dumps(response).encode('utf-8')

        # Send response: 4-byte length + JSON
        conn.sendall(len(response_bytes).to_bytes(4, 'big'))
        conn.sendall(response_bytes)

    except Exception as e:
        logger.error(f"TCP error from {addr}: {e}")
    finally:
        conn.close()


def run_socket_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', SOCKET_PORT))
    sock.listen(5)
    logger.info(f"TCP socket server on port {SOCKET_PORT}")

    while True:
        conn, addr = sock.accept()
        threading.Thread(
            target=handle_socket_client,
            args=(conn, addr),
            daemon=True
        ).start()


# ── Main ─────────────────────────────────────────────────────

def main():
    logger.info("Starting Blender Render Server")
    logger.info(f"  HTTP:   http://0.0.0.0:{HTTP_PORT}")
    logger.info(f"  Socket: tcp://0.0.0.0:{SOCKET_PORT}")
    logger.info("✓ Server ready")

    # Start both servers in threads
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    socket_thread = threading.Thread(target=run_socket_server, daemon=True)

    http_thread.start()
    socket_thread.start()

    # Keep main thread alive
    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        logger.info("Server stopped")


if __name__ == '__main__':
    main()
