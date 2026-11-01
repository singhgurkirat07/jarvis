# JARVIS — Phase 1 Completion Report

**Date:** 2026-10-31  
**Phase:** Phase 1 — Foundation and Direct Local AI Inference  
**Status:** Complete (awaiting user approval before any Git commit or Phase 2 work)

---

## 1. Verified Project Root & Pre-Existing Workspace Audit

- **Verified Working Directory:** `C:\Users\Gurkirat Singh\Desktop\jarvis` (Windows case-insensitive equivalent of `C:\Users\Gurkirat Singh\Desktop\Jarvis`).
- **Pre-existing files in workspace:** None (directory was completely empty prior to setup; nothing was overwritten).
- **Git Status:**
  - Initially, `C:\Users\Gurkirat Singh\Desktop\jarvis\.git` did not exist (note: an unrelated empty `.git` directory was detected in the parent user profile `C:\Users\Gurkirat Singh\.git`).
  - A dedicated Git repository was initialized inside `C:\Users\Gurkirat Singh\Desktop\jarvis` (`git init`) so Jarvis is isolated from the parent directory.
  - **No commits have been made**, per instruction.

---

## 2. Directory Structure

```text
C:\Users\Gurkirat Singh\Desktop\jarvis\
├── backend/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── config.py
│   ├── db.py
│   ├── engine.py
│   ├── gpu.py
│   ├── logging_setup.py
│   ├── model_file.py
│   └── offline.py
├── config/
│   └── settings.json
├── data/
│   ├── benchmark_results.json   (git-ignored)
│   └── jarvis.db                (git-ignored runtime SQLite DB)
├── frontend/
│   ├── app.js
│   ├── index.html
│   └── styles.css
├── logs/
│   └── jarvis.log               (git-ignored rotating JSON-lines log)
├── models/
│   └── README.md
├── scripts/
│   ├── benchmark.py
│   └── start_server.ps1
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_engine.py
│   ├── test_gpu.py
│   ├── test_inference_real.py
│   ├── test_model_file.py
│   ├── test_offline.py
│   └── test_shutdown.py
├── .gitignore
├── PHASE_1_REPORT.md
├── pytest.ini
├── README.md
└── requirements.txt
```

---

## 3. Hardware, Environment & Python Runtime

- **OS:** Microsoft Windows 11 Home Single Language (64-bit)
- **Installed Python Versions Discovered:**
  - `Python 3.12.10` (`py -3.12`, default `python` on PATH) — **Selected runtime** (contains `llama_cpp_python 0.3.4`, `fastapi 0.110.0`, `uvicorn 0.29.0`, `nvidia-*-cu12`, `nvidia-ml-py 13.610.43`, `psutil 7.2.2`, `pytest 9.1.1`, `httpx 0.28.1`).
  - `Python 3.11.9` (`py -3.11`)
  - `Python 3.14.6` (`py -3.14`)
- **Packages Installed/Upgraded/Downloaded:** **Zero.** All functionality uses existing local packages.
- **GPU & Driver:**
  - **GPU:** NVIDIA GeForce RTX 5050 Laptop GPU (Blackwell architecture, compute capability `12.0` / `sm_120`)
  - **Driver:** `610.47` (CUDA Driver UMD `13.3`)
  - **Total VRAM:** `8,151 MiB` (~8 GB), ~`7,676–8,033 MiB` free at idle
- **System Memory & Storage:**
  - **System RAM:** `23.31 GiB` usable (24 GB physical), ~`8.73 GiB` free at audit time
  - **Disk (`C:`):** `191.91 GiB` (`206,062,133,248` bytes) free of `951.65 GiB`
- **Git:** `git version 2.54.0.windows.1`

---

## 4. Model Path, Validation & Availability

