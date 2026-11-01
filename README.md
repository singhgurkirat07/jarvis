# Jarvis — Local AI Assistant (Phases 1, 2, 3, 4 & 5)

Jarvis is a local-first AI chat and voice application that runs GGUF models directly from disk using `llama.cpp` (`llama-cpp-python`) with NVIDIA CUDA hardware acceleration, a FastAPI backend, SQLite conversation/settings/memory/tool/task/notification persistence, deterministic context management, structured tool calling with 3-level permission enforcement, a persistent task engine with timezone-aware scheduling and reminders, optional SSRF-protected web research, a 100% local CPU voice pipeline (`faster-whisper` STT, `piper-tts` TTS, and optional `openwakeword` `"Hey Jarvis"` wake-word activation), structured JSON logging, and a responsive streaming web UI.

- **No Ollama**
- **No cloud LLM APIs, cloud speech/TTS APIs, cloud databases, or telemetry**
- **No automatic model downloads**
- **Offline socket guard enabled by default (`offline_mode = true`)**
- **Wake-word monitoring disabled by default (`wake_word_enabled = false`) and never auto-starts on restart**
- **Bound strictly to `127.0.0.1` by default**

---

## Project Structure

```text
Jarvis/
├── backend/
│   ├── __init__.py
│   ├── __main__.py        # CLI entrypoint (`py -3.12 -m backend`)
│   ├── app.py             # FastAPI routes (chat, conversations, search, memories, settings, tools, approvals, tasks, notifications, voice)
│   ├── config.py          # Settings loader & validator (JSON + SQLite + JARVIS_* env)
│   ├── context.py         # Token-budget ContextManager between SQLite, memories, tools & llama.cpp
│   ├── db.py              # SQLite WAL storage (conversations, messages, settings, memories, tools, approvals, tasks, executions, notifications)
│   ├── engine.py          # llama.cpp lifecycle, thread-safe lock, OOM fallback, reload
│   ├── gpu.py             # NVML GPU detection, CUDA DLL registration, layer planner
│   ├── logging_setup.py   # Structured JSON-lines file logger + console logger
│   ├── memory.py          # Explicit memory extraction, secret guard & deterministic retrieval
│   ├── model_file.py      # GGUF v2/v3 binary header validator & metadata parser
│   ├── offline.py         # Process-level outbound network blocker
│   ├── permissions.py     # 3-level permission model & cryptographic approval argument binding
│   ├── services.py        # Service layer for conversations, chat turns, memories, settings, tools, tasks, notifications & voice
│   ├── tasks.py           # Persistent TaskEngine, TaskScheduler, recurrence calculator, recovery & chat task parser
│   ├── tools.py           # Central ToolRegistry & 11 built-in tools (Date/Time, AST Calculator, Workspace, System, Web)
│   ├── voice.py           # Local CPU VoiceService (AudioDeviceManager, Faster-Whisper STT, Piper TTS, openWakeWord)
│   └── web.py             # SSRF-protected HTTP fetcher, HTML sanitizing parser & prompt-injection guard
├── config/
│   └── settings.json      # Default model, GPU, generation, offline mode, tool, workspace, scheduler & voice settings
├── data/                  # SQLite database (`jarvis.db`) & benchmark results (git-ignored)
├── frontend/
│   ├── index.html         # Responsive chat & voice UI with sidebar, search, memories, tools, tasks, notifications & settings modals
│   ├── styles.css         # Mobile-first stylesheet with dark, midnight, and light themes
│   └── app.js             # Conversation management, SSE streaming, tool badges, approvals, citations, tasks, notifications & voice UI
├── logs/                  # Rotating JSON-lines logs (`jarvis.log`, git-ignored)
├── models/
│   └── README.md          # Notes on GGUF & voice model path configuration
├── scripts/
│   ├── benchmark.py               # Subprocess-isolated CPU vs GPU inference benchmark
│   ├── start_server.ps1           # PowerShell server launcher
│   └── voice_hardware_check.py    # Interactive microphone, speaker TTS & wake-word hardware verification
├── tests/                 # 250 automated unit and integration tests (Phases 1–5)
├── workspace/             # Dedicated sandboxed directory for workspace file tools
├── .gitignore
├── PHASE_1_REPORT.md      # Phase 1 completion report
├── PHASE_2_REPORT.md      # Phase 2 completion report
├── PHASE_3_REPORT.md      # Phase 3 completion report
├── PHASE_4_REPORT.md      # Phase 4 completion report
├── PHASE_5_REPORT.md      # Phase 5 completion report
├── pytest.ini
├── README.md
└── requirements.txt
```

---

## Quick Start

```powershell
py -3.12 -m backend
# or: .\scripts\start_server.ps1
```

Then open **http://127.0.0.1:8000** in your browser.

---

## Testing & Hardware Verification

Run the complete Phase 1–5 automated test suite (`250` tests):

```powershell
py -3.12 -m pytest -v
```

Run the interactive physical microphone/speaker hardware verification script:

```powershell
py -3.12 scripts/voice_hardware_check.py
```
