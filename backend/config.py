"""Settings: config/settings.json, overridable by SQLite user_settings and JARVIS_<NAME> env vars."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .model_file import validate_model

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "settings.json"

VALID_THEMES = ("dark", "midnight", "light")
VALID_NEW_CONV_BEHAVIORS = ("resume_last", "start_fresh")

# Changing any of these requires reloading the llama.cpp model to take effect in the engine.
MODEL_RELOAD_FIELDS = ("model_path", "n_gpu_layers", "n_ctx", "n_batch", "n_threads",
                       "use_mmap", "offload_kqv", "flash_attn")

# User-editable settings exposed through the settings API and UI.
USER_EDITABLE_FIELDS = (
    "model_path", "n_gpu_layers", "n_ctx", "temperature", "top_p",
    "max_tokens", "repeat_penalty", "system_prompt",
    "theme", "stream_responses", "conversation_history_enabled",
    "memory_enabled", "default_new_conversation_behavior",
    "offline_mode", "tools_enabled", "max_tool_calls_per_turn",
    "scheduler_enabled", "scheduler_poll_seconds", "max_concurrent_tasks",
    "voice_input_enabled", "voice_output_enabled", "wake_word_enabled",
    "selected_microphone", "selected_speaker", "selected_tts_voice",
    "speech_speed", "speech_volume", "listening_duration",
    "wake_word_sensitivity", "wake_word_timeout_seconds",
    "auto_submit_transcript", "stop_speaking_on_input",
)


class SettingsValidationError(ValueError):
    """Raised when a user setting fails validation."""


@dataclass
class Settings:
    model_path: str = ""
    n_gpu_layers: str | int = "auto"   # "auto", -1 (all), 0 (CPU only) or a layer count
    # Must stay False with llama-cpp-python 0.3.4 on RTX 50xx (sm_120): True without flash_attn
    # crashes the process with "CUDA error: an illegal memory access". See PHASE_1_REPORT.md.
    offload_kqv: bool = False
    flash_attn: bool = False
    n_ctx: int = 4096
    n_batch: int = 512
    n_threads: int | None = None
    use_mmap: bool = True
    vram_reserve_mb: int = 1024

    max_tokens: int = 512
    temperature: float = 0.7
    top_p: float = 0.9
    repeat_penalty: float = 1.1
    system_prompt: str = "You are Jarvis, a helpful offline assistant."

    # Phase 2 application settings
    theme: str = "dark"
    stream_responses: bool = True
    conversation_history_enabled: bool = True
    memory_enabled: bool = True
    default_new_conversation_behavior: str = "resume_last"

    # Phase 3 tool-calling, workspace & online/offline settings
    offline_mode: bool = True
    tools_enabled: bool = True
    max_tool_calls_per_turn: int = 3
    workspace_dir: str = "workspace"
    approval_ttl_seconds: int = 300

    # Phase 4 task engine & scheduler settings
    scheduler_enabled: bool = True
    scheduler_poll_seconds: float = 2.0
    max_concurrent_tasks: int = 2
    task_lease_seconds: float = 120.0
    missed_execution_threshold_seconds: float = 300.0

    # Phase 5 local voice input, speech output & wake-word settings
    voice_input_enabled: bool = True
    voice_output_enabled: bool = False
    wake_word_enabled: bool = False
    stt_model_path: str = r"C:\Users\Gurkirat Singh\models\faster-whisper-small.en"
    tts_model_path: str = r"C:\Users\Gurkirat Singh\models\en_GB-alan-medium.onnx"
    tts_config_path: str = r"C:\Users\Gurkirat Singh\models\en_GB-alan-medium.onnx.json"
    wake_word_model_path: str = r"C:\Users\Gurkirat Singh\models\hey_jarvis_v0.1.onnx"
    selected_microphone: str = "default"
    selected_speaker: str = "default"
    selected_tts_voice: str = "en_GB-alan-medium"
    speech_speed: float = 1.0
    speech_volume: float = 1.0
    listening_duration: float = 8.0
    wake_word_sensitivity: float = 0.5
    wake_word_timeout_seconds: float = 10.0
    auto_submit_transcript: bool = False
    stop_speaking_on_input: bool = True

    host: str = "127.0.0.1"
    port: int = 8000
    allow_remote: bool = False
    enforce_offline: bool = True
    queue_timeout_s: float = 120

    db_path: str = "data/jarvis.db"
    log_dir: str = "logs"
    log_level: str = "INFO"

    def resolve(self, rel: str) -> Path:
        """Resolve a project-relative path (db, logs) against the project root."""
        p = Path(rel)
        return p if p.is_absolute() else ROOT / p

    def public_dict(self) -> dict:
        """Return all user-visible settings."""
        return {k: getattr(self, k) for k in USER_EDITABLE_FIELDS + ("offload_kqv", "flash_attn", "n_batch", "vram_reserve_mb")}

    def copy(self) -> "Settings":
        return Settings(**asdict(self))


def _coerce(name: str, raw, current):
    if name == "n_gpu_layers":
        if isinstance(raw, bool):
            raise SettingsValidationError("n_gpu_layers must be 'auto' or an integer")
        s = str(raw).strip().lower()
        if s == "auto":
            return "auto"
        try:
            return int(s)
        except ValueError as e:
            raise SettingsValidationError(f"Invalid n_gpu_layers value: {raw!r}") from e
    if name == "n_threads":
        return None if raw in (None, "", "null", "none") else int(raw)
    if isinstance(current, bool):
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
        raise SettingsValidationError(f"Invalid boolean for {name}: {raw!r}")
    if isinstance(current, int):
        if isinstance(raw, bool):
            raise SettingsValidationError(f"Expected integer for {name}, got boolean")
        return int(raw)
    if isinstance(current, float):
        if isinstance(raw, bool):
            raise SettingsValidationError(f"Expected float for {name}, got boolean")
        return float(raw)
    return str(raw)


def validate_settings_values(candidate: Settings, check_model_file: bool = False) -> None:
    """Validate candidate settings values before saving or applying them."""
    if check_model_file or candidate.model_path:
        if check_model_file:
            validate_model(candidate.model_path)
        elif not str(candidate.model_path).strip():
            raise SettingsValidationError("model_path must not be empty")

    if candidate.n_gpu_layers != "auto":
        if not isinstance(candidate.n_gpu_layers, int) or candidate.n_gpu_layers < -1 or candidate.n_gpu_layers > 256:
            raise SettingsValidationError("n_gpu_layers must be 'auto' or an integer between -1 and 256")

    if not (256 <= candidate.n_ctx <= 32768):
        raise SettingsValidationError("n_ctx must be between 256 and 32768")

    if not (1 <= candidate.max_tokens <= 8192):
        raise SettingsValidationError("max_tokens must be between 1 and 8192")

    if candidate.max_tokens >= candidate.n_ctx:
        raise SettingsValidationError(
            f"max_tokens ({candidate.max_tokens}) must be smaller than n_ctx ({candidate.n_ctx})"
        )

    if not (0.0 <= candidate.temperature <= 2.0):
        raise SettingsValidationError("temperature must be between 0.0 and 2.0")

    if not (0.0 < candidate.top_p <= 1.0):
        raise SettingsValidationError("top_p must be greater than 0.0 and at most 1.0")

    if not (0.5 <= candidate.repeat_penalty <= 2.5):
        raise SettingsValidationError("repeat_penalty must be between 0.5 and 2.5")

    if not candidate.system_prompt or not candidate.system_prompt.strip():
        raise SettingsValidationError("system_prompt must not be blank")
    if len(candidate.system_prompt) > 8000:
        raise SettingsValidationError("system_prompt must not exceed 8000 characters")

    if candidate.theme not in VALID_THEMES:
        raise SettingsValidationError(f"theme must be one of {VALID_THEMES}")

    if candidate.default_new_conversation_behavior not in VALID_NEW_CONV_BEHAVIORS:
        raise SettingsValidationError(
            f"default_new_conversation_behavior must be one of {VALID_NEW_CONV_BEHAVIORS}"
        )

    if not (1 <= candidate.max_tool_calls_per_turn <= 8):
        raise SettingsValidationError("max_tool_calls_per_turn must be between 1 and 8")

    if not (0.05 <= candidate.scheduler_poll_seconds <= 3600.0):
        raise SettingsValidationError("scheduler_poll_seconds must be between 0.05 and 3600.0")

    if not (1 <= candidate.max_concurrent_tasks <= 8):
        raise SettingsValidationError("max_concurrent_tasks must be between 1 and 8")

    if not (0.5 <= candidate.speech_speed <= 2.0):
        raise SettingsValidationError("speech_speed must be between 0.5 and 2.0")

    if not (0.0 <= candidate.speech_volume <= 2.0):
        raise SettingsValidationError("speech_volume must be between 0.0 and 2.0")

    if not (1.0 <= candidate.listening_duration <= 60.0):
        raise SettingsValidationError("listening_duration must be between 1.0 and 60.0 seconds")

    if not (0.05 <= candidate.wake_word_sensitivity <= 0.99):
        raise SettingsValidationError("wake_word_sensitivity must be between 0.05 and 0.99")

    if not (1.0 <= candidate.wake_word_timeout_seconds <= 120.0):
        raise SettingsValidationError("wake_word_timeout_seconds must be between 1.0 and 120.0 seconds")

    for dev_field in ("selected_microphone", "selected_speaker", "selected_tts_voice"):
        val = getattr(candidate, dev_field, "")
        if not isinstance(val, str) or not val.strip() or len(val) > 256:
            raise SettingsValidationError(f"{dev_field} must be a non-empty string up to 256 characters")

    # Guard against the known RTX 5050 CUDA crash when offload_kqv=True and flash_attn=False
    if candidate.offload_kqv and not candidate.flash_attn:
        raise SettingsValidationError(
            "offload_kqv=true without flash_attn=true is unsafe on RTX 5050 (Blackwell sm_120) "
            "with llama-cpp-python 0.3.4 and causes a CUDA illegal memory access crash."
        )


def load_settings(path: str | Path | None = None, **overrides) -> Settings:
    s = Settings()
    path = Path(path or os.environ.get("JARVIS_CONFIG") or DEFAULT_CONFIG)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    names = {f.name for f in fields(Settings)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"Unknown keys in {path}: {sorted(unknown)}")
    for f in fields(Settings):
        value = data.get(f.name, getattr(s, f.name))
        env = os.environ.get(f"JARVIS_{f.name.upper()}")
        if env is not None:
            value = env
        if f.name in overrides:
            value = overrides[f.name]
        setattr(s, f.name, _coerce(f.name, value, getattr(Settings, f.name, value)))
    return s
