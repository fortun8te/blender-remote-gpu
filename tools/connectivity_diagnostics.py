#!/usr/bin/env python3
"""
Comprehensive Connectivity Diagnostic Suite for Blender Remote GPU

This tool provides advanced diagnostics with:
- JSON export for bug reports
- Network latency histogram
- Bandwidth estimation
- Timeout recovery testing
- Connection flakiness detection
- HTML report generation
"""

import sys
import json
import socket
import time
import subprocess
import statistics
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
import html


@dataclass
class TestResult:
    """Result of a single diagnostic test."""
    name: str
    passed: bool
    duration: float
    message: str
    details: Dict[str, Any]
    timestamp: str


class DiagnosticReporter:
    """Collects and formats diagnostic results."""

    def __init__(self):
        self.results: List[TestResult] = []
        self.start_time = datetime.now()

    def add_result(self, name: str, passed: bool, duration: float,
                   message: str, details: Optional[Dict] = None):
        """Record a test result."""
        self.results.append(TestResult(
            name=name,
            passed=passed,
            duration=duration,
            message=message,
            details=details or {},
            timestamp=datetime.now().isoformat()
        ))

    def summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        total_duration = sum(r.duration for r in self.results)

        return {
            "total_tests": total,
            "passed": passed,
            "failed": failed,
            "success_rate": (passed / total * 100) if total > 0 else 0,
            "total_duration": total_duration,
            "timestamp": datetime.now().isoformat(),
        }

    def to_json(self) -> str:
        """Export results as JSON."""
        return json.dumps({
            "summary": self.summary(),
            "results": [asdict(r) for r in self.results]
        }, indent=2)

    def to_html(self) -> str:
        """Generate HTML report."""
        summary = self.summary()
        passed_pct = summary["success_rate"]
        status_color = "green" if passed_pct == 100 else "orange" if passed_pct >= 80 else "red"

        html_parts = [
            "<!DOCTYPE html>",
            "<html>",
            "<head>",
            "<meta charset='utf-8'>",
            "<title>Blender Remote GPU Diagnostics Report</title>",
            "<style>",
            "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 2rem; background: #f5f5f5; }",
            ".container { max-width: 900px; margin: 0 auto; background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }",
            "h1 { color: #333; border-bottom: 3px solid #0066cc; padding-bottom: 1rem; }",
            ".summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin: 2rem 0; }",
            ".summary-card { background: #f9f9f9; padding: 1rem; border-radius: 6px; border-left: 4px solid #0066cc; }",
            ".summary-card h3 { margin: 0 0 0.5rem 0; color: #666; font-size: 0.9rem; text-transform: uppercase; }",
            ".summary-card .value { font-size: 2rem; font-weight: bold; color: #333; }",
            ".test { margin: 1.5rem 0; padding: 1rem; border-radius: 6px; border-left: 4px solid; }",
            ".test.pass { background: #f0f9f6; border-left-color: #10b981; }",
            ".test.fail { background: #fef2f2; border-left-color: #ef4444; }",
            ".test-header { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.5rem; }",
            ".test-icon { font-size: 1.5rem; }",
            ".test-title { font-weight: bold; color: #333; flex: 1; }",
            ".test-duration { color: #999; font-size: 0.9rem; }",
            ".test-message { color: #666; margin: 0.5rem 0; }",
            ".test-details { background: rgba(0,0,0,0.02); padding: 1rem; border-radius: 4px; margin-top: 1rem; font-family: monospace; font-size: 0.85rem; overflow-x: auto; }",
            ".status-good { color: #10b981; }",
            ".status-warning { color: #f59e0b; }",
            ".status-bad { color: #ef4444; }",
            "footer { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #ddd; color: #999; font-size: 0.9rem; }",
            "</style>",
            "</head>",
            "<body>",
            "<div class='container'>",
            f"<h1>Blender Remote GPU Diagnostics Report</h1>",
            f"<p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>",
            "<div class='summary'>",
            f"<div class='summary-card'>",
            f"  <h3>Tests Passed</h3>",
            f"  <div class='value'>{summary['passed']}/{summary['total_tests']}</div>",
            f"</div>",
            f"<div class='summary-card'>",
            f"  <h3>Success Rate</h3>",
            f"  <div class='value {status_color}'>{passed_pct:.1f}%</div>",
            f"</div>",
            f"<div class='summary-card'>",
            f"  <h3>Total Duration</h3>",
            f"  <div class='value'>{summary['total_duration']:.2f}s</div>",
            f"</div>",
            "</div>",
        ]

        # Add test results
        html_parts.append("<h2>Test Results</h2>")
        for result in self.results:
            status_class = "pass" if result.passed else "fail"
            icon = "✅" if result.passed else "❌"

            html_parts.append(f"<div class='test {status_class}'>")
            html_parts.append(f"  <div class='test-header'>")
            html_parts.append(f"    <span class='test-icon'>{icon}</span>")
            html_parts.append(f"    <span class='test-title'>{html.escape(result.name)}</span>")
            html_parts.append(f"    <span class='test-duration'>{result.duration:.3f}s</span>")
            html_parts.append(f"  </div>")
            html_parts.append(f"  <p class='test-message'>{html.escape(result.message)}</p>")

            if result.details:
                details_json = json.dumps(result.details, indent=2)
                html_parts.append(f"  <div class='test-details'>{html.escape(details_json)}</div>")

            html_parts.append("</div>")

        html_parts.extend([
            "<footer>",
            "<p>For bug reports, attach the JSON export of these results.</p>",
            f"<p>Blender Remote GPU v1.0.4 | Report generated {datetime.now().isoformat()}</p>",
            "</footer>",
            "</div>",
            "</body>",
            "</html>"
        ])

        return "\n".join(html_parts)


