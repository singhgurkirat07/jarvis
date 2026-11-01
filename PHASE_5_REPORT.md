# JARVIS — Phase 5 Completion Report: Local Voice Input, Speech Output & Wake-Word Activation

**Date:** 2026-11-01  
**Project Root:** `C:\Users\Gurkirat Singh\Desktop\jarvis`  
**Phase Status:** Automated Implementation, 250-Test Regression Suite & Browser Visual QA Complete; Physical Microphone/Speaker Hardware Check Ready for User Confirmation (`scripts/voice_hardware_check.py`)

---

## 1. Executive Summary

Phase 5 equips **JARVIS** with a 100% local, privacy-preserving voice interface (`backend/voice.py`) integrated directly into the existing FastAPI backend (`backend/app.py`), service layer (`backend/services.py`), persistent SQLite settings (`backend/config.py`, `data/jarvis.db`), and responsive web UI (`frontend/index.html`, `frontend/styles.css`, `frontend/app.js`).

Every stage of the voice pipeline—audio device management, speech-to-text (STT), text-to-speech (TTS), and optional wake-word detection—executes **locally on the CPU** (`0 MB` of GPU VRAM consumed), preserving the entire 8 GB VRAM budget on the NVIDIA GeForce RTX 5050 Laptop GPU for `qwen2.5-7b-instruct.gguf` (`offload_kqv=False`).

---

## 2. Speech-to-Text (STT) Engine & Model

- **Engine:** `faster-whisper 1.2.1` (`ctranslate2 4.8.2`)
- **Execution Device & Precision:** `device="cpu"`, `compute_type="int8"`, `local_files_only=True`
- **Local Model Path:** `C:\Users\Gurkirat Singh\models\faster-whisper-small.en` (`model.bin` 461 MB, `config.json`, `tokenizer.json`, `vocabulary.txt`)
- **Capabilities Implemented (`LocalSpeechToText` in `backend/voice.py`):**
  - Model availability verification (`check_model_available()`) with clear error reporting (`VoiceModelMissingError`) if model files are absent.
  - In-memory 16 kHz mono PCM16 WAV validation, resampling, and RMS/peak silence detection (`compute_audio_metrics()`).
  - Configurable recording duration (`listening_duration`, default `8.0s`, bounded `1.0s – 60.0s`) and manual early stop (`POST /api/voice/stop-recording`) or cancellation (`POST /api/voice/cancel`).
  - Default **Transcript Preview & Edit Confirmation** (`WaitingForConfirmation` state) so recognized speech can be reviewed and edited before submission to the LLM, with optional `auto_submit_transcript`.
  - Honest uncalibrated confidence reporting: returns raw Whisper `avg_logprob` and `no_speech_prob` alongside `"is_calibrated": False` and an explicit note that these values are uncalibrated log-likelihood heuristics.

---

## 3. Text-to-Speech (TTS) Engine & Voice

- **Engine:** `piper-tts 1.8.0` (`onnxruntime 1.29.0`)
- **Execution Device:** CPU (`use_cuda=False`)
- **Local Voice Model:** `C:\Users\Gurkirat Singh\models\en_GB-alan-medium.onnx` (60.3 MB, `22050 Hz`) + `C:\Users\Gurkirat Singh\models\en_GB-alan-medium.onnx.json`
- **Capabilities Implemented (`LocalTextToSpeech` in `backend/voice.py`):**
  - Local voice discovery (`discover_local_voices()`) in the folder that holds the configured voice (`C:\Users\Gurkirat Singh\models`) and in the project's `models/` folder.
  - Markdown cleanup (`clean_text_for_speech()`) and word-safe sentence chunking (`chunk_text_for_tts()`) that **never cuts words mid-word**.
  - Configurable `speech_speed` (`0.5 – 2.0`, default `1.0`) and `speech_volume` (`0.0 – 2.0`, default `1.0`).
  - Immediate playback interruption (`POST /api/voice/stop-speaking`) and automatic interruption when new user input or recording begins (`stop_speaking_on_input = True`).
  - Graceful fallback if TTS voice assets are missing (`VoiceModelMissingError` caught in `speak_response()` so text chat continues normally).

---

## 4. Wake-Word Engine & Model

- **Engine:** `openwakeword 0.6.0` (`onnxruntime 1.29.0`, `inference_framework="onnx"`)
- **Execution Device:** CPU
- **Local Wake-Word Model:** `C:\Users\Gurkirat Singh\models\hey_jarvis_v0.1.onnx` (`"Hey Jarvis"`, 1.2 MB)
- **Capabilities Implemented (`WakeWordDetector` in `backend/voice.py`):**
  - **Disabled by default (`wake_word_enabled = False`)** and **never auto-starts after application restart** (`SettingsService.load_persisted_into_startup()` and `VoiceService.__init__()` explicitly reset `wake_word_enabled = False` on startup).
  - Lightweight 16 kHz frame evaluation (`1280` samples / 80 ms frames) without running continuous Whisper transcription.
  - Configurable `wake_word_sensitivity` (`0.05 – 0.99`, default `0.5`) and `wake_word_timeout_seconds` (`1.0 – 120.0s`, default `10.0s`).
  - Duplicate activation suppression via active command-window locking (`duplicate_suppressed_active_window`) and post-trigger cooldown (`duplicate_suppressed_cooldown`).
  - Immediate one-click disable in the topbar (`#wake-word-indicator`) and Settings modal.