- **Configured Model Path:** `C:\Users\Gurkirat Singh\models\qwen2.5-7b-instruct.gguf`
- **Status:** Exists, readable, kept in place (not moved or copied).
- **Parsed GGUF Header Metadata (`backend/model_file.py`):**
  - **Format:** `GGUF` v3
  - **File Size:** `4,683,074,240` bytes (`4,466 MiB` / `4.36 GiB`)
  - **Model Name:** `Qwen2.5 7B Instruct`
  - **Architecture:** `qwen2`
  - **Quantization:** `Q4_K_M` (`ftype = 15`, 339 tensors)
  - **Block / Layer Count:** `28` repeating blocks (`29` offloaded layers including output layer)
  - **Training Context Length:** `32,768` tokens (configured runtime `n_ctx`: `4,096`)

---

## 5. Inference Runtime & GPU / CUDA Investigation

### 5.1 Windows CUDA Runtime DLL Discovery
`llama_cpp_python 0.3.4` in Python 3.12 includes `ggml-cuda.dll` (compiled for CUDA 12 with architectures `500..900`), which dynamically links against `cudart64_12.dll`, `cublas64_12.dll`, and `cublasLt64_12.dll`. Because no system-wide CUDA Toolkit is on `PATH`, importing `llama_cpp` directly fails unless the existing `nvidia-cuda-runtime-cu12`, `nvidia-cublas-cu12`, and `nvidia-cuda-nvrtc-cu12` wheel `bin/` directories in `site-packages/nvidia/*/bin` are registered via `os.add_dll_directory()`. `backend/gpu.py::register_cuda_dlls()` performs this automatically before `llama_cpp` is imported.

### 5.2 Investigation of `offload_kqv` on RTX 5050 (`sm_120`)
We isolated and reproduced the CUDA crash in a child process:
- With `n_gpu_layers = -1`, `offload_kqv = True`, `flash_attn = False`:
  - `llama.cpp` loads all 29 layers onto `CUDA0` and allocates the `112 MiB` FP16 KV cache on `CUDA0`, then immediately crashes on the first prompt evaluation with:
    ```text
    CUDA error: an illegal memory access was encountered
    ggml-cuda.cu:70: CUDA error (exit code -1073740791 / 0xC0000409)
    ```
  - **Root cause:** `llama-cpp-python 0.3.4` embeds CUDA kernels compiled up to compute capability `9.0` (`ARCHS = 500..900`). On the RTX 5050 (`sm_120`, Blackwell), the non-Flash-Attention CUDA KQV kernel triggers an illegal memory access when operating on GPU-resident KV cache tensors.
- **Verified Safe Default Configuration (`config/settings.json`):**
  - `n_gpu_layers: "auto"` (plans `-1` / all `29/29` weight layers on GPU when free VRAM minus `1024 MiB` reserve exceeds the `4,466 MiB` model size, with automatic fallback to half layers then CPU on load failure).
  - `offload_kqv: false` (keeps the KV cache and KQV attention buffer in host RAM while keeping all 29 transformer weight layers on the RTX 5050 GPU).
  - *Additional finding:* Setting both `offload_kqv: true` and `flash_attn: true` uses the Flash Attention CUDA kernel path, which also succeeded in isolated benchmark testing at `48.16 tok/s`, but `offload_kqv: false` (`flash_attn: false`) is preserved as the default configuration in `config/settings.json` for maximum stability as requested.

---

## 6. Performance Benchmarks (VRAM, RAM, Load Time, Tokens/sec)

Measured via `py -3.12 scripts/benchmark.py --include-unsafe` (`n_ctx = 2048`, `max_tokens = 128`, `temperature = 0`, isolated subprocess per configuration; raw data in `data/benchmark_results.json`):

