"""NVRHI-style GPU backend abstraction for multi-GPU support.

Supports: OptiX, CUDA, HIP, Vulkan backends with device detection,
validation, and selection.
"""

import logging
import subprocess
import platform
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Optional, Tuple

logger = logging.getLogger("remote-gpu.gpu_backend")


@dataclass
class GPUDevice:
    """Represents a single GPU device."""
    name: str
    vram_gb: float
    index: int
    driver_version: Optional[str] = None
    compute_capability: Optional[str] = None  # CUDA/OptiX


@dataclass
class BackendInfo:
    """Information about an available backend."""
    name: str
    version: str
    devices: list[GPUDevice]

    def to_dict(self) -> dict:
        """Convert to serializable dict."""
        return {
            "name": self.name,
            "version": self.version,
            "devices": [asdict(d) for d in self.devices],
        }


class GPUBackendError(Exception):
    """GPU backend-related error."""
    pass


class GPUBackend(ABC):
    """Abstract base for GPU backends."""

    @abstractmethod
    def detect() -> Optional["GPUBackend"]:
        """Detect if this backend is available. Return instance or None."""
        pass

    @abstractmethod
    def get_info(self) -> BackendInfo:
        """Return backend info including version and detected devices."""
        pass

    @abstractmethod
    def get_blender_device_arg(self) -> str:
        """Return the --cycles-device argument for Blender CLI."""
        pass

    @abstractmethod
    def validate(self) -> Tuple[bool, str]:
        """Validate backend is functional. Return (is_valid, error_msg)."""
        pass


