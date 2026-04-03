"""
Agent USB1: Windows USB Server for Thunderbolt communication with Mac.

Listens for Blender Mac addon connecting via Thunderbolt USB.
Receives render jobs as JSON over USB bulk transfers.
Queues jobs, spawns Blender subprocess, sends results back.

Protocol:
  Mac sends: {"cmd": "render", "scene_path": "...", "width": 640, "height": 360, "samples": 4}
  Server responds: {"status": "queued", "job_id": "abc123"}
  Mac polls: {"cmd": "status", "job_id": "abc123"}
  Server responds: {"status": "running|done|error", "progress": 0.5}
  Mac requests result: {"cmd": "result", "job_id": "abc123"}
  Server sends: binary PNG data + metadata

USB Details:
  - Standard USB bulk transfer (endpoint 0x81 in, 0x01 out)
  - Auto-detects Mac -> Windows Thunderbolt connection
  - 4KB JSON request buffer
  - Binary result streaming
"""

import json
import logging
import threading
import time
import usb.core
import usb.util
import subprocess
import base64
import os
import sys
from typing import Dict, Optional, Any, Tuple
from pathlib import Path
from datetime import datetime

from job_queue import JobQueue

# Setup logging
log_path = Path(os.environ.get("TEMP", "/tmp")) / "usb_server.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("usb_server")

# USB constants
USB_VENDOR_ID = 0x05AC  # Apple (for Thunderbolt device)
USB_PRODUCT_ID = 0x1234  # Placeholder - will detect dynamically
USB_ENDPOINT_OUT = 0x01  # Bulk transfer OUT
USB_ENDPOINT_IN = 0x81   # Bulk transfer IN
USB_TIMEOUT = 1000  # ms
USB_READ_SIZE = 4096  # 4KB request buffer