| Configuration | `n_gpu_layers` | `offload_kqv` | `flash_attn` | Load Time (s) | Model VRAM Delta (MiB) | Total GPU VRAM Used (MiB) | Process RSS RAM (MiB) | Time to First Token (s) | Decode Speed (tok/s) | End-to-End Speed (tok/s) | Status |
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **CPU only** (`cpu`) | `0` | `false` | `false` | `1.62 s` | `863 MiB` | `1,340 MiB` | `4,819 MiB` | `0.250 s` | **4.22 tok/s** | `4.22 tok/s` | Stable |
| **Partial GPU** (`gpu_half`) | `14` | `false` | `false` | `2.77 s` | `2,712 MiB` | `3,226 MiB` | `4,828 MiB` | `0.163 s` | **7.01 tok/s** | `7.00 tok/s` | Stable |
| **Full GPU — Safe Default** (`gpu_full`) | `-1` (29/29) | `false` | `false` | `4.03 s` | `4,459 MiB` | `5,061 MiB` | `4,827 MiB` | `0.036 s` | **29.33 tok/s** | `29.32 tok/s` | **Default** |
| **Full GPU + KQV** (`gpu_full_kqv`) | `-1` (29/29) | `true` | `false` | — | — | — | — | — | — | — | **Crashes** (`ggml-cuda.cu:70` illegal memory access) |
| **Full GPU + KQV + FlashAttn** (`gpu_full_kqv_fa`) | `-1` (29/29) | `true` | `true` | `4.49 s` | `4,712 MiB` | `5,150 MiB` | `4,638 MiB` | `0.021 s` | **48.16 tok/s** | `48.15 tok/s` | Experimental |

- **GPU vs CPU Speedup (Safe Default `gpu_full` vs `cpu`):** **6.95× faster** decode throughput (`29.33 tok/s` vs `4.22 tok/s`) and **6.94× lower** time-to-first-token (`36 ms` vs `250 ms`), while leaving **~3,090 MiB of free VRAM headroom** on the 8 GB RTX 5050.

---

## 7. Test Suite Results

Command executed: `py -3.12 -m pytest -v`

- **Passed:** `72`
- **Failed:** `0`
- **Skipped:** `0`
- **Total Duration:** `27.99 s`

### Test Coverage Breakdown by Requirement

| Requirement Area | Test File(s) | Executed | Passed | Failed | Skipped |
|---|---|---:|---:|---:|---:|
| Model file discovery, GGUF v2/v3 validation & config | `tests/test_model_file.py` | 14 | 14 | 0 | 0 |
| GPU detection (NVML), CUDA DLL registration & layer planning | `tests/test_gpu.py` | 8 | 8 | 0 | 0 |
| Engine lifecycle, OOM fallback, concurrency lock, context trimming, stream abort | `tests/test_engine.py` | 13 | 13 | 0 | 0 |
| FastAPI endpoints, SSE streaming, SQLite persistence, invalid inputs (12 cases), loopback & Host guard | `tests/test_api.py` | 23 | 23 | 0 | 0 |
| Real GGUF model initialization, GPU offload, deterministic & streaming inference, multi-turn, corrupt GGUF handling | `tests/test_inference_real.py` | 6 | 6 | 0 | 0 |
| Offline socket blocker, env flags, static check against cloud/Ollama imports, real offline inference | `tests/test_offline.py` | 6 | 6 | 0 | 0 |
| Graceful shutdown (in-process & real subprocess `CTRL_BREAK_EVENT` + VRAM release verification) | `tests/test_shutdown.py` | 2 | 2 | 0 | 0 |
| **Total** | **7 test modules** | **72** | **72** | **0** | **0** |

### Visual & Browser QA
- Verified in headless Chrome via DevTools Protocol across **1280×800 (desktop)**, **768×1024 (tablet)**, and **375×740 (mobile)** viewports:
  - Semantic HTML (`header`, `main`, `section`, `footer`), `<html lang="en">`, single `<h1>`, SEO/meta tags.
  - Live status indicator (`Qwen2.5 7B Instruct ready on GPU`), real streaming chat response (`24.1 tokens/s`), zero horizontal overflow on mobile (`375px`), and **zero browser console errors**.

---

## 8. Files Created or Changed

All files below were created inside `C:\Users\Gurkirat Singh\Desktop\jarvis` (no pre-existing files existed):

