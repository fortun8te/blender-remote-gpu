#!/usr/bin/env python3
"""
Diagnostic test suite for Blender Remote GPU remote_gpu_render.

Run without Blender:  python3 test_remote_gpu_render.py
Run with Blender:     blender --background --python test_remote_gpu_render.py

Tests all 4 connection methods and server connectivity.
"""

import json
import sys
import time
import os

# Add remote_gpu_render to path for testing
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Default server settings
SERVER_IP = os.environ.get("RENDER_SERVER_IP", "100.74.135.83")
SERVER_PORT = int(os.environ.get("RENDER_SERVER_PORT", "9876"))

PASS = "\033[92m PASS \033[0m"
FAIL = "\033[91m FAIL \033[0m"
SKIP = "\033[93m SKIP \033[0m"

results = []


def test(name, fn):
    """Run a test and record result."""
    try:
        fn()
        print(f"  [{PASS}] {name}")
        results.append(("PASS", name))
    except Exception as e:
        print(f"  [{FAIL}] {name}: {e}")
        results.append(("FAIL", name, str(e)))


def test_skip(name, reason):
    print(f"  [{SKIP}] {name}: {reason}")
    results.append(("SKIP", name, reason))


# ── Server Tests ──────────────────────────────────────────────

def test_server_http():
    """Test HTTP connection to server."""
    print(f"\n--- Server Tests (target: {SERVER_IP}:{SERVER_PORT}) ---")

    def _http():
        import urllib.request
        data = json.dumps({"type": "ping"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://{SERVER_IP}:{SERVER_PORT}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        assert result["type"] == "pong", f"Expected pong, got {result['type']}"
        print(f"         GPU: {result.get('gpu', '?')}, VRAM: {result.get('vram_free', '?')} MB")

    test("HTTP (urllib.request)", _http)


def test_server_http_client():
    def _http_client():
        import http.client
        data = json.dumps({"type": "ping"}).encode("utf-8")
        conn = http.client.HTTPConnection(SERVER_IP, SERVER_PORT, timeout=5)
        conn.request("POST", "/", body=data, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        result = json.loads(resp.read().decode("utf-8"))
        conn.close()
        assert result["type"] == "pong"

    test("HTTP (http.client)", _http_client)


def test_server_socket():
    def _socket():
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((SERVER_IP, SERVER_PORT + 1))
        payload = json.dumps({"type": "ping"}).encode("utf-8")
        sock.sendall(len(payload).to_bytes(4, "big"))
        sock.sendall(payload)
        length = int.from_bytes(sock.recv(4), "big")
        data = b""
        while len(data) < length:
            data += sock.recv(4096)
        result = json.loads(data.decode("utf-8"))
        sock.close()
        assert result["type"] == "pong"

    test("TCP Socket (port+1)", _socket)


def test_server_xmlrpc():
    def _xmlrpc():
        import xmlrpc.client
        proxy = xmlrpc.client.ServerProxy(
            f"http://{SERVER_IP}:{SERVER_PORT + 2}", allow_none=True
        )
        result_str = proxy.handle(json.dumps({"type": "ping"}))
        result = json.loads(result_str)
        assert result["type"] == "pong"

    test("XML-RPC (port+2)", _xmlrpc)


# ── Connection Cascade Test ───────────────────────────────────

def test_connection_cascade():
    print(f"\n--- Connection Cascade Test ---")

    def _cascade():
        # Import connection module directly (avoid bpy dependency)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "connection",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "remote_gpu_render", "connection.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        conn = mod.Connection(SERVER_IP, SERVER_PORT)
        conn.connect()
        assert conn.connected, f"Not connected: {conn.error}"
        print(f"         Method: {conn.method}, GPU: {conn.gpu_name}, Latency: {conn.latency_ms}ms")
        conn.close()

    test("4-method cascade", _cascade)


# ── Blender Tests ─────────────────────────────────────────────

def test_blender():
    print(f"\n--- Blender Tests ---")

    try:
        import bpy
    except ImportError:
        test_skip("Blender import", "Not running inside Blender")
        test_skip("Addon registration", "Not running inside Blender")
        test_skip("Preferences loading", "Not running inside Blender")
        return

    def _import():
        import bpy
        assert bpy.app.version >= (4, 0, 0), f"Blender {bpy.app.version} < 4.0"

    test("Blender import", _import)

    def _register():
        import bpy
        # Enable the remote_gpu_render
        bpy.ops.preferences.remote_gpu_render_enable(module="remote_gpu_render")
        assert "remote_gpu_render" in bpy.context.preferences.remote_gpu_renders or \
               any("remote" in k.lower() for k in bpy.context.preferences.remote_gpu_renders.keys()), \
               "Addon not found in preferences"

    test("Addon registration", _register)

    def _prefs():
        import bpy
        # Find the remote_gpu_render preferences
        found = False
        for name, remote_gpu_render in bpy.context.preferences.remote_gpu_renders.items():
            if hasattr(remote_gpu_render, "preferences") and hasattr(remote_gpu_render.preferences, "server_ip"):
                found = True
                print(f"         Addon name: '{name}'")
                print(f"         Server IP: {remote_gpu_render.preferences.server_ip}")
                print(f"         Server Port: {remote_gpu_render.preferences.server_port}")
                break
        assert found, "Addon preferences with server_ip not found"

    test("Preferences loading", _prefs)


# ── Local Server Test ─────────────────────────────────────────

def test_local_server():
    """Start local server and test against it."""
    print(f"\n--- Local Server Test ---")

    def _local():
        import subprocess
        import signal

        server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
        if not os.path.isfile(server_path):
            raise FileNotFoundError(f"server.py not found at {server_path}")

        # Start server
        proc = subprocess.Popen(
            [sys.executable, server_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(2)  # Let it start

        try:
            import urllib.request
            data = json.dumps({"type": "ping"}).encode("utf-8")
            req = urllib.request.Request(
                "http://localhost:9876",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            assert result["type"] == "pong", f"Expected pong, got {result}"
            print(f"         Local server responding correctly")
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    test("Local server start + ping", _local)


# ── Main ──────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Blender Remote GPU Addon — Diagnostic Test Suite")
    print(f"Server: {SERVER_IP}:{SERVER_PORT}")
    print(f"Python: {sys.version}")
    print("=" * 60)

    # Start local server for localhost tests
    local_proc = None
    if SERVER_IP in ("localhost", "127.0.0.1"):
        import subprocess
        server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
        local_proc = subprocess.Popen(
            [sys.executable, server_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(2)
        print("  [Started local server for testing]")
    else:
        test_local_server()

    # Test server (all 4 methods)
    test_server_http()
    test_server_http_client()
    test_server_socket()
    test_server_xmlrpc()

    # Test connection cascade
    test_connection_cascade()

    # Test Blender integration
    test_blender()

    # Cleanup local server
    if local_proc:
        local_proc.terminate()
        local_proc.wait(timeout=5)

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r[0] == "PASS")
    failed = sum(1 for r in results if r[0] == "FAIL")
    skipped = sum(1 for r in results if r[0] == "SKIP")
    total = len(results)
    print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")

    if failed > 0:
        print("\nFailed tests:")
        for r in results:
            if r[0] == "FAIL":
                print(f"  - {r[1]}: {r[2]}")

    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
