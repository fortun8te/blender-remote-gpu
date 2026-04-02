#!/bin/bash
# macOS/Linux script to start the render server

echo ""
echo "===================================="
echo "Remote GPU Render Server Startup"
echo "===================================="
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found. Please install from python.org or Homebrew"
    exit 1
fi

# Check if we're in the right directory
if [ ! -f "server/server.py" ]; then
    echo "ERROR: server/server.py not found."
    echo "Please run this from the blender-remote-gpu directory"
    exit 1
fi

# Install dependencies if needed
echo "Checking dependencies..."
python3 -m pip install -q websockets msgpack pillow numpy

# Generate TLS certificate if not present
if [ ! -f "/tmp/cert.pem" ]; then
    echo "Setting up TLS certificate..."
    chmod +x setup_tls.sh
    ./setup_tls.sh || exit 1
fi

# Install dependencies if needed
echo "Checking dependencies..."
python3 -m pip install -q websockets msgpack pillow numpy

# Start the server
echo ""
echo "Starting server on port 9876 (Tailscale) with TLS..."
echo "Press Ctrl+C to stop"
echo ""
echo "Tailscale IP: Check 'tailscale ip' or 'tailscale status'"
echo ""

python3 server/server.py --port 9876 --use-tls