---

## 5. Audio Devices Detected (`sounddevice 0.5.6`)

Measured on this machine on 2026-11-01 with `AudioDeviceManager.list_input_devices()` / `list_output_devices()`. Windows exposes each physical device once per host API (MME, DirectSound, WASAPI, WDM-KS), so the same hardware appears several times. Jarvis lists them as reported and does not merge duplicates. MME names are cut off at 31 characters by Windows.

### Microphone input devices (11 endpoints)
| Index | Name (as reported) | Ch | Rate | Host API | Default |
|---:|---|---:|---:|---:|---|
| 0 | Microsoft Sound Mapper - Input | 2 | 44100 | 0 | |
| 1 | Microphone Array (Realtek(R) Au | 2 | 44100 | 0 | **Yes** |
| 4 | Primary Sound Capture Driver | 2 | 44100 | 1 | |
| 5 | Microphone Array (Realtek(R) Audio) | 2 | 44100 | 1 | |
| 9 | Microphone Array (Realtek(R) Audio) | 2 | 48000 | 2 | |
| 12 | PC Speaker (Realtek HD Audio 2nd output with HAP) | 2 | 44100 | 3 | |
| 13 | Microphone (Realtek HD Audio Mic input) | 2 | 44100 | 3 | |
| 14 | Stereo Mix (Realtek HD Audio Stereo input) | 2 | 48000 | 3 | |
| 17 | PC Speaker (Realtek HD Audio output with HAP) | 2 | 44100 | 3 | |
| 18 | Microphone Array (Realtek HD Audio Mic Array input) | 2 | 44100 | 3 | |
| 24 | Headset (realme Buds T200x Hands-Free) | 1 | 16000 | 3 | |

### Speaker / headphone output devices (14 endpoints)
| Index | Name (as reported) | Ch | Rate | Host API | Default |
|---:|---|---:|---:|---:|---|
| 2 | Microsoft Sound Mapper - Output | 2 | 44100 | 0 | |
| 3 | Speakers (Realtek(R) Audio) | 2 | 44100 | 0 | **Yes** |
| 6 | Primary Sound Driver | 2 | 44100 | 1 | |
| 7 | Speakers (Realtek(R) Audio) | 2 | 44100 | 1 | |
| 8 | Speakers (Realtek(R) Audio) | 2 | 48000 | 2 | |
| 10 | Headphones 1 (Realtek HD Audio 2nd output with HAP) | 2 | 48000 | 3 | |
| 11 | Headphones 2 (Realtek HD Audio 2nd output with HAP) | 2 | 44100 | 3 | |
| 15 | Speakers 1 (Realtek HD Audio output with HAP) | 2 | 48000 | 3 | |
| 16 | Speakers 2 (Realtek HD Audio output with HAP) | 2 | 44100 | 3 | |
| 19 | Speakers (Nahimic Easy Surround) | 8 | 48000 | 3 | |
| 20 | Speakers (Nahimic mirroring Wave Speaker) | 2 | 44100 | 3 | |
| 21 | Headphones () | 2 | 48000 | 3 | |
| 22 | Speakers (Nahimic Wave Speaker) | 8 | 48000 | 3 | |
| 23 | Headset (realme Buds T200x Hands-Free) | 1 | 16000 | 3 | |

Jarvis never changes Windows sound settings. Picking a device in Settings only affects Jarvis.

---

## 6. Resource Usage (CPU, RAM & GPU VRAM)

Measured on 2026-11-01 by loading each voice model once in a fresh Python process (load time includes the first import of the library). RAM is the change in process resident memory.

| Component | Runs on | File size | Load time | RAM added | GPU VRAM added |
|---|---|---:|---:|---:|---:|
| `faster-whisper-small.en` (`int8`) | CPU | 463.6 MB (folder) | 1.71 s | 362 MB | 0 MB |
| `en_GB-alan-medium.onnx` (Piper) | CPU | 60.3 MB | 3.90 s | 19 MB | 0 MB |
| `hey_jarvis_v0.1.onnx` (openWakeWord) | CPU | 1.2 MB | 3.08 s | 139 MB | 0 MB |

- Total RAM added by the voice models: about **520 MB**.
- GPU memory in use was 450 MB before loading the voice models and 406 MB after. It did not go up, so the voice models use no VRAM (the small drop comes from other programs on the desktop).
- The LLM's own load time and VRAM use are recorded in `PHASE_1_REPORT.md` and were not re-measured for this phase.

---

## 7. Automated Test Results & Visual QA

Full test suite command:
```powershell
py -3.12 -m pytest -v
```
- **Total Tests Collected & Passed:** **250 passed in 42.78s** (0 failures)
  - **Phase 1–4 Regression Tests:** 225 passed
  - **Phase 5 Voice Suite (`tests/test_phase5_voice.py`):** 25 passed across 5 test classes:
    - `TestLocalSpeechToText` (8 tests): model availability, missing model handling, valid audio transcription with uncalibrated confidence metadata, empty audio rejection, silent recording detection, invalid format rejection, cancellation, and runtime error wrapping.
    - `TestLocalTextToSpeech` (7 tests): model availability & voice discovery, WAV synthesis, speed/volume configuration, empty text rejection, word-safe long text chunking, immediate playback interruption, and missing voice asset handling.
    - `TestWakeWordDetector` (5 tests): disabled by default, explicit enable/disable, positive/negative detection & duplicate suppression, sensitivity/timeout configuration, and startup reset guarantee after restart.
    - `TestAudioDeviceManagement` (4 tests): device discovery & default selection, safe switching by ID/name, missing device reporting, and disconnection/recording/playback error handling.
    - `TestVoiceChatIntegration` (1 end-to-end integration test): `/api/status` & `/api/voice/status` verification, explicit memory creation, `/api/voice/record` transcription, `/api/voice/transcript/edit`, `/api/chat` memory-augmented response, SQLite conversation persistence, `/api/voice/speak` playback, and offline enforcement.
- **Headless Chrome Visual QA:** Verified at Desktop (`1280×840`), Tablet (`768×1024`), and Mobile (`375×812`) viewports with `horizontal_overflow: false`, `modal_overflow: false`, and `0` console errors.

---

## 8. Real Hardware Verification Status

- **Status:** **Pending User Interactive Confirmation**
- Automated unit and integration tests use deterministic fake audio streams and synthetic/Piper WAV fixtures and therefore cannot confirm physical speaker audibility or physical microphone acoustics.
- An interactive hardware check script has been created at:
  ```powershell
  py -3.12 scripts/voice_hardware_check.py
  ```
- When run, `scripts/voice_hardware_check.py`:
  1. Lists all detected recording and playback devices.
  2. Synthesizes and plays a short test sentence through the selected speaker via local Piper TTS and asks you to confirm whether playback was clear.
  3. Records a 5-second microphone sample in RAM (never saved to disk), transcribes it locally with Faster-Whisper on CPU, prints the recognized transcript, and asks you to confirm accuracy.
  4. Optionally tests live `"Hey Jarvis"` wake-word detection.

---

## 9. Privacy Guarantees

1. **100% Local Processing:** All speech recognition (`faster-whisper`), speech synthesis (`piper-tts`), and wake-word detection (`openwakeword`) execute locally on CPU with `local_files_only=True`.
2. **No Cloud or Network Calls:** The `backend/offline.py` network guard stays on (`offline_mode = True` by default) and blocks outbound connections. `tests/test_offline.py` scans the backend for cloud LLM and Ollama code (e.g. `openai`, `ollama`, `anthropic`) and passes. The voice libraries are loaded with local file paths only.
3. **No Raw Audio Persistence:** Microphone recordings and synthesized speech buffers are held strictly in volatile RAM (`io.BytesIO` / `numpy.ndarray`) and are never written to disk unless explicitly requested.
4. **Wake-Word Disabled by Default & On Restart:** Wake-word monitoring is `False` by default, displays an explicit privacy notice in Settings, provides a one-click topbar disable toggle, and is forcibly reset to `False` whenever Jarvis starts or restarts.

---

## 10. Known Limitations

- **Whisper Confidence Scores:** `faster-whisper` exposes segment `avg_logprob` and `no_speech_prob`, which are uncalibrated log-likelihood heuristics rather than calibrated probabilities; the API and UI explicitly label them as `uncalibrated`.
- **Only one TTS voice is installed:** `C:\Users\Gurkirat Singh\models` holds just `en_GB-alan-medium.onnx` (22050 Hz). To add more voices, place an `.onnx` + `.onnx.json` pair in that folder or in the project's `models/` folder; Jarvis finds them automatically.
- **Duplicate device entries:** Each microphone and speaker appears several times in the device lists (once per Windows audio API). Picking the system default works; other entries may not open on every API.
- **Wake-word background listening is not exposed in the UI yet:** The topbar toggle turns the detector on and off, but the backend only starts continuous microphone monitoring when `start_background_stream=true` is sent to `/api/voice/wake-word`. A detected wake word changes the voice state; it does not yet start a recording by itself.
- **Physical hardware not yet confirmed:** See Section 8.
