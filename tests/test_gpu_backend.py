"""Unit tests for GPU backend abstraction system.

Tests backend detection, validation, and selection logic.
"""

import pytest
import logging
from unittest.mock import patch, MagicMock

# Add parent directories to path for imports
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.gpu_backend import (
    GPUBackendManager,
    OptiXBackend,
    CUDABackend,
    HIPBackend,
    VulkanBackend,
    GPUDevice,
    BackendInfo,
)


class TestGPUBackendDetection:
    """Test GPU backend detection logic."""

    @patch('subprocess.run')
    def test_optix_detection_success(self, mock_run):
        """Test successful OptiX detection with nvidia-smi."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="535.0\n",
        )
        backend = OptiXBackend.detect()
        assert backend is not None
        assert backend.name == "OPTIX"
        assert backend.driver_version == "535.0"

    @patch('subprocess.run')
    def test_optix_detection_failure(self, mock_run):
        """Test OptiX detection when nvidia-smi is not available."""
        mock_run.side_effect = FileNotFoundError()
        backend = OptiXBackend.detect()
        assert backend is None

    @patch('subprocess.run')
    def test_cuda_detection_success(self, mock_run):
        """Test successful CUDA detection."""
        def mock_run_impl(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if args[0][0] == "nvcc":
                result.stdout = "release 12.0"
            else:
                result.stdout = "535.0\n"
            return result

        mock_run.side_effect = mock_run_impl
        backend = CUDABackend.detect()
        assert backend is not None
        assert backend.name == "CUDA"
        assert backend.cuda_version == "12.0"

    @patch('subprocess.run')
    def test_hip_detection_success(self, mock_run):
        """Test successful HIP (AMD ROCm) detection."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="HIP version: 5.0",
        )
        backend = HIPBackend.detect()
        assert backend is not None
        assert backend.name == "HIP"
        assert backend.hip_version == "5.0"

    @patch('subprocess.run')
    def test_hip_detection_failure(self, mock_run):
        """Test HIP detection when hipcc not available."""
        mock_run.side_effect = FileNotFoundError()
        backend = HIPBackend.detect()
        assert backend is None


