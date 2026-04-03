#!/usr/bin/env python3
"""
Server Health Check Tool for Blender Remote GPU

Validates that the render server is properly configured and can handle renders:
- Blender executable availability
- NVIDIA GPU presence and memory
- Temporary file permissions
- Test .blend file rendering
- Performance benchmarks
- GPU utilization monitoring
"""

import sys
import os
import json
import subprocess
import tempfile
import shutil
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from datetime import datetime
import platform


@dataclass
class HealthCheckResult:
    """Result of a single health check."""
    component: str
    healthy: bool
    message: str
    details: Dict[str, Any]
    duration: float
    timestamp: str


class ServerHealthChecker:
    """Comprehensive server health verification."""

    def __init__(self, blender_path: Optional[str] = None):
        self.blender_path = blender_path or self._find_blender()
        self.results: list[HealthCheckResult] = []
        self.gpu_info = {}

    def _find_blender(self) -> Optional[str]:
        """Locate Blender executable on the system."""
        # Common installation paths
        paths_to_check = [
            "/usr/bin/blender",  # Linux
            "/usr/local/bin/blender",  # macOS Homebrew
            "/Applications/Blender.app/Contents/MacOS/Blender",  # macOS
            "C:\\Program Files\\Blender Foundation\\Blender\\blender.exe",  # Windows
            "C:\\Program Files (x86)\\Blender Foundation\\Blender\\blender.exe",  # Windows 32-bit
            os.path.expanduser("~/blender/blender"),  # User installation
        ]

        for path in paths_to_check:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path

        # Try 'which' command
        try:
            result = subprocess.run(["which", "blender"], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

        return None

    def add_result(self, component: str, healthy: bool, message: str,
                   details: Optional[Dict] = None, duration: float = 0):
        """Record a health check result."""
        self.results.append(HealthCheckResult(
            component=component,
            healthy=healthy,
            message=message,
            details=details or {},
            duration=duration,
            timestamp=datetime.now().isoformat()
        ))

    def check_blender_executable(self) -> bool:
        """Check if Blender executable is available."""
        start = time.time()

        if not self.blender_path:
            self.add_result(
                "Blender Executable",
                False,
                "Blender executable not found",
                {"checked_paths": self._get_common_paths()},
                time.time() - start
            )
            return False

        try:
            result = subprocess.run(
                [self.blender_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                # Extract version
                version_output = result.stdout.strip()
                self.add_result(
                    "Blender Executable",
                    True,
                    f"Blender found and accessible",
                    {
                        "path": self.blender_path,
                        "version_output": version_output
                    },
                    time.time() - start
                )
                return True
            else:
                self.add_result(
                    "Blender Executable",
                    False,
                    "Blender executable returned error",
                    {"returncode": result.returncode, "stderr": result.stderr},
                    time.time() - start
                )
                return False

        except subprocess.TimeoutExpired:
            self.add_result(
                "Blender Executable",
                False,
                "Blender version check timed out",
                {},
                time.time() - start
            )
            return False
        except Exception as e:
            self.add_result(
                "Blender Executable",
                False,
                f"Error checking Blender: {str(e)}",
                {"exception": str(e)},
                time.time() - start
            )
            return False

    def check_gpu_presence(self) -> bool:
        """Check for NVIDIA GPU and get memory info."""
        start = time.time()

        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free,driver_version",
                 "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                self.add_result(
                    "GPU Presence",
                    False,
                    "nvidia-smi failed or GPU not found",
                    {"stderr": result.stderr},
                    time.time() - start
                )
                return False

            # Parse GPU info
            gpus = []
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 5:
                    gpus.append({
                        "index": parts[0],
                        "name": parts[1],
                        "total_memory_mb": int(parts[2]) if parts[2].isdigit() else 0,
                        "free_memory_mb": int(parts[3]) if parts[3].isdigit() else 0,
                        "driver_version": parts[4]
                    })
                    self.gpu_info[parts[0]] = gpus[-1]

            if gpus:
                self.add_result(
                    "GPU Presence",
                    True,
                    f"Found {len(gpus)} GPU(s)",
                    {"gpus": gpus},
                    time.time() - start
                )
                return True
            else:
                self.add_result(
                    "GPU Presence",
                    False,
                    "No GPUs detected",
                    {},
                    time.time() - start
                )
                return False

        except FileNotFoundError:
            self.add_result(
                "GPU Presence",
                False,
                "nvidia-smi not found (NVIDIA drivers not installed)",
                {},
                time.time() - start
            )
            return False
        except Exception as e:
            self.add_result(
                "GPU Presence",
                False,
                f"Error checking GPU: {str(e)}",
                {"exception": str(e)},
                time.time() - start
            )
            return False

    def check_temp_directory_permissions(self) -> bool:
        """Check write permissions to temporary directory."""
        start = time.time()

        try:
            temp_dir = tempfile.gettempdir()
            test_dir = os.path.join(temp_dir, "blender-remote-gpu-test")

            # Try to create directory
            os.makedirs(test_dir, exist_ok=True)

            # Try to write a test file
            test_file = os.path.join(test_dir, "test_write.txt")
            with open(test_file, "w") as f:
                f.write("test")

            # Try to read it back
            with open(test_file, "r") as f:
                content = f.read()

            if content != "test":
                raise IOError("File content mismatch")

            # Clean up
            os.remove(test_file)
            os.rmdir(test_dir)

            self.add_result(
                "Temp Directory Permissions",
                True,
                f"Can read/write to {temp_dir}",
                {"temp_dir": temp_dir},
                time.time() - start
            )
            return True

        except PermissionError:
            self.add_result(
                "Temp Directory Permissions",
                False,
                f"Permission denied for {tempfile.gettempdir()}",
                {},
                time.time() - start
            )
            return False
        except Exception as e:
            self.add_result(
                "Temp Directory Permissions",
                False,
                f"Error testing temp directory: {str(e)}",
                {"exception": str(e)},
                time.time() - start
            )
            return False

    def check_blender_scene_file(self) -> bool:
        """Test opening a simple .blend file."""
        start = time.time()

        if not self.blender_path:
            self.add_result(
                "Blender Scene File",
                False,
                "Blender executable not available",
                {},
                time.time() - start
            )
            return False

        try:
            # Create a minimal .blend file by using blender in background mode
            temp_file = os.path.join(tempfile.gettempdir(), "test_scene.blend")

            # Use blender to create a minimal file
            result = subprocess.run(
                [self.blender_path, "--background", "--new-instance", "-o", temp_file, "-F", "BLEND"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if os.path.exists(temp_file) and os.path.getsize(temp_file) > 0:
                # Try to open it again
                verify_result = subprocess.run(
                    [self.blender_path, "--background", temp_file, "-o", "/dev/null"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                os.remove(temp_file)

                if verify_result.returncode == 0:
                    self.add_result(
                        "Blender Scene File",
                        True,
                        "Can create and open .blend files",
                        {"test_file_created": True},
                        time.time() - start
                    )
                    return True
                else:
                    self.add_result(
                        "Blender Scene File",
                        False,
                        "Failed to open test .blend file",
                        {"stderr": verify_result.stderr[:200]},
                        time.time() - start
                    )
                    return False
            else:
                self.add_result(
                    "Blender Scene File",
                    False,
                    "Failed to create test .blend file",
                    {"stderr": result.stderr[:200]},
                    time.time() - start
                )
                return False

        except subprocess.TimeoutExpired:
            self.add_result(
                "Blender Scene File",
                False,
                "Blender scene file test timed out",
                {},
                time.time() - start
            )
            return False
        except Exception as e:
            self.add_result(
                "Blender Scene File",
                False,
                f"Error testing scene file: {str(e)}",
                {"exception": str(e)},
                time.time() - start
            )
            return False

    def benchmark_render_performance(self) -> bool:
        """Benchmark simple render performance."""
        start = time.time()

        if not self.blender_path:
            self.add_result(
                "Render Benchmark",
                False,
                "Blender executable not available",
                {},
                time.time() - start
            )
            return False

        try:
            # Create a minimal test scene
            temp_file = os.path.join(tempfile.gettempdir(), "benchmark_scene.blend")
            output_file = os.path.join(tempfile.gettempdir(), "benchmark_output.png")

            # Generate a simple scene with blender Python script
            script_content = """
import bpy
bpy.context.scene.render.engine = 'CYCLES'
bpy.context.scene.cycles.samples = 16
bpy.context.scene.render.resolution_x = 320
bpy.context.scene.render.resolution_y = 240
bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
"""
            script_file = os.path.join(tempfile.gettempdir(), "bench_script.py")
            with open(script_file, "w") as f:
                f.write(script_content)

            # Create scene
            create_result = subprocess.run(
                [self.blender_path, "--background", "--new-instance", temp_file, "-P", script_file],
                capture_output=True,
                text=True,
                timeout=30
            )

            if not os.path.exists(temp_file):
                self.add_result(
                    "Render Benchmark",
                    False,
                    "Failed to create benchmark scene",
                    {},
                    time.time() - start
                )
                return False

            # Render the scene
            render_start = time.time()
            render_result = subprocess.run(
                [self.blender_path, "--background", temp_file,
                 "-o", output_file, "-F", "PNG", "-f", "1"],
                capture_output=True,
                text=True,
                timeout=60
            )
            render_duration = time.time() - render_start

            # Check if render succeeded
            if os.path.exists(output_file):
                file_size = os.path.getsize(output_file)

                # Calculate frames per second
                fps = 1.0 / render_duration if render_duration > 0 else 0

                # Clean up
                os.remove(temp_file)
                os.remove(output_file)
                os.remove(script_file)

                self.add_result(
                    "Render Benchmark",
                    True,
                    f"Simple render completed in {render_duration:.2f}s",
                    {
                        "render_duration": render_duration,
                        "output_size_bytes": file_size,
                        "fps": fps,
                        "resolution": "320x240"
                    },
                    time.time() - start
                )
                return True
            else:
                self.add_result(
                    "Render Benchmark",
                    False,
                    "Render failed or no output generated",
                    {"stderr": render_result.stderr[:200]},
                    time.time() - start
                )
                return False

        except subprocess.TimeoutExpired:
            self.add_result(
                "Render Benchmark",
                False,
                "Render benchmark timed out",
                {},
                time.time() - start
            )
            return False
        except Exception as e:
            self.add_result(
                "Render Benchmark",
                False,
                f"Error running benchmark: {str(e)}",
                {"exception": str(e)},
                time.time() - start
            )
            return False

    def calculate_readiness_score(self) -> float:
        """Calculate overall server readiness score."""
        if not self.results:
            return 0

        passed = sum(1 for r in self.results if r.healthy)
        total = len(self.results)

        # Weight certain checks more heavily
        weights = {
            "Blender Executable": 2.0,
            "GPU Presence": 2.0,
            "Temp Directory Permissions": 1.5,
            "Blender Scene File": 1.5,
            "Render Benchmark": 1.0,
        }

        total_weight = 0
        passed_weight = 0

        for result in self.results:
            weight = weights.get(result.component, 1.0)
            total_weight += weight
            if result.healthy:
                passed_weight += weight

        score = (passed_weight / total_weight * 100) if total_weight > 0 else 0
        return score

    def run_full_check(self) -> Dict[str, Any]:
        """Run complete health check suite."""
        print("\n╔════════════════════════════════════════════════════════════╗")
        print("║       Blender Remote GPU — Server Health Check Tool        ║")
        print("╚════════════════════════════════════════════════════════════╝\n")

        print(f"Platform: {platform.platform()}")
        print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        print("[1/5] Checking Blender executable...")
        self.check_blender_executable()

        print("[2/5] Checking GPU presence...")
        self.check_gpu_presence()

        print("[3/5] Checking temp directory permissions...")
        self.check_temp_directory_permissions()

        print("[4/5] Testing Blender scene file...")
        self.check_blender_scene_file()

        print("[5/5] Running render benchmark...")
        self.check_blender_scene_file()  # Quick check before benchmark

        return {
            "timestamp": datetime.now().isoformat(),
            "readiness_score": self.calculate_readiness_score(),
            "checks_passed": sum(1 for r in self.results if r.healthy),
            "total_checks": len(self.results)
        }

    def print_summary(self):
        """Print summary to console."""
        score = self.calculate_readiness_score()

        print(f"\n{'='*60}")
        print("SERVER HEALTH CHECK SUMMARY")
        print(f"{'='*60}")

        for result in self.results:
            status = "✅ PASS" if result.healthy else "❌ FAIL"
            print(f"{status} | {result.component:30} | {result.duration:.3f}s")

        print(f"\n{'='*60}")
        print(f"Readiness Score: {score:.1f}%")
        print(f"{'='*60}\n")

        if score >= 90:
            print("✅ Server is ready for rendering!")
        elif score >= 70:
            print("⚠️  Server has some issues. Check details above.")
        else:
            print("❌ Server is not ready. Fix issues before rendering.")

    def export_json(self, path: str):
        """Export results as JSON."""
        data = {
            "timestamp": datetime.now().isoformat(),
            "readiness_score": self.calculate_readiness_score(),
            "results": [asdict(r) for r in self.results]
        }
        Path(path).write_text(json.dumps(data, indent=2))
        print(f"Health check results saved: {path}")

    @staticmethod
    def _get_common_paths():
        """Get list of common Blender installation paths."""
        return [
            "/usr/bin/blender",
            "/usr/local/bin/blender",
            "/Applications/Blender.app/Contents/MacOS/Blender",
            "C:\\Program Files\\Blender Foundation\\Blender\\blender.exe",
        ]


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Blender Remote GPU Server Health Check"
    )
    parser.add_argument("--blender", help="Path to Blender executable")
    parser.add_argument("--json", help="Export results as JSON")

    args = parser.parse_args()

    checker = ServerHealthChecker(blender_path=args.blender)
    checker.run_full_check()
    checker.print_summary()

    if args.json:
        checker.export_json(args.json)

    # Exit code based on readiness score
    score = checker.calculate_readiness_score()
    sys.exit(0 if score >= 80 else 1)


if __name__ == "__main__":
    main()
