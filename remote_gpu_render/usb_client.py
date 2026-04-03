"""USB client for Blender addon (Mac side) — Thunderbolt USB bridge to Windows.

Replaces HTTP JobDispatcherClient with pyusb bulk transfers.
USB protocol: 4KB JSON payloads via bulk endpoints (0x01 out, 0x81 in).

Same job workflow as dispatcher:
1. submit_render_job() → {"status": "queued", "job_id": "uuid"}
2. get_job_status(job_id) → {"status": "running|done|error", "progress": 0.5}
3. get_job_result(job_id) → PNG binary data (or metadata + PNG chunks)
"""

import json
import time
import logging
import usb.core
import usb.util

# USB Protocol
USB_VENDOR_ID = 0x1234  # Must match Windows server
USB_PRODUCT_ID = 0x5678  # Must match Windows server
USB_BULK_OUT = 0x01  # Host → Device
USB_BULK_IN = 0x81   # Device → Host
USB_TIMEOUT = 5000   # milliseconds
USB_CHUNK_SIZE = 4096

# Logging
LOG_FILE = "/tmp/usb_client.log"
logger = logging.getLogger("USBClient")
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(LOG_FILE)
handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(handler)


class USBClient:
    """USB bulk transfer client for remote GPU render dispatcher."""

    def __init__(self):
        self.device = None
        self.connected = False
        self.latency_ms = 0
        self.error = ""

    def connect(self):
        """Find and connect to Windows Blender via Thunderbolt USB.

        Sets self.connected = True on success, self.error on failure.
        """
        self.error = ""
        self.connected = False

        try:
            logger.info("Scanning for USB device (vendor=0x%04x, product=0x%04x)",
                       USB_VENDOR_ID, USB_PRODUCT_ID)

            # Find device
            device = usb.core.find(find_all=False,
                                   idVendor=USB_VENDOR_ID,
                                   idProduct=USB_PRODUCT_ID)

            if device is None:
                self.error = "Device not found on Thunderbolt USB"
                logger.error(self.error)
                return

            # Set active configuration
            device.set_configuration()

            # Get interface (usually 0)
            cfg = device.get_active_configuration()
            intf = cfg[(0, 0)]

            # Verify endpoints exist
            out_ep = usb.util.find_descriptor(intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
            in_ep = usb.util.find_descriptor(intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)

            if out_ep is None or in_ep is None:
                self.error = "USB endpoints not found on device"
                logger.error(self.error)
                return

            self.device = device
            self.connected = True
            logger.info("Connected to USB device via Thunderbolt")

        except usb.core.USBError as e:
            self.error = f"USB error: {e}"
            logger.error(self.error)
        except Exception as e:
            self.error = f"Connection failed: {e}"
            logger.error(self.error)

    def disconnect(self):
        """Close USB connection."""
        if self.device:
            try:
                usb.util.dispose_resources(self.device)
            except Exception:
                pass
        self.device = None
        self.connected = False

    def _send_command(self, cmd_dict, timeout=USB_TIMEOUT):
        """Send JSON command via bulk out, read JSON response via bulk in.

        Args:
            cmd_dict: Command dict to send (e.g., {"cmd": "render", ...})
            timeout: Milliseconds to wait for response

        Returns:
            Response dict, or None on error
        """
        if not self.connected or not self.device:
            self.error = "Not connected to USB device"
            logger.error(self.error)
            return None

        try:
            # Serialize command to JSON
            cmd_json = json.dumps(cmd_dict).encode("utf-8")
            if len(cmd_json) > USB_CHUNK_SIZE - 100:  # Leave room for framing
                self.error = f"Command too large: {len(cmd_json)} bytes"
                logger.error(self.error)
                return None

            logger.debug("Sending command: %s", cmd_dict)

            # Send command
            start = time.time()
            cfg = self.device.get_active_configuration()
            intf = cfg[(0, 0)]
            out_ep = usb.util.find_descriptor(intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)

            out_ep.write(cmd_json, timeout=timeout)

            # Read response
            in_ep = usb.util.find_descriptor(intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)

            response_bytes = bytes(in_ep.read(USB_CHUNK_SIZE, timeout=timeout))
            self.latency_ms = int((time.time() - start) * 1000)

            # Parse response JSON
            response_str = response_bytes.decode("utf-8").rstrip('\x00')
            response = json.loads(response_str)

            logger.debug("Received response: %s (latency: %dms)", response, self.latency_ms)
            self.error = ""
            return response

        except usb.core.USBError as e:
            self.error = f"USB transfer error: {e}"
            logger.error(self.error)
            self.connected = False
            return None
        except json.JSONDecodeError as e:
            self.error = f"Invalid JSON in response: {e}"
            logger.error(self.error)
            return None
        except Exception as e:
            self.error = f"Command send failed: {e}"
            logger.error(self.error)
            return None

    def ping(self):
        """Test connectivity to Windows device.

        Returns:
            True if device responds, False otherwise
        """
        result = self._send_command({"cmd": "ping"}, timeout=5000)
        return result is not None and result.get("status") == "ok"

    def submit_render_job(self, scene_path, width, height, samples):
        """Submit a render job to the Windows dispatcher.

        Args:
            scene_path: Full path to .blend file on Windows dispatcher machine
            width: Output image width
            height: Output image height
            samples: Cycles samples

        Returns:
            {"job_id": "uuid", "status": "queued"} or None on error
        """
        cmd = {
            "cmd": "render",
            "scene_path": scene_path,
            "width": width,
            "height": height,
            "samples": samples,
        }
        result = self._send_command(cmd, timeout=30000)

        if result and result.get("status") == "queued":
            logger.info("Render job submitted: %s", result.get("job_id"))
            return result

        self.error = f"Failed to submit render job: {result}"
        logger.error(self.error)
        return None

    def get_job_status(self, job_id):
        """Poll render job status.

        Args:
            job_id: Job ID from submit_render_job()

        Returns:
            {
              "status": "queued|running|done|error",
              "progress": 0.5,  # 0.0 to 1.0
              "message": "...",  # optional
              "error": "..."  # if status == "error"
            }
        """
        cmd = {"cmd": "status", "job_id": job_id}
        result = self._send_command(cmd, timeout=5000)

        if result:
            logger.debug("Status for %s: %s (progress: %s)",
                        job_id[:8], result.get("status"), result.get("progress"))
            return result

        logger.error("Failed to get status for job %s", job_id[:8])
        return None

    def get_job_result(self, job_id):
        """Fetch completed job result (PNG binary data).

        For binary PNG results, the protocol uses:
        1. Send {"cmd": "result", "job_id": "..."} via bulk out
        2. Read metadata JSON (size, format) via bulk in (4KB chunk)
        3. Read PNG binary data in chunks via bulk in

        Args:
            job_id: Job ID from submit_render_job()

        Returns:
            PNG binary data (bytes), or None on error
        """
        cmd = {"cmd": "result", "job_id": job_id}

        try:
            logger.debug("Requesting result for job %s", job_id[:8])

            # Send result request
            cfg = self.device.get_active_configuration()
            intf = cfg[(0, 0)]
            out_ep = usb.util.find_descriptor(intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
            in_ep = usb.util.find_descriptor(intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)

            cmd_json = json.dumps(cmd).encode("utf-8")
            out_ep.write(cmd_json, timeout=30000)

            # Read metadata (first 4KB chunk)
            metadata_bytes = bytes(in_ep.read(USB_CHUNK_SIZE, timeout=30000))
            metadata_str = metadata_bytes.decode("utf-8").rstrip('\x00')
            metadata = json.loads(metadata_str)

            if metadata.get("status") == "error":
                self.error = metadata.get("error", "Unknown error")
                logger.error("Result error: %s", self.error)
                return None

            if metadata.get("status") != "success":
                self.error = f"Unexpected result status: {metadata.get('status')}"
                logger.error(self.error)
                return None

            # Metadata contains file size and format
            file_size = metadata.get("file_size", 0)
            file_format = metadata.get("format", "png")

            if file_size <= 0:
                self.error = "Invalid file size in metadata"
                logger.error(self.error)
                return None

            logger.debug("Fetching %d-byte %s result", file_size, file_format)

            # Read PNG binary data in chunks
            png_data = bytearray()
            remaining = file_size

            while remaining > 0:
                chunk_size = min(USB_CHUNK_SIZE, remaining)
                chunk = bytes(in_ep.read(chunk_size, timeout=30000))

                if not chunk:
                    self.error = "Unexpected end of data from device"
                    logger.error(self.error)
                    return None

                png_data.extend(chunk)
                remaining -= len(chunk)
                logger.debug("Read %d bytes, %d remaining", len(chunk), remaining)

            logger.info("Result fetched: %d bytes", len(png_data))
            self.error = ""
            return bytes(png_data)

        except usb.core.USBError as e:
            self.error = f"USB transfer error during result fetch: {e}"
            logger.error(self.error)
            self.connected = False
            return None
        except json.JSONDecodeError as e:
            self.error = f"Invalid JSON in result metadata: {e}"
            logger.error(self.error)
            return None
        except Exception as e:
            self.error = f"Result fetch failed: {e}"
            logger.error(self.error)
            return None
