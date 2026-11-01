import pytest

from backend.gpu import GpuInfo, detect_gpu, plan_gpu_layers, register_cuda_dlls

GB = 2**30


@pytest.mark.gpu
def test_detects_nvidia_gpu():
    g = detect_gpu()
    assert g.available
    assert "NVIDIA" in g.name
    assert g.total_mb > 4000
    assert 0 <= g.free_mb <= g.total_mb


def test_detect_gpu_never_raises_on_bad_index():
    g = detect_gpu(index=99)
    assert g.available is False and g.error


def test_cuda_runtime_libraries_load():
    """llama-cpp-python's CUDA build must import (needs cudart/cublas from nvidia-* wheels)."""
    register_cuda_dlls()
    llama_cpp = pytest.importorskip("llama_cpp")
    assert llama_cpp.llama_supports_gpu_offload()


def test_auto_full_offload_when_it_fits():
    g = GpuInfo(True, total_mb=8151, free_mb=7600)
    n, why = plan_gpu_layers("auto", int(4.4 * GB), 28, g, reserve_mb=1024)
    assert n == -1 and "fits" in why


def test_auto_partial_offload_when_vram_is_short():
    g = GpuInfo(True, total_mb=8151, free_mb=3500)
    n, why = plan_gpu_layers("auto", int(4.4 * GB), 28, g, reserve_mb=1024)
    assert 0 < n < 28 and "partial" in why


def test_auto_cpu_when_no_gpu():
    n, why = plan_gpu_layers("auto", GB, 28, GpuInfo(False, error="no driver"), reserve_mb=1024)
    assert n == 0 and "no usable GPU" in why


def test_auto_cpu_when_vram_below_reserve():
    n, _ = plan_gpu_layers("auto", GB, 28, GpuInfo(True, free_mb=500), reserve_mb=1024)
    assert n == 0


def test_explicit_layer_count_respected():
    assert plan_gpu_layers(10, GB, 28, GpuInfo(False), 1024)[0] == 10