1. `.gitignore` — Excludes `.gguf`/model files, `.env`/secrets, `data/*.db`, `logs/`, caches, and temp files.
2. `README.md` — Usage, configuration, architecture, API, and test documentation.
3. `PHASE_1_REPORT.md` — This completion report.
4. `requirements.txt` — Pinned versions of the verified Python 3.12 packages.
5. `pytest.ini` — Pytest configuration and markers (`model`, `gpu`).
6. `config/settings.json` — Default local model, GPU, generation, and localhost server configuration.
7. `backend/__init__.py` — Package docstring.
8. `backend/__main__.py` — CLI entrypoint with loopback check and Windows `SIGBREAK`/`SIGINT` graceful exit handling.
9. `backend/config.py` — Typed `Settings` loader (`config/settings.json` + `JARVIS_*` env overrides).
10. `backend/model_file.py` — Pure-Python GGUF v2/v3 header parser and model validator.
11. `backend/gpu.py` — NVML GPU detector, Windows CUDA wheel DLL path registrar, and VRAM-aware GPU layer planner.
12. `backend/offline.py` — Process-level socket connection guard blocking any non-loopback network connection.
13. `backend/engine.py` — `llama.cpp` model lifecycle manager, OOM layer fallback chain, thread-safe generation lock, token-aware context trimmer, and synchronous/streaming generators.
14. `backend/logging_setup.py` — Rotating JSON-lines logger (`logs/jarvis.log`) and console logger.
15. `backend/db.py` — Thread-safe SQLite (WAL) conversation and metrics store (`data/jarvis.db`).
16. `backend/app.py` — FastAPI application, loopback/Host-header middleware, `/api/health`, `/api/status`, `/api/chat` (JSON & SSE), `/api/conversations/{id}`, and static frontend mount.
17. `frontend/index.html` — Responsive, accessible single-page chat interface.
18. `frontend/styles.css` — Mobile-first responsive styling.
19. `frontend/app.js` — Model status polling, streaming SSE reader, cancellation (`Stop`), and conversation management.
20. `models/README.md` — Documentation for model directory usage.
21. `scripts/benchmark.py` — Subprocess-isolated CPU vs GPU inference benchmark runner.
22. `scripts/start_server.ps1` — PowerShell launcher script.
23. `tests/conftest.py` — Shared fixtures, synthetic GGUF generator, and `FakeLlama` test double.
24. `tests/test_model_file.py` — Model discovery, GGUF header validation, and settings tests.
25. `tests/test_gpu.py` — NVML GPU detection, CUDA DLL load, and GPU layer planner tests.
26. `tests/test_engine.py` — Engine lifecycle, OOM fallback, locking, trimming, and shutdown tests.
27. `tests/test_api.py` — API contract, streaming, persistence, input validation, error handling, and loopback security tests.
28. `tests/test_inference_real.py` — End-to-end tests against `qwen2.5-7b-instruct.gguf` on the RTX 5050 GPU.
29. `tests/test_offline.py` — Offline socket enforcement and zero-network real inference tests.
30. `tests/test_shutdown.py` — In-process and real subprocess graceful shutdown + VRAM release tests.

---

## 9. Remaining Limitations & Notes for Future Phases

1. **Blackwell (`sm_120`) Standard KQV Kernel in `llama-cpp-python 0.3.4`:** Because the prebuilt `llama-cpp-python 0.3.4` wheel was compiled prior to Blackwell (`ARCHS = 500..900`), `offload_kqv` must remain `false` unless `flash_attn: true` is also enabled or `llama-cpp-python` is rebuilt with native `sm_120` support in a future phase.
2. **Single-Request Concurrency:** `llama.cpp` contexts are not thread-safe for simultaneous evaluation; `Engine` serializes generation requests with a lock (`queue_timeout_s = 120s`, returning HTTP `429` if exceeded).
3. **Parent Directory `.git` Folder:** An empty, uncommitted `.git` folder exists at `C:\Users\Gurkirat Singh\.git` (outside the project). Initializing `.git` inside `C:\Users\Gurkirat Singh\Desktop\jarvis` isolates Jarvis from it, though you may wish to remove `C:\Users\Gurkirat Singh\.git` manually if it was created accidentally in the past.
4. **Python Version Selection:** Always invoke via `py -3.12` (or `.\scripts\start_server.ps1`), as `py` without a version flag defaults to Python `3.14` where `llama-cpp-python` is not installed.