class OptiXBackend(GPUBackend):
    """NVIDIA OptiX backend (RTX GPUs)."""

    def __init__(self, driver_version: str, devices: list[GPUDevice]):
        self.driver_version = driver_version
        self.devices = devices
        self.name = "OPTIX"

    @staticmethod
    def detect() -> Optional["OptiXBackend"]:
        """Detect OptiX availability via nvidia-smi and CUDA."""
        try:
            # Check nvidia-smi exists
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logger.debug("nvidia-smi not found or no GPUs")
                return None

            driver_version = result.stdout.strip().split("\n")[0].strip()

            # Detect devices via nvidia-smi
            devices = OptiXBackend._detect_nvidia_devices(driver_version)
            if not devices:
                logger.debug("No NVIDIA devices detected")
                return None

            logger.info(f"OptiX detected (driver {driver_version}, {len(devices)} device(s))")
            return OptiXBackend(driver_version, devices)

        except FileNotFoundError:
            logger.debug("nvidia-smi not found")
            return None
        except Exception as e:
            logger.warning(f"OptiX detection failed: {e}")
            return None

    @staticmethod
    def _detect_nvidia_devices(driver_version: str) -> list[GPUDevice]:
        """Query NVIDIA devices via nvidia-smi."""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return []

            devices = []
            for idx, line in enumerate(result.stdout.strip().split("\n")):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpu_name = parts[1]
                    memory_str = parts[2]
                    # Parse "24576 MiB" or "24 GB"
                    vram_gb = OptiXBackend._parse_vram(memory_str)
                    devices.append(GPUDevice(
                        name=gpu_name,
                        vram_gb=vram_gb,
                        index=idx,
                        driver_version=driver_version,
                    ))
            return devices
        except Exception as e:
            logger.warning(f"Failed to query NVIDIA devices: {e}")
            return []

    @staticmethod
    def _parse_vram(vram_str: str) -> float:
        """Parse VRAM string like '24576 MiB' or '24 GB' to GB float."""
        match = re.match(r"(\d+(?:\.\d+)?)\s*(\w+)", vram_str.strip())
        if not match:
            return 0.0
        value, unit = float(match.group(1)), match.group(2).upper()
        if unit.startswith("MIB"):
            return value / 1024.0
        elif unit.startswith("GB"):
            return value
        elif unit.startswith("KIB"):
            return value / (1024 * 1024)
        return value

    def get_info(self) -> BackendInfo:
        """Return OptiX backend info."""
        return BackendInfo(
            name=self.name,
            version=self.driver_version,
            devices=self.devices,
        )

    def get_blender_device_arg(self) -> str:
        """Return --cycles-device optix."""
        return "optix"

    def validate(self) -> Tuple[bool, str]:
        """Validate OptiX is functional."""
        try:
            # Check CUDA toolkit (nvcc) is available
            result = subprocess.run(
                ["nvcc", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False, "CUDA toolkit (nvcc) not found. Install NVIDIA CUDA."

            # Extract CUDA version
            match = re.search(r"release\s+([\d.]+)", result.stdout)
            if match:
                cuda_version = match.group(1)
                logger.info(f"CUDA {cuda_version} detected, OptiX valid")

            return True, ""
        except FileNotFoundError:
            return False, "CUDA toolkit (nvcc) not installed"
        except Exception as e:
            return False, f"OptiX validation failed: {e}"


class CUDABackend(GPUBackend):
    """NVIDIA CUDA backend (fallback from OptiX)."""

    def __init__(self, cuda_version: str, driver_version: str, devices: list[GPUDevice]):
        self.cuda_version = cuda_version
        self.driver_version = driver_version
        self.devices = devices
        self.name = "CUDA"

    @staticmethod
    def detect() -> Optional["CUDABackend"]:
        """Detect CUDA availability."""
        try:
            # Check CUDA toolkit
            result = subprocess.run(
                ["nvcc", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logger.debug("nvcc not found")
                return None

            # Extract CUDA version
            match = re.search(r"release\s+([\d.]+)", result.stdout)
            cuda_version = match.group(1) if match else "unknown"

            # Get driver version from nvidia-smi
            driver_result = subprocess.run(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            driver_version = driver_result.stdout.strip().split("\n")[0] if driver_result.returncode == 0 else "unknown"

            # Detect devices
            devices = CUDABackend._detect_nvidia_devices(driver_version)
            if not devices:
                logger.debug("No CUDA devices detected")
                return None

            logger.info(f"CUDA detected (CUDA {cuda_version}, driver {driver_version}, {len(devices)} device(s))")
            return CUDABackend(cuda_version, driver_version, devices)

        except FileNotFoundError:
            logger.debug("CUDA toolkit (nvcc) not found")
            return None
        except Exception as e:
            logger.warning(f"CUDA detection failed: {e}")
            return None

    @staticmethod
    def _detect_nvidia_devices(driver_version: str) -> list[GPUDevice]:
        """Query NVIDIA devices (same as OptiX)."""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return []

            devices = []
            for idx, line in enumerate(result.stdout.strip().split("\n")):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpu_name = parts[1]
                    memory_str = parts[2]
                    vram_gb = OptiXBackend._parse_vram(memory_str)
                    devices.append(GPUDevice(
                        name=gpu_name,
                        vram_gb=vram_gb,
                        index=idx,
                        driver_version=driver_version,
                    ))
            return devices
        except Exception as e:
            logger.warning(f"Failed to query NVIDIA devices: {e}")
            return []

    def get_info(self) -> BackendInfo:
        """Return CUDA backend info."""
        return BackendInfo(
            name=self.name,
            version=self.cuda_version,
            devices=self.devices,
        )

    def get_blender_device_arg(self) -> str:
        """Return --cycles-device cuda."""
        return "cuda"

    def validate(self) -> Tuple[bool, str]:
        """Validate CUDA is functional."""
        try:
            result = subprocess.run(
                ["nvcc", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False, "CUDA toolkit (nvcc) not found"
            return True, ""
        except FileNotFoundError:
            return False, "CUDA toolkit (nvcc) not installed"
        except Exception as e:
            return False, f"CUDA validation failed: {e}"


class HIPBackend(GPUBackend):
    """AMD HIP backend (AMD ROCm)."""

    def __init__(self, hip_version: str, devices: list[GPUDevice]):
        self.hip_version = hip_version
        self.devices = devices
        self.name = "HIP"

    @staticmethod
    def detect() -> Optional["HIPBackend"]:
        """Detect HIP (AMD ROCm) availability."""
        try:
            # Check hipcc (HIP compiler)
            result = subprocess.run(
                ["hipcc", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logger.debug("hipcc not found")
                return None

            # Extract HIP version
            match = re.search(r"HIP version:\s+([\d.]+)", result.stdout)
            hip_version = match.group(1) if match else "unknown"

            # Detect AMD devices via rocm-smi
            devices = HIPBackend._detect_amd_devices()
            if not devices:
                logger.debug("No AMD/HIP devices detected")
                return None

            logger.info(f"HIP detected (HIP {hip_version}, {len(devices)} device(s))")
            return HIPBackend(hip_version, devices)

        except FileNotFoundError:
            logger.debug("HIP (hipcc) not found")
            return None
        except Exception as e:
            logger.warning(f"HIP detection failed: {e}")
            return None

    @staticmethod
    def _detect_amd_devices() -> list[GPUDevice]:
        """Query AMD devices via rocm-smi."""
        try:
            result = subprocess.run(
                ["rocm-smi"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return []

            devices = []
            gpu_idx = 0
            current_gpu = None

            for line in result.stdout.split("\n"):
                if "GPU" in line and ("Device" in line or "Name" in line):
                    # Extract GPU index from lines like "GPU 0: gfx906"
                    match = re.search(r"GPU\s+(\d+)", line)
                    if match:
                        gpu_idx = int(match.group(1))
                        # Try to extract name
                        name_match = re.search(r":\s*(.+?)$", line)
                        current_gpu = name_match.group(1).strip() if name_match else f"GPU {gpu_idx}"

                if "Total VRAM" in line or "Mem Total" in line:
                    # Extract VRAM: "Total VRAM: 16384 MB"
                    match = re.search(r"(\d+)\s*MB", line)
                    if match and current_gpu:
                        vram_mb = int(match.group(1))
                        vram_gb = vram_mb / 1024.0
                        devices.append(GPUDevice(
                            name=current_gpu,
                            vram_gb=vram_gb,
                            index=gpu_idx,
                        ))
                        current_gpu = None

            return devices
        except Exception as e:
            logger.warning(f"Failed to query AMD devices: {e}")
            return []

    def get_info(self) -> BackendInfo:
        """Return HIP backend info."""
        return BackendInfo(
            name=self.name,
            version=self.hip_version,
            devices=self.devices,
        )

    def get_blender_device_arg(self) -> str:
        """Return --cycles-device hip."""
        return "hip"

    def validate(self) -> Tuple[bool, str]:
        """Validate HIP is functional."""
        try:
            result = subprocess.run(
                ["hipcc", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False, "HIP (hipcc) not found. Install AMD ROCm."
            return True, ""
        except FileNotFoundError:
            return False, "HIP (hipcc) not installed"
        except Exception as e:
            return False, f"HIP validation failed: {e}"


class VulkanBackend(GPUBackend):
    """Vulkan backend (experimental, multi-vendor)."""

    def __init__(self, vulkan_version: str, devices: list[GPUDevice]):
        self.vulkan_version = vulkan_version
        self.devices = devices
        self.name = "VULKAN"

    @staticmethod
    def detect() -> Optional["VulkanBackend"]:
        """Detect Vulkan availability."""
        try:
            # Check for vulkaninfo
            result = subprocess.run(
                ["vulkaninfo", "--summary"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logger.debug("vulkaninfo not found")
                return None

            # Extract Vulkan version from output
            match = re.search(r"Vulkan InstanceVersion:\s+([\d.]+)", result.stdout)
            vulkan_version = match.group(1) if match else "unknown"

            # Parse device count (basic)
            device_count = result.stdout.count("GPU")
            devices = [GPUDevice(
                name=f"Vulkan GPU {i}",
                vram_gb=0.0,  # Vulkan doesn't easily expose VRAM
                index=i,
            ) for i in range(max(1, device_count))]

            logger.info(f"Vulkan detected (version {vulkan_version}, {len(devices)} device(s))")
            return VulkanBackend(vulkan_version, devices)

        except FileNotFoundError:
            logger.debug("vulkaninfo not found")
            return None
        except Exception as e:
            logger.warning(f"Vulkan detection failed: {e}")
            return None

    def get_info(self) -> BackendInfo:
        """Return Vulkan backend info."""
        return BackendInfo(
            name=self.name,
            version=self.vulkan_version,
            devices=self.devices,
        )

    def get_blender_device_arg(self) -> str:
        """Return --cycles-device vulkan."""
        return "vulkan"

    def validate(self) -> Tuple[bool, str]:
        """Validate Vulkan is functional."""
        try:
            result = subprocess.run(
                ["vulkaninfo", "--summary"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False, "Vulkan SDK not found or not configured"
            return True, ""
        except FileNotFoundError:
            return False, "Vulkan SDK (vulkaninfo) not installed"
        except Exception as e:
            return False, f"Vulkan validation failed: {e}"


class GPUBackendManager:
    """Manages GPU backend detection, selection, and validation."""

    # Priority order: prefer OptiX over CUDA, then HIP, then Vulkan
    BACKEND_CLASSES = [OptiXBackend, CUDABackend, HIPBackend, VulkanBackend]

    def __init__(self):
        self._available_backends: dict[str, GPUBackend] = {}
        self._current_backend: Optional[GPUBackend] = None
        self._detect_all()

    def _detect_all(self):
        """Detect all available backends."""
        for backend_class in self.BACKEND_CLASSES:
            try:
                backend = backend_class.detect()
                if backend:
                    self._available_backends[backend.name] = backend
                    logger.info(f"Backend available: {backend.name}")
            except Exception as e:
                logger.warning(f"Failed to detect {backend_class.__name__}: {e}")

        # Default to first available
        if self._available_backends:
            self._current_backend = list(self._available_backends.values())[0]
            logger.info(f"Default backend selected: {self._current_backend.name}")

    def detect_available_backends(self) -> list[Tuple[str, str]]:
        """Return list of (name, version) tuples for available backends.

        E.g., [("OPTIX", "535.0"), ("CUDA", "12.0")]
        """
        result = []
        for name in sorted(self._available_backends.keys()):
            backend = self._available_backends[name]
            info = backend.get_info()
            result.append((info.name, info.version))
        return result

    def get_backend_info(self, backend_name: str) -> Optional[dict]:
        """Return detailed info for a backend or None if not available.

        Returns {"version": str, "devices": [{"name": str, "vram_gb": float, ...}]}
        """
        if backend_name not in self._available_backends:
            return None

        backend = self._available_backends[backend_name]
        info = backend.get_info()
        return info.to_dict()

    def select_backend(self, backend_name: str) -> Tuple[bool, str]:
        """Select a backend. Return (success, error_msg).

        Validates the backend before selection.
        """
        if backend_name not in self._available_backends:
            return False, f"Backend '{backend_name}' not available. Available: {list(self._available_backends.keys())}"

        backend = self._available_backends[backend_name]
        is_valid, error = backend.validate()
        if not is_valid:
            return False, f"Backend validation failed: {error}"

        self._current_backend = backend
        logger.info(f"Backend selected: {backend_name}")
        return True, ""

    def get_current_backend(self) -> Optional[GPUBackend]:
        """Return currently selected backend."""
        return self._current_backend

    def get_current_backend_name(self) -> str:
        """Return name of currently selected backend."""
        if self._current_backend:
            return self._current_backend.name
        return "NONE"

    def get_current_device_arg(self) -> str:
        """Return the --cycles-device argument for currently selected backend.

        E.g., "optix", "cuda", "hip", "vulkan", or empty string if no backend.
        """
        if self._current_backend:
            return self._current_backend.get_blender_device_arg()
        return ""

    def validate_backend_requirements(self, backend_name: str) -> Tuple[bool, str]:
        """Check if a backend can be used. Return (is_valid, error_msg).

        This is checked before switching to a backend.
        """
        if backend_name not in self._available_backends:
            return False, f"Backend '{backend_name}' not detected on this system"

        backend = self._available_backends[backend_name]
        return backend.validate()

    def fallback_to_available(self) -> bool:
        """If current backend fails, fall back to next available.

        Return True if fallback successful, False if no alternatives.
        """
        if not self._current_backend:
            return False

        current_name = self._current_backend.name
        remaining = [
            backend for name, backend in self._available_backends.items()
            if name != current_name
        ]

        if remaining:
            self._current_backend = remaining[0]
            logger.warning(f"Falling back to {self._current_backend.name}")
            return True

        logger.error("No fallback backends available")
        return False