class LatencyHistogram:
    """Tracks network latency statistics."""

    def __init__(self, name: str = "Latency"):
        self.name = name
        self.samples: List[float] = []

    def add(self, latency: float):
        """Record a latency measurement (in seconds)."""
        self.samples.append(latency)

    def stats(self) -> Dict[str, float]:
        """Calculate latency statistics."""
        if not self.samples:
            return {}

        samples_ms = [s * 1000 for s in self.samples]  # Convert to milliseconds
        return {
            "min_ms": min(samples_ms),
            "max_ms": max(samples_ms),
            "mean_ms": statistics.mean(samples_ms),
            "median_ms": statistics.median(samples_ms),
            "stdev_ms": statistics.stdev(samples_ms) if len(samples_ms) > 1 else 0,
            "p95_ms": sorted(samples_ms)[int(len(samples_ms) * 0.95)] if samples_ms else 0,
            "p99_ms": sorted(samples_ms)[int(len(samples_ms) * 0.99)] if samples_ms else 0,
            "samples": len(samples_ms),
        }


class BandwidthTester:
    """Estimates bandwidth by measuring transfer time."""

    @staticmethod
    def estimate_bandwidth(data_size: int, duration: float) -> float:
        """Calculate bandwidth in MB/s."""
        if duration <= 0:
            return 0
        return (data_size / (1024 * 1024)) / duration


