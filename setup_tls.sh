#!/bin/bash
# Generate self-signed TLS certificate for development
# Run once on Windows server machine

echo "Generating self-signed TLS certificate for Tailscale..."
openssl req -x509 -newkey rsa:4096 -nodes \
  -out /tmp/cert.pem \
  -keyout /tmp/key.pem \
  -days 365 \
  -subj "/C=US/ST=CA/L=Local/O=RemoteGPU/CN=degitaar.local"

if [ -f /tmp/cert.pem ] && [ -f /tmp/key.pem ]; then
    echo "✓ TLS certificate generated:"
    echo "  - /tmp/cert.pem"
    echo "  - /tmp/key.pem"
    echo ""
    echo "Server will use TLS when connecting to Tailscale"
else
    echo "✗ Certificate generation failed"
    exit 1
fi
