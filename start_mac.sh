#!/bin/bash
# Remote GPU — Mac side setup
# Run once to verify your Thunderbolt connection is working.

echo "============================================================"
echo "  Remote GPU — Mac connection check"
echo "============================================================"
echo ""

# Check if TB4 cable creates a network interface
TB4_IP=$(ifconfig | grep -A1 'bridge\|en[5-9]\|en1[0-9]' | grep 'inet 169\.254\.' | awk '{print $2}' | head -1)

if [ -n "$TB4_IP" ]; then
    echo "✓ Thunderbolt 4 link detected — Mac IP: $TB4_IP"
    echo ""
    echo "Your Mac is on the 169.254.x.x link-local network."
    echo "Windows PC will be on the same subnet."
    echo ""
else
    echo "No TB4 link-local address found."
    echo "Try: plug in the cable, wait 10 seconds, run this again."
    echo ""
    echo "Checking all network interfaces..."
    ifconfig | grep 'inet ' | grep -v '127.0.0.1'
    echo ""
fi

echo "Next steps:"
echo "  1. Start START_SERVER.bat on your Windows PC"
echo "  2. Open Blender on Mac"
echo "  3. N-Panel → Remote GPU → Auto-discover (TB4 / LAN)"
echo "  4. Click Connect, Upload Scene, Start Live Preview"
echo ""
echo "============================================================"