class ConnectivityDiagnostics:
    """Main diagnostic suite."""

    def __init__(self, host: str = "100.74.135.83", port: int = 9876, timeout: int = 10):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.reporter = DiagnosticReporter()
        self.latency_histogram = LatencyHistogram("WebSocket Latency")

    def test_tailscale_status(self) -> bool:
        """Check if Tailscale is active."""
        start = time.time()
        try:
            result = subprocess.run(
                ["tailscale", "status"],
                capture_output=True,
                text=True,
                timeout=5
            )

            elapsed = time.time() - start

            if result.returncode != 0:
                self.reporter.add_result(
                    "Tailscale Status",
                    False,
                    elapsed,
                    "Tailscale not running",
                    {"error": "returncode", "code": result.returncode}
                )
                return False

            # Check if Windows machine is active
            if self.host in result.stdout and "active" in result.stdout.lower():
                self.reporter.add_result(
                    "Tailscale Status",
                    True,
                    elapsed,
                    f"Tailscale running, server {self.host} is active",
                    {"output_lines": len(result.stdout.split('\n'))}
                )
                return True
            else:
                self.reporter.add_result(
                    "Tailscale Status",
                    False,
                    elapsed,
                    f"Tailscale running but server {self.host} not found or offline",
                    {}
                )
                return False

        except FileNotFoundError:
            elapsed = time.time() - start
            self.reporter.add_result(
                "Tailscale Status",
                False,
                elapsed,
                "Tailscale CLI not installed",
                {}
            )
            return False
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start
            self.reporter.add_result(
                "Tailscale Status",
                False,
                elapsed,
                "Tailscale status check timed out",
                {}
            )
            return False
        except Exception as e:
            elapsed = time.time() - start
            self.reporter.add_result(
                "Tailscale Status",
                False,
                elapsed,
                f"Error: {str(e)}",
                {"exception": str(e)}
            )
            return False

    def test_tcp_connectivity(self) -> bool:
        """Test raw TCP socket connectivity."""
        start = time.time()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)

            result = sock.connect_ex((self.host, self.port))
            elapsed = time.time() - start

            sock.close()

            if result == 0:
                self.reporter.add_result(
                    f"TCP Connectivity ({self.host}:{self.port})",
                    True,
                    elapsed,
                    f"TCP connection successful",
                    {"latency_ms": round(elapsed * 1000, 2)}
                )
                self.latency_histogram.add(elapsed)
                return True
            else:
                self.reporter.add_result(
                    f"TCP Connectivity ({self.host}:{self.port})",
                    False,
                    elapsed,
                    f"TCP connection failed (code {result})",
                    {"error_code": result}
                )
                return False

        except Exception as e:
            elapsed = time.time() - start
            self.reporter.add_result(
                f"TCP Connectivity ({self.host}:{self.port})",
                False,
                elapsed,
                f"TCP test error: {str(e)}",
                {"exception": str(e)}
            )
            return False

    def test_websocket_handshake(self) -> bool:
        """Test WebSocket protocol handshake."""
        start = time.time()
        try:
            from websockets.sync.client import connect
        except ImportError:
            elapsed = time.time() - start
            self.reporter.add_result(
                "WebSocket Handshake",
                False,
                elapsed,
                "websockets module not installed",
                {"install": "pip install websockets"}
            )
            return False

        try:
            url = f"ws://{self.host}:{self.port}"
            ws = connect(url, open_timeout=self.timeout)
            elapsed = time.time() - start

            # Collect handshake details
            server_header = ws.response.headers.get('server', 'unknown') if hasattr(ws, 'response') else 'unknown'

            self.reporter.add_result(
                "WebSocket Handshake",
                True,
                elapsed,
                f"WebSocket connection successful to {url}",
                {
                    "url": url,
                    "latency_ms": round(elapsed * 1000, 2),
                    "server": server_header,
                    "subprotocol": ws.subprotocol or "none"
                }
            )
            self.latency_histogram.add(elapsed)

            ws.close()
            return True

        except TimeoutError:
            elapsed = time.time() - start
            self.reporter.add_result(
                "WebSocket Handshake",
                False,
                elapsed,
                "WebSocket handshake timeout",
                {
                    "url": f"ws://{self.host}:{self.port}",
                    "possible_causes": [
                        "Server not listening on port",
                        "Firewall blocking port",
                        "Tailscale tunnel not established"
                    ]
                }
            )
            return False

        except Exception as e:
            elapsed = time.time() - start
            self.reporter.add_result(
                "WebSocket Handshake",
                False,
                elapsed,
                f"WebSocket handshake failed: {str(e)}",
                {"exception": str(e)}
            )
            return False

    def test_ping_pong_protocol(self, num_pings: int = 5) -> bool:
        """Test PING/PONG protocol multiple times."""
        try:
            from websockets.sync.client import connect
        except ImportError:
            self.reporter.add_result(
                "PING/PONG Protocol",
                False,
                0,
                "websockets module not available",
                {}
            )
            return False

        try:
            url = f"ws://{self.host}:{self.port}"
            ws = connect(url, open_timeout=self.timeout)

            successful_pings = 0
            failed_pings = 0
            test_start = time.time()

            for i in range(num_pings):
                try:
                    ping_msg = json.dumps({"type": "ping"})
                    ping_time = time.time()
                    ws.send(ping_msg)

                    response = ws.recv(timeout=5)
                    elapsed = time.time() - ping_time

                    pong_data = json.loads(response)
                    if pong_data.get("type") == "pong":
                        successful_pings += 1
                        self.latency_histogram.add(elapsed)
                    else:
                        failed_pings += 1
                except Exception:
                    failed_pings += 1

            total_elapsed = time.time() - test_start
            ws.close()

            passed = failed_pings == 0
            self.reporter.add_result(
                "PING/PONG Protocol",
                passed,
                total_elapsed,
                f"PING/PONG: {successful_pings}/{num_pings} successful",
                {
                    "successful": successful_pings,
                    "failed": failed_pings,
                    "success_rate": successful_pings / num_pings * 100,
                    "latency_stats": self.latency_histogram.stats()
                }
            )

            return passed

        except Exception as e:
            self.reporter.add_result(
                "PING/PONG Protocol",
                False,
                0,
                f"PING/PONG test failed: {str(e)}",
                {"exception": str(e)}
            )
            return False

    def test_timeout_recovery(self) -> bool:
        """Test graceful handling of network timeouts."""
        start = time.time()
        try:
            from websockets.sync.client import connect
        except ImportError:
            elapsed = time.time() - start
            self.reporter.add_result(
                "Timeout Recovery",
                False,
                elapsed,
                "websockets module not available",
                {}
            )
            return False

        try:
            url = f"ws://{self.host}:{self.port}"
            ws = connect(url, open_timeout=self.timeout)

            # Send ping with very short timeout to trigger recovery scenario
            ping_msg = json.dumps({"type": "ping"})
            ws.send(ping_msg)

            try:
                # Short timeout to simulate timeout condition
                response = ws.recv(timeout=0.1)
                # If we get here, connection is healthy
            except TimeoutError:
                # Expected timeout - check if connection can recover
                pass

            # Send another ping to verify recovery
            ws.send(ping_msg)
            response = ws.recv(timeout=5)
            pong_data = json.loads(response)

            elapsed = time.time() - start
            ws.close()

            passed = pong_data.get("type") == "pong"
            self.reporter.add_result(
                "Timeout Recovery",
                passed,
                elapsed,
                "Connection recovered from timeout successfully" if passed else "Connection did not recover",
                {"recovered": passed}
            )

            return passed

        except Exception as e:
            elapsed = time.time() - start
            self.reporter.add_result(
                "Timeout Recovery",
                False,
                elapsed,
                f"Timeout recovery test failed: {str(e)}",
                {"exception": str(e)}
            )
            return False

    def test_connection_flakiness(self, duration: int = 30, interval: float = 2) -> bool:
        """Detect connection instability patterns."""
        try:
            from websockets.sync.client import connect
        except ImportError:
            self.reporter.add_result(
                "Connection Flakiness",
                False,
                0,
                "websockets module not available",
                {}
            )
            return False

        try:
            url = f"ws://{self.host}:{self.port}"
            test_start = time.time()
            ws = connect(url, open_timeout=self.timeout)

            successes = []
            failures = []
            latencies = []

            while time.time() - test_start < duration:
                try:
                    ping_time = time.time()
                    ping_msg = json.dumps({"type": "ping"})
                    ws.send(ping_msg)

                    response = ws.recv(timeout=3)
                    elapsed = time.time() - ping_time

                    pong_data = json.loads(response)
                    if pong_data.get("type") == "pong":
                        successes.append(time.time())
                        latencies.append(elapsed)
                    else:
                        failures.append(time.time())
                except Exception:
                    failures.append(time.time())

                time.sleep(interval)

            total_elapsed = time.time() - test_start
            ws.close()

            # Analyze patterns
            total_attempts = len(successes) + len(failures)
            success_rate = len(successes) / total_attempts * 100 if total_attempts > 0 else 0

            # Calculate jitter (variation in latency)
            jitter = 0
            if latencies and len(latencies) > 1:
                jitter = statistics.stdev(latencies)

            # Detect consecutive failures (flakiness indicator)
            max_consecutive_failures = 0
            current_consecutive = 0
            for i in range(total_attempts):
                if i < len(failures):
                    current_consecutive += 1
                    max_consecutive_failures = max(max_consecutive_failures, current_consecutive)
                else:
                    current_consecutive = 0

            passed = success_rate >= 95
            self.reporter.add_result(
                "Connection Flakiness",
                passed,
                total_elapsed,
                f"Connection stability: {success_rate:.1f}% success rate (flaky)" if not passed else "Connection stable",
                {
                    "test_duration": duration,
                    "successful_pings": len(successes),
                    "failed_pings": len(failures),
                    "success_rate": success_rate,
                    "max_consecutive_failures": max_consecutive_failures,
                    "jitter_ms": round(jitter * 1000, 2) if jitter else 0,
                    "latency_stats": self.latency_histogram.stats()
                }
            )

            return passed

        except Exception as e:
            self.reporter.add_result(
                "Connection Flakiness",
                False,
                0,
                f"Flakiness test failed: {str(e)}",
                {"exception": str(e)}
            )
            return False

    def run_full_suite(self) -> Dict[str, Any]:
        """Run complete diagnostic suite."""
        print("\n╔════════════════════════════════════════════════════════════╗")
        print("║  Blender Remote GPU — Connectivity Diagnostic Suite v2     ║")
        print("╚════════════════════════════════════════════════════════════╝\n")

        print(f"Target: {self.host}:{self.port}")
        print(f"Timeout: {self.timeout}s")
        print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        # Run tests in sequence
        print("[1/6] Testing Tailscale status...")
        tailscale_ok = self.test_tailscale_status()

        print("[2/6] Testing TCP connectivity...")
        tcp_ok = self.test_tcp_connectivity()

        if tcp_ok:
            print("[3/6] Testing WebSocket handshake...")
            ws_ok = self.test_websocket_handshake()

            if ws_ok:
                print("[4/6] Testing PING/PONG protocol...")
                ping_ok = self.test_ping_pong_protocol(num_pings=5)

                print("[5/6] Testing timeout recovery...")
                recovery_ok = self.test_timeout_recovery()

                print("[6/6] Testing connection flakiness...")
                flakiness_ok = self.test_connection_flakiness(duration=30, interval=2)
            else:
                self.reporter.add_result("PING/PONG Protocol", False, 0, "Skipped (WebSocket failed)", {})
                self.reporter.add_result("Timeout Recovery", False, 0, "Skipped (WebSocket failed)", {})
                self.reporter.add_result("Connection Flakiness", False, 0, "Skipped (WebSocket failed)", {})
        else:
            self.reporter.add_result("WebSocket Handshake", False, 0, "Skipped (TCP failed)", {})
            self.reporter.add_result("PING/PONG Protocol", False, 0, "Skipped (TCP failed)", {})
            self.reporter.add_result("Timeout Recovery", False, 0, "Skipped (TCP failed)", {})
            self.reporter.add_result("Connection Flakiness", False, 0, "Skipped (TCP failed)", {})

        return self.reporter.summary()

    def export_json(self, path: str):
        """Export results as JSON."""
        Path(path).write_text(self.reporter.to_json())
        print(f"JSON report saved: {path}")

    def export_html(self, path: str):
        """Export results as HTML."""
        Path(path).write_text(self.reporter.to_html())
        print(f"HTML report saved: {path}")

    def print_summary(self):
        """Print summary to console."""
        summary = self.reporter.summary()
        print(f"\n{'='*60}")
        print("DIAGNOSTICS SUMMARY")
        print(f"{'='*60}")
        print(f"Tests Passed: {summary['passed']}/{summary['total_tests']}")
        print(f"Success Rate: {summary['success_rate']:.1f}%")
        print(f"Total Duration: {summary['total_duration']:.2f}s")

        if summary['success_rate'] == 100:
            print("\n✅ All tests passed! Addon should work correctly.")
        elif summary['success_rate'] >= 80:
            print("\n⚠️  Most tests passed. Check details above for warnings.")
        else:
            print("\n❌ Multiple failures detected. See details for troubleshooting.")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Blender Remote GPU Connectivity Diagnostics"
    )
    parser.add_argument("--host", default="100.74.135.83", help="Server host")
    parser.add_argument("--port", type=int, default=9876, help="Server port")
    parser.add_argument("--timeout", type=int, default=10, help="Test timeout in seconds")
    parser.add_argument("--json", help="Export results as JSON")
    parser.add_argument("--html", help="Export results as HTML")

    args = parser.parse_args()

    diag = ConnectivityDiagnostics(host=args.host, port=args.port, timeout=args.timeout)
    diag.run_full_suite()
    diag.print_summary()

    if args.json:
        diag.export_json(args.json)
    if args.html:
        diag.export_html(args.html)

    # Exit code based on success rate
    summary = diag.reporter.summary()
    sys.exit(0 if summary['success_rate'] == 100 else 1)


if __name__ == "__main__":
    main()