class TestGPUBackendManager:
    """Test GPU backend manager functionality."""

    @patch('shared.gpu_backend.OptiXBackend.detect')
    @patch('shared.gpu_backend.CUDABackend.detect')
    @patch('shared.gpu_backend.HIPBackend.detect')
    @patch('shared.gpu_backend.VulkanBackend.detect')
    def test_manager_initialization(self, mock_vulkan, mock_hip, mock_cuda, mock_optix):
        """Test manager detects available backends on init."""
        # Mock CUDA and OptiX available, others not
        mock_optix.return_value = OptiXBackend(
            "535.0",
            [GPUDevice("RTX 5090", 24.0, 0)]
        )
        mock_cuda.return_value = CUDABackend(
            "12.0", "535.0",
            [GPUDevice("RTX 5090", 24.0, 0)]
        )
        mock_hip.return_value = None
        mock_vulkan.return_value = None

        mgr = GPUBackendManager()

        # Check detected backends
        available = mgr.detect_available_backends()
        assert len(available) == 2
        names = [name for name, _ in available]
        assert "OPTIX" in names
        assert "CUDA" in names

    @patch('shared.gpu_backend.OptiXBackend.detect')
    @patch('shared.gpu_backend.CUDABackend.detect')
    @patch('shared.gpu_backend.HIPBackend.detect')
    @patch('shared.gpu_backend.VulkanBackend.detect')
    def test_backend_selection(self, mock_vulkan, mock_hip, mock_cuda, mock_optix):
        """Test selecting a specific backend."""
        optix_backend = OptiXBackend(
            "535.0",
            [GPUDevice("RTX 5090", 24.0, 0)]
        )
        optix_backend.validate = MagicMock(return_value=(True, ""))

        cuda_backend = CUDABackend(
            "12.0", "535.0",
            [GPUDevice("RTX 5090", 24.0, 0)]
        )
        cuda_backend.validate = MagicMock(return_value=(True, ""))

        mock_optix.return_value = optix_backend
        mock_cuda.return_value = cuda_backend
        mock_hip.return_value = None
        mock_vulkan.return_value = None

        mgr = GPUBackendManager()

        # Default should be OptiX (first detected)
        assert mgr.get_current_backend_name() == "OPTIX"

        # Switch to CUDA
        success, error = mgr.select_backend("CUDA")
        assert success
        assert mgr.get_current_backend_name() == "CUDA"

    @patch('shared.gpu_backend.OptiXBackend.detect')
    @patch('shared.gpu_backend.CUDABackend.detect')
    @patch('shared.gpu_backend.HIPBackend.detect')
    @patch('shared.gpu_backend.VulkanBackend.detect')
    def test_device_arg_generation(self, mock_vulkan, mock_hip, mock_cuda, mock_optix):
        """Test generating Blender --cycles-device arguments."""
        optix_backend = OptiXBackend(
            "535.0",
            [GPUDevice("RTX 5090", 24.0, 0)]
        )
        mock_optix.return_value = optix_backend
        mock_cuda.return_value = None
        mock_hip.return_value = None
        mock_vulkan.return_value = None

        mgr = GPUBackendManager()

        # Check device arg
        device_arg = mgr.get_current_device_arg()
        assert device_arg == "optix"

    @patch('shared.gpu_backend.OptiXBackend.detect')
    @patch('shared.gpu_backend.CUDABackend.detect')
    @patch('shared.gpu_backend.HIPBackend.detect')
    @patch('shared.gpu_backend.VulkanBackend.detect')
    def test_fallback_to_available(self, mock_vulkan, mock_hip, mock_cuda, mock_optix):
        """Test fallback when selected backend becomes unavailable."""
        optix_backend = OptiXBackend(
            "535.0",
            [GPUDevice("RTX 5090", 24.0, 0)]
        )
        cuda_backend = CUDABackend(
            "12.0", "535.0",
            [GPUDevice("RTX 5090", 24.0, 0)]
        )

        mock_optix.return_value = optix_backend
        mock_cuda.return_value = cuda_backend
        mock_hip.return_value = None
        mock_vulkan.return_value = None

        mgr = GPUBackendManager()
        initial_backend = mgr.get_current_backend_name()
        assert initial_backend == "OPTIX"

        # Fallback should switch to CUDA
        fallback_success = mgr.fallback_to_available()
        assert fallback_success
        assert mgr.get_current_backend_name() == "CUDA"


class TestBackendInfo:
    """Test backend info data structures."""

    def test_backend_info_serialization(self):
        """Test BackendInfo can be serialized to dict."""
        devices = [
            GPUDevice("RTX 5090", 24.0, 0),
            GPUDevice("RTX 4090", 24.0, 1),
        ]
        info = BackendInfo("OPTIX", "535.0", devices)

        data = info.to_dict()
        assert data["name"] == "OPTIX"
        assert data["version"] == "535.0"
        assert len(data["devices"]) == 2
        assert data["devices"][0]["name"] == "RTX 5090"
        assert data["devices"][0]["vram_gb"] == 24.0


class TestDeviceDetection:
    """Test GPU device detection parsing."""

    def test_vram_parsing_mib(self):
        """Test parsing VRAM in MiB format."""
        vram_gb = OptiXBackend._parse_vram("24576 MiB")
        assert vram_gb == 24.0

    def test_vram_parsing_gb(self):
        """Test parsing VRAM in GB format."""
        vram_gb = OptiXBackend._parse_vram("24 GB")
        assert vram_gb == 24.0

    def test_vram_parsing_invalid(self):
        """Test parsing invalid VRAM string."""
        vram_gb = OptiXBackend._parse_vram("invalid")
        assert vram_gb == 0.0


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
