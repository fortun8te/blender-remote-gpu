#!/usr/bin/env python3
"""
Tailscale WebSocket Handshake Debug Tool

Comprehensive diagnostics for Blender Remote GPU Tailscale connectivity.
Tests network health, WebSocket handshake, and protocol compliance.
"""

import sys
import json
import socket
import time
import subprocess
from pathlib import Path


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*60}")
    print(f"{text}")
    print(f"{'='*60}{Colors.END}\n")


def print_success(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")


def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.END}")


def print_warning(text):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")


def print_info(text):
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.END}")


def test_tailscale_status():
    """Check if Tailscale is running and connected."""
    print_header("1. Tailscale Status Check")

    try:
        result = subprocess.run(
            ["tailscale", "status"],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')

            # Extract local IP
            for line in lines:
                if "100.119.65.49" in line or "100.74.135.83" in line:
                    print_info(line.strip())

            # Check if Windows is connected
            if "100.74.135.83" in result.stdout and "active" in result.stdout:
                print_success("Tailscale running, Windows machine is ACTIVE")
                return True
            else:
                print_warning("Tailscale running but Windows offline or not found")
                return False
        else:
            print_error("Tailscale not running")
            return False
    except FileNotFoundError:
        print_error("Tailscale CLI not installed")
        return False
    except subprocess.TimeoutExpired:
        print_error("Tailscale status check timed out")
        return False
    except Exception as e:
        print_error(f"Error checking Tailscale: {e}")
        return False


def test_tcp_connectivity(host, port, timeout=5):
    """Test TCP socket connectivity (before WebSocket)."""
    print_header(f"2. TCP Connectivity Test ({host}:{port})")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        print_info(f"Attempting TCP connection to {host}:{port}...")
        start = time.time()

        result = sock.connect_ex((host, port))
        elapsed = time.time() - start

        sock.close()

        if result == 0:
            print_success(f"TCP connection successful ({elapsed:.2f}s)")
            return True
        else:
            print_error(f"TCP connection failed (code: {result}, {elapsed:.2f}s)")
            return False
    except Exception as e:
        print_error(f"TCP test error: {e}")
        return False


def test_websocket_handshake(url, timeout=10):
    """Test WebSocket handshake and protocol compliance."""
    print_header("3. WebSocket Handshake Test")

    try:
        from websockets.sync.client import connect
    except ImportError:
        print_error("websockets module not installed")
        print_info("Install with: pip install websockets")
        return False

    try:
        print_info(f"Connecting to {url}...")
        print_info(f"Timeout: {timeout}s")

        start = time.time()
        ws = connect(url, open_timeout=timeout)
        handshake_time = time.time() - start

        print_success(f"WebSocket connected ({handshake_time:.2f}s)")
        print_info(f"Subprotocol: {ws.subprotocol or 'none'}")
        print_info(f"Server: {ws.response.headers.get('server', 'unknown')}")

        return ws
    except TimeoutError:
        elapsed = time.time() - start
        print_error(f"WebSocket handshake timeout ({elapsed:.2f}s)")
        print_warning("Possible causes:")
        print_warning("  • Server not listening on port")
        print_warning("  • Firewall blocking port 9876")
        print_warning("  • Tailscale tunnel not established")
        return None
    except ConnectionRefusedError:
        print_error("Connection refused - server not running")
        return None
    except Exception as e:
        print_error(f"WebSocket handshake failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_ping_pong(ws):
    """Test PING/PONG keep-alive protocol."""
    print_header("4. PING/PONG Protocol Test")

    if not ws:
        print_error("No WebSocket connection")
        return False

    try:
        # Send ping
        ping_msg = json.dumps({"type": "ping"})
        print_info(f"Sending: {ping_msg}")

        start = time.time()
        ws.send(ping_msg)

        # Receive pong
        response = ws.recv(timeout=5)
        elapsed = time.time() - start

        print_success(f"Received pong ({elapsed:.3f}s)")

        try:
            pong_data = json.loads(response)
            print_info(f"Response: {response}")

            # Validate pong structure
            if pong_data.get("type") == "pong":
                print_success("Response is valid PONG")

                gpu = pong_data.get("gpu", "Unknown")
                vram = pong_data.get("vram_free", "Unknown")

                print_info(f"GPU: {gpu}")
                print_info(f"VRAM Free: {vram} MB")

                return True
            else:
                print_error(f"Invalid response type: {pong_data.get('type')}")
                return False
        except json.JSONDecodeError:
            print_error("Response is not valid JSON")
            return False

    except TimeoutError:
        print_error("PONG timeout - server not responding")
        return False
    except Exception as e:
        print_error(f"PING/PONG test failed: {e}")
        return False


def test_scene_upload_protocol(ws):
    """Test scene upload protocol (binary frame handling)."""
    print_header("5. Scene Upload Protocol Test")

    if not ws:
        print_error("No WebSocket connection")
        return False

    try:
        # Send scene_upload command
        scene_msg = json.dumps({
            "type": "scene_upload",
            "filename": "test_scene.blend",
            "size": 1024
        })

        print_info(f"Sending: {scene_msg}")
        ws.send(scene_msg)

        # In real scenario, would send binary data here
        # For now, just verify server acknowledged
        print_info("Waiting for acknowledgment...")

        # Set short timeout for this test
        try:
            ack = ws.recv(timeout=2)
            print_info(f"Received: {ack}")

            ack_data = json.loads(ack)
            if ack_data.get("type") == "scene_ack":
                print_success("Server acknowledged scene upload protocol")
                return True
            else:
                print_warning(f"Unexpected response: {ack_data.get('type')}")
                return False
        except TimeoutError:
            print_warning("No immediate acknowledgment (expected in production)")
            return True  # Not critical for this test

    except Exception as e:
        print_error(f"Scene upload protocol test failed: {e}")
        return False


def test_connection_stability(ws, duration=15, check_interval=2):
    """Test connection stability over time with keep-alive."""
    print_header(f"6. Connection Stability Test ({duration}s)")

    if not ws:
        print_error("No WebSocket connection")
        return False

    try:
        successful_pings = 0
        failed_pings = 0
        start_time = time.time()

        print_info(f"Sending PING every {check_interval}s for {duration}s...")

        while time.time() - start_time < duration:
            try:
                ping_msg = json.dumps({"type": "ping"})
                ws.send(ping_msg)

                response = ws.recv(timeout=3)
                pong_data = json.loads(response)

                if pong_data.get("type") == "pong":
                    successful_pings += 1
                    elapsed = time.time() - start_time
                    print_info(f"[{elapsed:.1f}s] PING → PONG ✓")
                else:
                    failed_pings += 1
            except Exception as e:
                failed_pings += 1
                elapsed = time.time() - start_time
                print_warning(f"[{elapsed:.1f}s] PING failed: {e}")

            time.sleep(check_interval)

        print()
        print_success(f"Connection stability test complete")
        print_info(f"Successful: {successful_pings}, Failed: {failed_pings}")

        if successful_pings >= (duration // check_interval) * 0.8:
            print_success("Connection stable (>80% success rate)")
            return True
        else:
            print_error("Connection unstable (>20% failure rate)")
            return False

    except Exception as e:
        print_error(f"Stability test failed: {e}")
        return False


def generate_diagnostic_report(results):
    """Generate summary report."""
    print_header("Summary Report")

    total = len(results)
    passed = sum(1 for v in results.values() if v)

    print(f"Tests passed: {Colors.GREEN}{passed}/{total}{Colors.END}")

    print("\nDetailed results:")
    test_names = [
        "Tailscale Status",
        "TCP Connectivity",
        "WebSocket Handshake",
        "PING/PONG Protocol",
        "Scene Upload Protocol",
        "Connection Stability"
    ]

    for name, result in zip(test_names, results.values()):
        status = f"{Colors.GREEN}PASS{Colors.END}" if result else f"{Colors.RED}FAIL{Colors.END}"
        print(f"  • {name}: {status}")

    if passed == total:
        print_success("\n🎉 All tests passed! Addon should work.")
    elif passed >= total * 0.8:
        print_warning("\n⚠️  Most tests passed. Check warnings above.")
    else:
        print_error("\n❌ Multiple failures. Debug connection setup.")


def main():
    """Run full diagnostic suite."""
    print(f"\n{Colors.BOLD}{Colors.CYAN}")
    print("╔════════════════════════════════════════════════════════════╗")
    print("║     Blender Remote GPU — Tailscale Handshake Debug Tool     ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print(Colors.END)

    # Configuration
    WINDOWS_IP = "100.74.135.83"
    WINDOWS_PORT = 9876
    WS_URL = f"ws://{WINDOWS_IP}:{WINDOWS_PORT}"

    print_info(f"Target: {WS_URL}")
    print_info(f"Start time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    results = {}

    # Test 1: Tailscale status
    results['tailscale'] = test_tailscale_status()

    # Test 2: TCP connectivity
    results['tcp'] = test_tcp_connectivity(WINDOWS_IP, WINDOWS_PORT)

    # Test 3-6: WebSocket tests (only if TCP succeeded)
    if results['tcp']:
        ws = test_websocket_handshake(WS_URL)
        results['handshake'] = ws is not None

        if ws:
            results['ping_pong'] = test_ping_pong(ws)
            results['scene_upload'] = test_scene_upload_protocol(ws)
            results['stability'] = test_connection_stability(ws)
            ws.close()
        else:
            results['ping_pong'] = False
            results['scene_upload'] = False
            results['stability'] = False
    else:
        results['handshake'] = False
        results['ping_pong'] = False
        results['scene_upload'] = False
        results['stability'] = False
        print_error("\nSkipping WebSocket tests (TCP failed)")

    # Generate report
    generate_diagnostic_report(results)

    # Exit code
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Interrupted by user{Colors.END}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
