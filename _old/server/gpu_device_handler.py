"""GPU device selection handler for the render server.

Manages GPU backend queries and device selection via protocol messages.
"""

import logging
from typing import Optional, Dict, Any

from shared.protocol import MsgType, pack_websocket

logger = logging.getLogger("remote-gpu.gpu_device_handler")


class GPUDeviceHandler:
    """Manages GPU backend info and device selection for clients."""

    def __init__(self, gpu_backend_mgr=None):
        """Initialize handler with optional GPU backend manager.

        Args:
            gpu_backend_mgr: GPUBackendManager instance or None.
        """
        self.gpu_backend_mgr = gpu_backend_mgr

    def get_status_data(self) -> Dict[str, Any]:
        """Get GPU backend status for STATUS message payload.

        Returns dict with available_backends, current_backend, device info.
        """
        if not self.gpu_backend_mgr:
            return {
                "available_backends": [],
                "current_backend": "NONE",
                "backend_devices": [],
            }

        # Get available backends
        available = self.gpu_backend_mgr.detect_available_backends()
        available_names = [name for name, version in available]

        # Get current backend name
        current_backend = self.gpu_backend_mgr.get_current_backend_name()

        # Get devices for current backend
        backend_devices = []
        if current_backend != "NONE":
            backend_info = self.gpu_backend_mgr.get_backend_info(current_backend)
            if backend_info and "devices" in backend_info:
                backend_devices = backend_info["devices"]

        return {
            "available_backends": available_names,
            "current_backend": current_backend,
            "backend_devices": backend_devices,
        }

    async def handle_device_select(self, websocket, data: Dict[str, Any], session_id: str):
        """Handle RENDER_DEVICE_SELECT message from client.

        Expected data: {"backend": "OPTIX", "device_idx": 0}

        Args:
            websocket: WebSocket connection to client.
            data: Message payload with backend and device_idx.
            session_id: Session ID for logging.
        """
        if not self.gpu_backend_mgr:
            logger.warning(f"[{session_id}] Device select requested but GPU backend manager not available")
            return

        backend_name = data.get("backend", "").upper()
        device_idx = data.get("device_idx", 0)

        if not backend_name:
            logger.warning(f"[{session_id}] Device select: backend name not provided")
            return

        logger.info(f"[{session_id}] Client requesting GPU backend: {backend_name} (device {device_idx})")

        # Validate and select backend
        success, error_msg = self.gpu_backend_mgr.select_backend(backend_name)

        if success:
            logger.info(f"[{session_id}] Backend selected: {backend_name}")
            status = self.get_status_data()
        else:
            logger.warning(f"[{session_id}] Backend selection failed: {error_msg}")
            # Try fallback
            if self.gpu_backend_mgr.fallback_to_available():
                logger.info(f"[{session_id}] Fell back to {self.gpu_backend_mgr.get_current_backend_name()}")
                status = self.get_status_data()
                error_msg = f"Requested backend unavailable, using {status['current_backend']}"
            else:
                status = self.get_status_data()

        # Send updated status
        try:
            messages = pack_websocket(MsgType.STATUS, status)
            for msg in messages:
                await websocket.send(msg)
        except Exception as e:
            logger.error(f"[{session_id}] Failed to send updated status: {e}")


def create_device_handler(gpu_backend_mgr=None) -> GPUDeviceHandler:
    """Factory to create GPU device handler with optional backend manager.

    Args:
        gpu_backend_mgr: GPUBackendManager instance or None.

    Returns:
        GPUDeviceHandler instance.
    """
    return GPUDeviceHandler(gpu_backend_mgr)