class USBServer:
    """
    Windows USB server for Thunderbolt communication.
    Manages USB device connection, JSON message protocol, and job dispatch.
    """

    def __init__(self, max_workers: int = 3):
        """Initialize USB server with job queue."""
        self.job_queue = JobQueue(max_workers=max_workers)
        self.device = None
        self.running = False
        self.read_thread = None
        self.worker_threads = []
        self.blender_path = self._find_blender()

        log.info("USB Server initialized")
        log.info(f"Blender path: {self.blender_path}")

    def _find_blender(self) -> Optional[str]:
        """
        Find Blender executable on Windows.

        Returns:
            Path to blender.exe or None if not found
        """
        common_paths = [
            "C:\\Program Files\\Blender Foundation\\Blender\\blender.exe",
            "C:\\Program Files (x86)\\Blender Foundation\\Blender\\blender.exe",
            "C:\\Users\\Public\\Blender\\blender.exe",
        ]

        for path in common_paths:
            if os.path.exists(path):
                log.info(f"Found Blender at {path}")
                return path

        # Try to find in PATH
        result = os.system("where blender.exe > nul 2>&1")
        if result == 0:
            log.info("Found Blender in PATH")
            return "blender.exe"

        log.warning("Blender not found - render jobs will fail")
        return None

    def find_device(self) -> bool:
        """
        Auto-detect Mac -> Windows Thunderbolt USB device.

        Returns:
            True if device found and claimed, False otherwise
        """
        try:
            # Look for standard USB devices
            devices = usb.core.find(find_all=True)

            log.info("Scanning USB devices...")
            for dev in devices:
                try:
                    mfg = usb.util.get_string(dev, dev.iManufacturer)
                    prod = usb.util.get_string(dev, dev.iProduct)
                    log.debug(f"  Device: {mfg} {prod} (VID:PID {dev.idVendor:04x}:{dev.idProduct:04x})")

                    # Match Apple Thunderbolt devices or generic descriptors
                    if "Apple" in str(mfg) or "Blender" in str(prod) or "Remote" in str(prod):
                        log.info(f"Found potential Mac device: {mfg} {prod}")
                        self.device = dev
                        return self._claim_device()
                except Exception as e:
                    log.debug(f"  Error reading device: {e}")
                    continue

            log.warning("No Mac Thunderbolt device found")
            return False

        except Exception as e:
            log.error(f"USB enumeration failed: {e}")
            return False

    def _claim_device(self) -> bool:
        """
        Claim USB device and set configuration.

        Returns:
            True if device claimed successfully
        """
        try:
            if self.device.is_kernel_driver_active(0):
                self.device.detach_kernel_driver(0)
                log.debug("Detached kernel driver")

            self.device.set_configuration()
            log.info(f"Device claimed: {self.device.idVendor:04x}:{self.device.idProduct:04x}")
            return True

        except Exception as e:
            log.error(f"Failed to claim device: {e}")
            return False

    def _read_message(self) -> Optional[Dict[str, Any]]:
        """
        Read one JSON message from USB IN endpoint.

        Returns:
            Parsed JSON dict or None on error/timeout
        """
        try:
            if not self.device:
                return None

            data = self.device.read(USB_ENDPOINT_IN, USB_READ_SIZE, timeout=USB_TIMEOUT)
            if not data:
                return None

            msg_bytes = bytes(data).rstrip(b'\x00')  # Remove null padding
            msg_str = msg_bytes.decode('utf-8', errors='ignore')
            msg = json.loads(msg_str)

            log.debug(f"Received: {msg}")
            return msg

        except usb.core.USBTimeoutError:
            # Normal timeout - no data available
            return None
        except Exception as e:
            log.error(f"USB read error: {e}")
            return None

    def _write_message(self, msg: Dict[str, Any]) -> bool:
        """
        Write one JSON message to USB OUT endpoint.

        Args:
            msg: Dict to serialize and send

        Returns:
            True if written successfully
        """
        try:
            if not self.device:
                return False

            msg_bytes = json.dumps(msg).encode('utf-8')
            # Pad to 4KB boundary
            msg_bytes = msg_bytes + b'\x00' * (USB_READ_SIZE - len(msg_bytes))

            self.device.write(USB_ENDPOINT_OUT, msg_bytes, timeout=USB_TIMEOUT)
            log.debug(f"Sent: {msg}")
            return True

        except Exception as e:
            log.error(f"USB write error: {e}")
            return False

    def _handle_message(self, msg: Dict[str, Any]) -> None:
        """
        Dispatch incoming message to appropriate handler.

        Args:
            msg: Incoming JSON message dict
        """
        try:
            cmd = msg.get("cmd")

            if cmd == "render":
                self._handle_render(msg)
            elif cmd == "status":
                self._handle_status(msg)
            elif cmd == "result":
                self._handle_result(msg)
            else:
                log.warning(f"Unknown command: {cmd}")
                self._write_message({"status": "error", "message": f"Unknown command: {cmd}"})

        except Exception as e:
            log.error(f"Error handling message: {e}")

    def _handle_render(self, msg: Dict[str, Any]) -> None:
        """
        Handle render request: submit to job queue and respond with job_id.

        Message format:
          {"cmd": "render", "scene_path": "...", "width": 640, "height": 360, "samples": 4}

        Response:
          {"status": "queued", "job_id": "abc123"}
        """
        scene_path = msg.get("scene_path")
        width = msg.get("width", 640)
        height = msg.get("height", 360)
        samples = msg.get("samples", 4)

        params = {
            "scene_path": scene_path,
            "width": width,
            "height": height,
            "samples": samples,
        }

        job_id = self.job_queue.submit_job("render", params)
        log.info(f"Render job submitted: {job_id} ({width}x{height}, {samples}spp)")

        self._write_message({"status": "queued", "job_id": job_id})

    def _handle_status(self, msg: Dict[str, Any]) -> None:
        """
        Handle status request: return current job status and progress.

        Message format:
          {"cmd": "status", "job_id": "abc123"}

        Response:
          {"status": "queued|running|done|error", "progress": 0.5, "job_id": "abc123"}
        """
        job_id = msg.get("job_id")
        if not job_id:
            self._write_message({"status": "error", "message": "Missing job_id"})
            return

        status = self.job_queue.get_status(job_id)
        log.debug(f"Status query: {job_id} -> {status}")

        response = {
            "status": status,
            "job_id": job_id,
            "progress": 0.0,  # TODO: track progress per job
        }
        self._write_message(response)

    def _handle_result(self, msg: Dict[str, Any]) -> None:
        """
        Handle result request: send rendered PNG (binary) or error message.

        Message format:
          {"cmd": "result", "job_id": "abc123"}

        Response (binary):
          First 4 bytes: length of JSON metadata
          Next N bytes: JSON metadata ({"width": 640, "height": 360, ...})
          Remaining bytes: PNG image data
        """
        job_id = msg.get("job_id")
        if not job_id:
            self._write_message({"status": "error", "message": "Missing job_id"})
            return

        result = self.job_queue.get_result(job_id)
        if not result:
            log.debug(f"Result not found: {job_id}")
            self._write_message({"status": "not_found", "job_id": job_id})
            return

        # Extract PNG data from result
        if "type" in result and result["type"] == "error":
            self._write_message({"status": "error", "message": result.get("message", "Unknown error")})
            return

        # Build binary response: metadata + PNG
        metadata = {
            "job_id": job_id,
            "width": result.get("width"),
            "height": result.get("height"),
            "timestamp": result.get("timestamp"),
        }
        metadata_bytes = json.dumps(metadata).encode('utf-8')
        metadata_len = len(metadata_bytes).to_bytes(4, byteorder='little')

        png_data = result.get("png_data", b"")
        if isinstance(png_data, str):
            png_data = base64.b64decode(png_data)

        response = metadata_len + metadata_bytes + png_data

        try:
            # Send binary response (may be larger than 4KB, so chunk it)
            chunk_size = USB_READ_SIZE
            for i in range(0, len(response), chunk_size):
                chunk = response[i:i+chunk_size]
                # Pad to 4KB
                chunk = chunk + b'\x00' * (USB_READ_SIZE - len(chunk))
                self.device.write(USB_ENDPOINT_OUT, chunk, timeout=USB_TIMEOUT)

            log.info(f"Result sent: {job_id} ({len(png_data)} bytes)")

        except Exception as e:
            log.error(f"Failed to send result: {e}")

    def _worker_loop(self) -> None:
        """
        Worker thread: dequeue jobs, spawn Blender, send results.
        """
        while self.running:
            job = self.job_queue.get_next_job()
            if not job:
                time.sleep(0.5)
                continue

            job_id, job_type, params = job

            if job_type == "render":
                self._execute_render(job_id, params)
            else:
                log.warning(f"Unknown job type: {job_type}")
                self.job_queue.set_error(job_id, f"Unknown job type: {job_type}")

    def _execute_render(self, job_id: str, params: Dict[str, Any]) -> None:
        """
        Execute render job by spawning Blender subprocess.

        Args:
            job_id: Job identifier
            params: Render parameters (scene_path, width, height, samples)
        """
        if not self.blender_path:
            self.job_queue.set_error(job_id, "Blender not found")
            return

        scene_path = params.get("scene_path")
        width = params.get("width", 640)
        height = params.get("height", 360)
        samples = params.get("samples", 4)

        log.info(f"Executing render: {job_id}")

        try:
            # Build Blender command
            # --python-expr: render.py will be passed as script
            output_path = Path(os.environ.get("TEMP", "/tmp")) / f"render_{job_id}.png"

            cmd = [
                self.blender_path,
                "--background",
                scene_path,
                "--python-expr",
                f"import bpy; bpy.context.scene.render.resolution_x = {width}; "
                f"bpy.context.scene.render.resolution_y = {height}; "
                f"bpy.context.scene.cycles.samples = {samples}; "
                f"bpy.ops.render.render(write_still=True); "
                f"bpy.data.images['Render Result'].save_render('{output_path}')",
            ]

            log.debug(f"Blender command: {' '.join(cmd)}")

            # Spawn Blender subprocess (isolated)
            result = subprocess.run(
                cmd,
                timeout=300,  # 5 minute timeout
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                error = f"Blender failed: {result.stderr}"
                log.error(error)
                self.job_queue.set_error(job_id, error)
                return

            # Read rendered PNG
            if not output_path.exists():
                error = f"Render output not found: {output_path}"
                log.error(error)
                self.job_queue.set_error(job_id, error)
                return

            with open(output_path, "rb") as f:
                png_data = f.read()

            # Store result
            result_dict = {
                "job_id": job_id,
                "width": width,
                "height": height,
                "png_data": base64.b64encode(png_data).decode('utf-8'),
                "timestamp": datetime.now().isoformat(),
            }

            self.job_queue.set_result(job_id, result_dict)
            log.info(f"Render complete: {job_id} ({len(png_data)} bytes)")

            # Cleanup
            output_path.unlink()

        except subprocess.TimeoutExpired:
            error = f"Blender timeout (render took >5min)"
            log.error(error)
            self.job_queue.set_error(job_id, error)
        except Exception as e:
            error = f"Render execution failed: {e}"
            log.error(error)
            self.job_queue.set_error(job_id, error)

    def _read_loop(self) -> None:
        """
        Main read loop: continuously read USB messages and dispatch.
        """
        log.info("USB read loop started")

        while self.running:
            msg = self._read_message()
            if msg:
                self._handle_message(msg)
            else:
                time.sleep(0.1)  # Brief sleep to avoid busy-wait

        log.info("USB read loop stopped")

    def start(self) -> bool:
        """
        Start USB server: find device, start read loop, start worker threads.

        Returns:
            True if successfully started
        """
        if not self.find_device():
            log.error("Failed to find USB device")
            return False

        self.running = True

        # Start read thread
        self.read_thread = threading.Thread(target=self._read_loop, daemon=False)
        self.read_thread.start()
        log.info("Read thread started")

        # Start worker threads
        for i in range(self.job_queue.max_workers):
            worker = threading.Thread(target=self._worker_loop, daemon=False, name=f"Worker-{i}")
            worker.start()
            self.worker_threads.append(worker)

        log.info(f"Started {len(self.worker_threads)} worker threads")
        log.info("USB Server ready")

        return True

    def stop(self) -> None:
        """
        Stop USB server: shutdown threads, close device.
        """
        log.info("Stopping USB server...")

        self.running = False

        # Wait for threads to finish
        if self.read_thread:
            self.read_thread.join(timeout=2.0)

        for worker in self.worker_threads:
            worker.join(timeout=2.0)

        # Release device
        if self.device:
            try:
                usb.util.release_interface(self.device, 0)
                usb.util.dispose_resources(self.device)
                log.info("USB device released")
            except Exception as e:
                log.warning(f"Error releasing device: {e}")

        log.info("USB Server stopped")

    def status(self) -> Dict[str, Any]:
        """
        Get server health status.

        Returns:
            Dict with device status, queue status, etc.
        """
        return {
            "running": self.running,
            "device_connected": self.device is not None,
            "blender_available": self.blender_path is not None,
            "queue": self.job_queue.get_queue_status(),
        }


def main():
    """Main entry point."""
    log.info("=" * 60)
    log.info("Agent USB1: Windows USB Server for Thunderbolt")
    log.info("=" * 60)

    server = USBServer(max_workers=3)

    try:
        if not server.start():
            log.error("Failed to start server")
            return 1

        # Keep running
        while True:
            time.sleep(1)

            # Log status periodically
            status = server.status()
            if status["device_connected"]:
                queue_status = status["queue"]
                if queue_status["total_submitted"] > 0:
                    log.debug(f"Queue: {queue_status}")

    except KeyboardInterrupt:
        log.info("Received interrupt signal")
    except Exception as e:
        log.error(f"Unexpected error: {e}", exc_info=True)
        return 1
    finally:
        server.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
