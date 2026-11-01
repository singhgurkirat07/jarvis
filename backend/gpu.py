"""GPU detection (NVML), CUDA DLL discovery and hardware-aware layer planning."""
from __future__ import annotations

import glob
import logging
import os
import site
import sys
from dataclasses import dataclass, asdict

log = logging.getLogger("jarvis.gpu")
_dll_handles: list = []


def register_cuda_dlls() -> list[str]:
    """Windows: make the pip-installed CUDA runtime (nvidia-*-cu12 wheels) visible to llama.dll.

    llama-cpp-python's CUDA build needs cudart64_12.dll / cublas64_12.dll. No CUDA Toolkit is
    installed system-wide here, so without this the import fails with "Could not find module
    llama.dll (or one of its dependencies)". Nothing is installed or modified; we only add
    already-present directories to this process's DLL search path.
    """
    if sys.platform != "win32" or _dll_handles:
        return []
    dirs = []
    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    for root in roots:
        for d in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            _dll_handles.append(os.add_dll_directory(d))
            dirs.append(d)
    return dirs


@dataclass
class GpuInfo:
    available: bool
    name: str | None = None
    driver: str | None = None
    total_mb: int = 0
    used_mb: int = 0
    free_mb: int = 0
    error: str | None = None

    def as_dict(self):
        return asdict(self)


def detect_gpu(index: int = 0) -> GpuInfo:
    try:
        import pynvml
    except ImportError:
        return GpuInfo(False, error="pynvml (nvidia-ml-py) not installed")
    try:
        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(index)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            name = pynvml.nvmlDeviceGetName(h)
            driver = pynvml.nvmlSystemGetDriverVersion()
            mb = 1024 * 1024
            return GpuInfo(
                True,
                name=name.decode() if isinstance(name, bytes) else name,
                driver=driver.decode() if isinstance(driver, bytes) else driver,
                total_mb=mem.total // mb, used_mb=mem.used // mb, free_mb=mem.free // mb,
            )
        finally:
            pynvml.nvmlShutdown()
    except Exception as e:  # no NVIDIA driver / no device
        return GpuInfo(False, error=str(e))


def plan_gpu_layers(setting, model_bytes: int, block_count: int | None,
                    gpu: GpuInfo, reserve_mb: int) -> tuple[int, str]:
    """Return (n_gpu_layers, reason). -1 means offload every layer.

    For "auto": offload everything if the weights plus a safety reserve fit in free VRAM,
    otherwise offload only as many layers as fit, else run on CPU.
    """
    if setting != "auto":
        return int(setting), "configured explicitly"
    if not gpu.available:
        return 0, f"no usable GPU ({gpu.error})"
    model_mb = model_bytes / (1024 * 1024)
    budget = gpu.free_mb - reserve_mb
    if budget >= model_mb:
        return -1, f"model {model_mb:.0f} MiB fits in {gpu.free_mb} MiB free VRAM (reserve {reserve_mb} MiB)"
    layers = block_count or 32
    fit = int(budget / (model_mb / (layers + 1))) if budget > 0 else 0
    fit = max(0, min(fit, layers))
    return fit, f"partial offload: {fit}/{layers} layers fit in {gpu.free_mb} MiB free VRAM (reserve {reserve_mb} MiB)"
