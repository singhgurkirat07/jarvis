"""Automated test suite for JARVIS Phase 5: Local Voice Input, Speech Output & Wake-Word Activation."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.db import Database
from backend.services import SettingsService
from backend.voice import (
    AudioDeviceDisconnectedError,
    AudioDeviceManager,
    AudioDeviceUnavailableError,
    AudioPlaybackError,
    AudioRecordingError,
    EmptyAudioError,
    InvalidAudioFormatError,
    LocalSpeechToText,
    LocalTextToSpeech,
    SilentAudioError,
    SpeechRecognitionError,
    SpeechSynthesisError,
    TranscriptionCancelledError,
    VoiceError,
    VoiceModelMissingError,
    VoiceService,
    WakeWordDetector,
    WakeWordError,
    chunk_text_for_tts,
    compute_audio_metrics,
    encode_pcm16_to_wav,
    generate_silence_wav,
    generate_sine_wav,
    parse_wav_bytes,
)

MODELS_DIR = Path(r"C:\Users\Gurkirat Singh\models")
REAL_STT_PATH = str(MODELS_DIR / "faster-whisper-small.en")
REAL_TTS_PATH = str(MODELS_DIR / "en_GB-alan-medium.onnx")
REAL_TTS_CONFIG = str(MODELS_DIR / "en_GB-alan-medium.onnx.json")
REAL_WAKE_PATH = str(MODELS_DIR / "hey_jarvis_v0.1.onnx")


# ---------------------------------------------------------------------------
# Fake SoundDevice Module & Shared Real Model Fixtures
# ---------------------------------------------------------------------------

class _FakeDefaultAttr:
    def __init__(self, device_pair: tuple[int, int] = (0, 2)) -> None:
        self.device = device_pair


class FakeInputStream:
    def __init__(self, parent: "FakeSoundDeviceModule", samplerate: int, device: int, channels: int, dtype: str, blocksize: int) -> None:
        self.parent = parent
        self.samplerate = samplerate
        self.device = device
        self.channels = channels
        self.dtype = dtype
        self.blocksize = blocksize
        self.offset = 0

    def __enter__(self) -> "FakeInputStream":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def read(self, frames: int) -> tuple[np.ndarray, bool]:
        if self.parent.disconnect_during_record:
            raise RuntimeError("PortAudioError: Microphone device disconnected during capture")
        if self.parent.on_record_read_hook is not None:
            self.parent.on_record_read_hook()
        src = self.parent.recorded_samples
        if src is None or src.size == 0:
            chunk = np.zeros((frames, 1), dtype=np.int16)
            return chunk, False
        end = self.offset + frames
        if self.offset >= src.size:
            chunk_1d = np.zeros(frames, dtype=np.int16)
        elif end <= src.size:
            chunk_1d = src[self.offset:end]
        else:
            tail = src[self.offset:]
            pad = np.zeros(frames - tail.size, dtype=np.int16)
            chunk_1d = np.concatenate([tail, pad])
        self.offset = end
        return chunk_1d.reshape(-1, 1), False


class FakeOutputStream:
    def __init__(self, parent: "FakeSoundDeviceModule", samplerate: int, device: int, channels: int, dtype: str, blocksize: int) -> None:
        self.parent = parent
        self.samplerate = samplerate
        self.device = device
        self.channels = channels
        self.dtype = dtype
        self.blocksize = blocksize

    def __enter__(self) -> "FakeOutputStream":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def write(self, block: np.ndarray) -> None:
        if self.parent.disconnect_during_play:
            raise RuntimeError("PortAudioError: Output speaker disconnected during playback")
        arr = np.asarray(block, dtype=np.int16).reshape(-1)
        self.parent.played_blocks.append(
            {
                "device": self.device,
                "samplerate": self.samplerate,
                "samples": arr.copy(),
            }
        )
        if self.parent.on_play_write_hook is not None:
            self.parent.on_play_write_hook()


class FakeSoundDeviceModule:
    """Deterministic fake `sounddevice` module for automated tests without physical microphone/speaker interaction."""

    def __init__(self, recorded_samples: Optional[np.ndarray] = None) -> None:
        self.devices: List[Dict[str, Any]] = [
            {
                "name": "Fake Microphone Array",
                "max_input_channels": 2,
                "max_output_channels": 0,
                "default_samplerate": 16000.0,
                "hostapi": 0,
            },
            {
                "name": "Fake Studio USB Mic",
                "max_input_channels": 1,
                "max_output_channels": 0,
                "default_samplerate": 48000.0,
                "hostapi": 0,
            },
            {
                "name": "Fake Desktop Speakers",
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 22050.0,
                "hostapi": 0,
            },
            {
                "name": "Fake Wireless Headphones",
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
                "hostapi": 0,
            },
        ]
        self.default = _FakeDefaultAttr((0, 2))
        self.recorded_samples = recorded_samples
        self.played_blocks: List[Dict[str, Any]] = []
        self.fail_open_input: bool = False
        self.fail_open_output: bool = False
        self.disconnect_during_record: bool = False
        self.disconnect_during_play: bool = False
        self.on_record_read_hook: Optional[Callable[[], None]] = None
        self.on_play_write_hook: Optional[Callable[[], None]] = None

    def query_devices(self) -> List[Dict[str, Any]]:
        return list(self.devices)

    def InputStream(self, **kwargs: Any) -> FakeInputStream:
        if self.fail_open_input:
            raise RuntimeError("Simulated microphone open failure")
        return FakeInputStream(self, **kwargs)

    def OutputStream(self, **kwargs: Any) -> FakeOutputStream:
        if self.fail_open_output:
            raise RuntimeError("Simulated speaker open failure")
        return FakeOutputStream(self, **kwargs)


@pytest.fixture(scope="module")
def shared_real_tts() -> LocalTextToSpeech:
    tts = LocalTextToSpeech(model_path=REAL_TTS_PATH, config_path=REAL_TTS_CONFIG)
    tts._get_voice()
    return tts


@pytest.fixture(scope="module")
def shared_real_stt() -> LocalSpeechToText:
    stt = LocalSpeechToText(model_path=REAL_STT_PATH)
    stt.ensure_loaded()
    return stt


@pytest.fixture(scope="module")
def synthesized_speech_wav(shared_real_tts: LocalTextToSpeech) -> bytes:
    res = shared_real_tts.synthesize_to_wav_bytes("Hello Jarvis, please check the system status.")
    return res["wav_bytes"]


class FakeChatEngine:
    """Fast deterministic local Engine stub for Phase 5 voice-to-chat integration tests."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = "ready"
        self.error = None
        self.is_ready = True

    def load_in_background(self) -> None:
        return None

    def close(self) -> None:
        return None

    def cancel_active(self) -> bool:
        return False

    def status(self) -> Dict[str, Any]:
        return {
            "state": "ready",
            "error": None,
            "n_gpu_layers": -1,
            "n_ctx": 4096,
            "offload_kqv": False,
            "model": {"name": "qwen2.5-7b-instruct.gguf", "layers": 28},
        }

    def generate(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Dict[str, Any]:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        sys_msg = messages[0].get("content", "") if messages else ""
        mem_note = " (with memory context)" if "Explicit user memories" in sys_msg or "GPU temperature" in sys_msg else ""
        return {
            "role": "assistant",
            "content": f"Jarvis heard: {last_user}{mem_note}",
            "prompt_tokens": 24,
            "completion_tokens": 12,
            "total_tokens": 36,
            "seconds": 0.05,
            "tokens_per_second": 240.0,
            "decode_tokens_per_second": 240.0,
            "finish_reason": "stop",
        }


# ---------------------------------------------------------------------------
# 1. Speech Recognition Tests
# ---------------------------------------------------------------------------

class TestLocalSpeechToText:
    def test_stt_model_availability_and_initialization(self, shared_real_stt: LocalSpeechToText) -> None:
        avail = shared_real_stt.check_model_available()
        assert avail["available"] is True
        assert avail["has_model_bin"] is True
        assert avail["has_config_json"] is True
        assert avail["device"] == "cpu"
        assert avail["compute_type"] == "int8"

    def test_stt_missing_model_handled_gracefully(self, tmp_path: Path) -> None:
        missing_dir = tmp_path / "nonexistent_whisper"
        stt = LocalSpeechToText(model_path=missing_dir)
        avail = stt.check_model_available()
        assert avail["available"] is False
        assert avail["has_model_bin"] is False
        with pytest.raises(VoiceModelMissingError, match="Local speech-to-text model not found"):
            stt.ensure_loaded()

    def test_stt_valid_audio_input_generates_transcript_with_uncalibrated_confidence(
        self,
        shared_real_stt: LocalSpeechToText,
        synthesized_speech_wav: bytes,
    ) -> None:
        result = shared_real_stt.transcribe_wav_bytes(synthesized_speech_wav)
        assert isinstance(result["text"], str)
        assert "jarvis" in result["text"].lower() or "system" in result["text"].lower()
        assert result["is_silent"] is False
        assert result["language"] == "en"
        assert result["duration_seconds"] > 0.5
        conf = result["confidence"]
        assert conf["is_calibrated"] is False
        assert "uncalibrated" in conf["note"].lower()
        assert isinstance(conf["avg_logprob"], float)

    def test_stt_empty_audio_rejected(self, shared_real_stt: LocalSpeechToText) -> None:
        with pytest.raises(EmptyAudioError, match="empty"):
            shared_real_stt.transcribe_wav_bytes(b"")

    def test_stt_silent_recording_detected_and_handled(self, shared_real_stt: LocalSpeechToText) -> None:
        silent_wav = generate_silence_wav(duration_seconds=1.5, sample_rate=16000)
        res = shared_real_stt.transcribe_wav_bytes(silent_wav, raise_on_silence=False)
        assert res["is_silent"] is True
        assert res["text"] == ""
        with pytest.raises(SilentAudioError, match="silent"):
            shared_real_stt.transcribe_wav_bytes(silent_wav, raise_on_silence=True)

    def test_stt_invalid_audio_format_rejected(self, shared_real_stt: LocalSpeechToText) -> None:
        with pytest.raises(InvalidAudioFormatError, match="Invalid or unsupported audio format"):
            shared_real_stt.transcribe_wav_bytes(b"NOT_A_VALID_RIFF_WAV_FILE_HEADER_12345")

    def test_stt_cancellation_before_and_during_recognition(
        self,
        shared_real_stt: LocalSpeechToText,
        synthesized_speech_wav: bytes,
    ) -> None:
        cancel_ev = threading.Event()
        cancel_ev.set()
        with pytest.raises(TranscriptionCancelledError, match="cancelled"):
            shared_real_stt.transcribe_wav_bytes(synthesized_speech_wav, cancel_event=cancel_ev)

    def test_stt_recognition_runtime_error_wrapped_cleanly(self, tmp_path: Path) -> None:
        (tmp_path / "model.bin").write_text("dummy", encoding="utf-8")
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")

        class BrokenWhisperModel:
            def transcribe(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("CTranslate2 internal decoding failure")

        stt = LocalSpeechToText(model_path=tmp_path, model_factory=lambda _p: BrokenWhisperModel())
        valid_wav = generate_sine_wav(duration_seconds=1.0, sample_rate=16000)
        with pytest.raises(SpeechRecognitionError, match="Local speech transcription failed"):
            stt.transcribe_wav_bytes(valid_wav)


# ---------------------------------------------------------------------------
# 2. Text-to-Speech Tests
# ---------------------------------------------------------------------------

class TestLocalTextToSpeech:
    def test_tts_model_availability_and_voice_discovery(self, shared_real_tts: LocalTextToSpeech) -> None:
        avail = shared_real_tts.check_model_available()
        assert avail["available"] is True
        voices = shared_real_tts.discover_local_voices()
        assert len(voices) >= 1
        assert any("en_GB-alan-medium" in v["id"] for v in voices)

    def test_tts_speech_generation_produces_valid_wav(self, shared_real_tts: LocalTextToSpeech) -> None:
        out = shared_real_tts.synthesize_to_wav_bytes("Jarvis local speech synthesis is operational.")
        assert len(out["wav_bytes"]) > 1000
        assert out["sample_rate"] == 22050
        assert out["duration_seconds"] > 0.5
        assert out["chunk_count"] >= 1
        samples, sr, dur = parse_wav_bytes(out["wav_bytes"])
        assert sr == 22050
        assert dur > 0.5
        assert samples.size > 5000

    def test_tts_playback_configuration_speed_and_volume(self, shared_real_tts: LocalTextToSpeech) -> None:
        fake_sd = FakeSoundDeviceModule()
        audio_mgr = AudioDeviceManager(sd_module=fake_sd)
        fast = shared_real_tts.speak(
            "Testing speed and volume.",
            audio_devices=audio_mgr,
            speaker_selection="2",
            speed=1.5,
            volume=0.6,
        )
        slow = shared_real_tts.speak(
            "Testing speed and volume.",
            audio_devices=audio_mgr,
            speaker_selection="2",
            speed=0.7,
            volume=1.2,
        )
        assert fast["spoken"] is True
        assert slow["spoken"] is True
        # Slower speech has longer duration than faster speech for the same sentence
        assert slow["played_seconds"] > fast["played_seconds"]
        assert len(fake_sd.played_blocks) >= 2

    def test_tts_empty_text_rejected(self, shared_real_tts: LocalTextToSpeech) -> None:
        with pytest.raises(ValueError, match="empty"):
            shared_real_tts.synthesize_to_wav_bytes("   \n\t  ")

    def test_tts_long_text_chunking_never_cuts_words_mid_word(self) -> None:
        long_paragraph = (
            "Artificial intelligence running locally on your workstation guarantees complete privacy. "
            "Every single word in this long sentence must remain intact without ever being sliced in the middle "
            "when the text chunker splits paragraphs into manageable synthesis segments for Piper TTS."
        )
        chunks = chunk_text_for_tts(long_paragraph, max_chunk_chars=70)
        assert len(chunks) >= 3
        for chunk in chunks:
            assert len(chunk) <= 70
        rejoined_words = " ".join(chunks).split()
        original_words = long_paragraph.split()
        assert rejoined_words == original_words

    def test_tts_playback_interruption(self, shared_real_tts: LocalTextToSpeech) -> None:
        fake_sd = FakeSoundDeviceModule()
        stop_ev = threading.Event()
        fake_sd.on_play_write_hook = stop_ev.set
        audio_mgr = AudioDeviceManager(sd_module=fake_sd)

        res = shared_real_tts.speak(
            "First sentence is spoken. Second sentence should be interrupted immediately.",
            audio_devices=audio_mgr,
            speaker_selection="2",
            stop_event=stop_ev,
        )
        assert res["interrupted"] is True
        assert res["spoken"] is True

    def test_tts_missing_voice_assets_handled_gracefully(self, tmp_path: Path) -> None:
        tts = LocalTextToSpeech(
            model_path=tmp_path / "missing_voice.onnx",
            config_path=tmp_path / "missing_voice.onnx.json",
        )
        avail = tts.check_model_available()
        assert avail["available"] is False
        with pytest.raises(VoiceModelMissingError):
            tts.synthesize_to_wav_bytes("Hello world")


# ---------------------------------------------------------------------------
# 3. Wake-Word Detection Tests
# ---------------------------------------------------------------------------

class TestWakeWordDetector:
    def test_wake_word_disabled_by_default_and_model_available(self) -> None:
        detector = WakeWordDetector(model_path=REAL_WAKE_PATH)
        st = detector.status()
        assert st["enabled"] is False
        assert st["microphone_active"] is False
        assert st["model"]["available"] is True
        assert "microphone" in st["privacy_notice"].lower()

    def test_wake_word_explicit_enable_and_disable(self) -> None:
        fake_sd = FakeSoundDeviceModule()
        audio_mgr = AudioDeviceManager(sd_module=fake_sd)
        detector = WakeWordDetector(model_path=REAL_WAKE_PATH)
        st_enabled = detector.enable(
            audio_devices=audio_mgr,
            device_selection="0",
            start_background_stream=False,
        )
        assert st_enabled["enabled"] is True
        assert st_enabled["microphone_active"] is True
        st_disabled = detector.disable()
        assert st_disabled["enabled"] is False
        assert st_disabled["microphone_active"] is False

    def test_wake_word_positive_and_negative_detection_and_duplicate_suppression(self) -> None:
        class FakeOwwModel:
            def __init__(self) -> None:
                self.next_score = 0.1
                self.reset_called = 0

            def predict(self, frame: np.ndarray) -> Dict[str, float]:
                return {"hey_jarvis_v0.1": self.next_score}

            def reset(self) -> None:
                self.reset_called += 1

        fake_oww = FakeOwwModel()
        events: List[Dict[str, Any]] = []
        detector = WakeWordDetector(
            model_path=REAL_WAKE_PATH,
            detector_factory=lambda _p: fake_oww,
            on_detected=events.append,
            cooldown_seconds=2.0,
        )
        fake_sd = FakeSoundDeviceModule()
        audio_mgr = AudioDeviceManager(sd_module=fake_sd)
        detector.enable(
            audio_devices=audio_mgr,
            device_selection="0",
            sensitivity=0.5,
            timeout_seconds=10.0,
            start_background_stream=False,
        )

        frame = np.zeros(1280, dtype=np.int16)
        t0 = time.monotonic()
        # 1. Negative detection (score 0.1 < sensitivity 0.5)
        neg = detector.evaluate_frame(frame, now_monotonic=t0)
        assert neg["detected"] is False
        assert neg["reason"] == "below_threshold"
        assert len(events) == 0

        # 2. Positive detection (score 0.85 >= sensitivity 0.5)
        fake_oww.next_score = 0.85
        pos = detector.evaluate_frame(frame, now_monotonic=t0 + 1.0)
        assert pos["detected"] is True
        assert pos["score"] == pytest.approx(0.85)
        assert len(events) == 1
        assert detector.status()["detections_count"] == 1

        # 3. Duplicate activation prevented while command window is active
        dup = detector.evaluate_frame(frame, now_monotonic=t0 + 1.5)
        assert dup["detected"] is False
        assert dup["reason"] == "duplicate_suppressed_active_window"
        assert len(events) == 1

        # Complete command window, still within 2.0s cooldown at t0 + 2.0
        detector.complete_command_window()
        dup_cooldown = detector.evaluate_frame(frame, now_monotonic=t0 + 2.0)
        assert dup_cooldown["detected"] is False
        assert dup_cooldown["reason"] == "duplicate_suppressed_cooldown"

        # After cooldown expires at t0 + 4.0, positive detection succeeds again
        pos2 = detector.evaluate_frame(frame, now_monotonic=t0 + 4.0)
        assert pos2["detected"] is True
        assert len(events) == 2

    def test_wake_word_sensitivity_and_timeout_handling(self) -> None:
        detector = WakeWordDetector(
            model_path=REAL_WAKE_PATH,
            detector_factory=lambda _p: type(
                "M",
                (),
                {"predict": lambda self, f: {"hey_jarvis": 0.75}, "reset": lambda self: None},
            )(),
        )
        detector.configure(sensitivity=0.8, timeout_seconds=15.0)
        assert detector.sensitivity == pytest.approx(0.8)
        assert detector.timeout_seconds == pytest.approx(15.0)

        with pytest.raises(ValueError, match="wake_word_sensitivity"):
            detector.configure(sensitivity=0.999)
        with pytest.raises(ValueError, match="wake_word_timeout_seconds"):
            detector.configure(timeout_seconds=0.2)

        # Lower sensitivity to 0.5, trigger detection at t=50, then verify timeout at t=66
        fake_sd = FakeSoundDeviceModule()
        audio_mgr = AudioDeviceManager(sd_module=fake_sd)
        detector.enable(
            audio_devices=audio_mgr,
            device_selection="0",
            sensitivity=0.5,
            timeout_seconds=15.0,
            start_background_stream=False,
        )
        res = detector.evaluate_frame(np.zeros(1280, dtype=np.int16), now_monotonic=50.0)
        assert res["detected"] is True
        assert detector.check_inactivity_timeout(now_monotonic=60.0) is False
        assert detector.check_inactivity_timeout(now_monotonic=66.0) is True
        assert detector.status()["command_window_active"] is False

    def test_wake_word_never_starts_automatically_after_restart(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "restart_wake.db")
        s1 = Settings()
        s1.wake_word_enabled = True
        engine = FakeChatEngine(s1)
        # Persist wake_word_enabled = True in SQLite
        db.set_user_settings({"wake_word_enabled": True})

        # Simulate application restart: SettingsService.__init__ must reset wake_word_enabled=False
        s2 = Settings()
        svc2 = SettingsService(db=db, engine=engine, base_settings=s2)
        assert svc2.active_settings.wake_word_enabled is False
        assert svc2.saved_settings.wake_word_enabled is False
        assert db.get_user_settings().get("wake_word_enabled") is False
        db.close()


# ---------------------------------------------------------------------------
# 4. Audio Device Management Tests
# ---------------------------------------------------------------------------

class TestAudioDeviceManagement:
    def test_device_discovery_and_default_selection(self) -> None:
        fake_sd = FakeSoundDeviceModule()
        mgr = AudioDeviceManager(sd_module=fake_sd)
        mics = mgr.list_input_devices()
        speakers = mgr.list_output_devices()
        assert len(mics) == 2
        assert len(speakers) == 2

        default_mic = mgr.resolve_input_device("default")
        assert default_mic["index"] == 0
        assert default_mic["name"] == "Fake Microphone Array"

        default_spk = mgr.resolve_output_device("default")
        assert default_spk["index"] == 2
        assert default_spk["name"] == "Fake Desktop Speakers"

    def test_safe_device_switching_by_id_and_name(self) -> None:
        fake_sd = FakeSoundDeviceModule()
        mgr = AudioDeviceManager(sd_module=fake_sd)
        usb_mic = mgr.resolve_input_device("1")
        assert usb_mic["name"] == "Fake Studio USB Mic"

        by_name_mic = mgr.resolve_input_device("Studio USB")
        assert by_name_mic["index"] == 1

        headphones = mgr.resolve_output_device("Wireless Headphones")
        assert headphones["index"] == 3

    def test_missing_devices_reported_clearly(self) -> None:
        fake_sd = FakeSoundDeviceModule()
        mgr = AudioDeviceManager(sd_module=fake_sd)
        with pytest.raises(AudioDeviceUnavailableError, match="unavailable or disconnected"):
            mgr.resolve_input_device("99")
        with pytest.raises(AudioDeviceUnavailableError, match="unavailable or disconnected"):
            mgr.resolve_output_device("Nonexistent Speaker")

        fake_sd.devices = []
        with pytest.raises(AudioDeviceUnavailableError, match="No microphone input devices"):
            mgr.resolve_input_device("default")
        with pytest.raises(AudioDeviceUnavailableError, match="No speaker or headphone output devices"):
            mgr.resolve_output_device("default")

    def test_device_disconnection_and_recording_or_playback_failures(self) -> None:
        fake_sd = FakeSoundDeviceModule()
        mgr = AudioDeviceManager(sd_module=fake_sd)

        fake_sd.disconnect_during_record = True
        with pytest.raises(AudioDeviceDisconnectedError, match="disconnected"):
            mgr.record_wav(duration_seconds=0.5, sample_rate=16000, device_selection="0")

        fake_sd.disconnect_during_record = False
        fake_sd.fail_open_input = True
        with pytest.raises(AudioRecordingError, match="Could not open microphone"):
            mgr.record_wav(duration_seconds=0.5, sample_rate=16000, device_selection="0")

        fake_sd.fail_open_input = False
        fake_sd.fail_open_output = True
        wav = generate_sine_wav(duration_seconds=0.5, sample_rate=22050)
        with pytest.raises(AudioPlaybackError, match="Could not open speaker"):
            mgr.play_wav_bytes(wav, device_selection="2")

        fake_sd.fail_open_output = False
        fake_sd.disconnect_during_play = True
        with pytest.raises(AudioDeviceDisconnectedError, match="disconnected"):
            mgr.play_wav_bytes(wav, device_selection="2")


# ---------------------------------------------------------------------------
# 5. End-to-End Voice & Chat Integration Tests
# ---------------------------------------------------------------------------

class TestVoiceChatIntegration:
    @pytest.mark.asyncio
    async def test_full_voice_to_chat_memory_persistence_and_tts_flow(
        self,
        tmp_path: Path,
        shared_real_stt: LocalSpeechToText,
        shared_real_tts: LocalTextToSpeech,
        synthesized_speech_wav: bytes,
    ) -> None:
        import httpx
        from backend.engine import Engine

        settings = Settings()
        settings.db_path = str(tmp_path / "jarvis_phase5.db")
        settings.log_dir = str(tmp_path / "logs")
        settings.offline_mode = True
        settings.voice_input_enabled = True
        settings.voice_output_enabled = True

        samples_22k, orig_sr, _ = parse_wav_bytes(synthesized_speech_wav)
        from backend.voice import resample_int16
        samples_16k = resample_int16(samples_22k, orig_sr, 16000)

        fake_sd = FakeSoundDeviceModule(recorded_samples=samples_16k)
        audio_mgr = AudioDeviceManager(sd_module=fake_sd)
        wake_detector = WakeWordDetector(model_path=REAL_WAKE_PATH)

        voice_service = VoiceService(
            settings_provider=lambda: settings,
            audio_devices=audio_mgr,
            stt=shared_real_stt,
            tts=shared_real_tts,
            wake_word=wake_detector,
        )
        fake_engine = Engine(settings)
        fake_engine.status.state = "ready"
        fake_engine.close = lambda: None  # type: ignore[method-assign]

        def fake_generate(messages: List[Dict[str, str]], **kwargs: Any) -> Dict[str, Any]:
            last_user = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user = m.get("content", "")
                    break
            sys_msg = messages[0].get("content", "") if messages else ""
            mem_note = " (with memory context)" if "GPU temperature" in sys_msg else ""
            return {
                "role": "assistant",
                "content": f"Jarvis heard: {last_user}{mem_note}",
                "prompt_tokens": 24,
                "completion_tokens": 12,
                "total_tokens": 36,
                "seconds": 0.05,
                "tokens_per_second": 240.0,
                "decode_tokens_per_second": 240.0,
                "finish_reason": "stop",
            }

        fake_engine.generate = fake_generate  # type: ignore[method-assign]
        app = create_app(
            settings=settings,
            engine=fake_engine,
            load_model=False,
            voice_service=voice_service,
        )

        async with app.router.lifespan_context(app):
            voice_service.settings_provider = lambda: app.state.settings_service.active_settings
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
                # 1. Verify status includes voice state and wake_word is disabled by default
                st_res = await client.get("/api/status")
                assert st_res.status_code == 200
                st_body = st_res.json()
                assert st_body["voice_state"] == "Idle"
                assert st_body["wake_word_enabled"] is False
                assert st_body["settings"]["offline_mode"] is True

                vst_res = await client.get("/api/voice/status")
                assert vst_res.status_code == 200
                vst_body = vst_res.json()
                assert vst_body["state"] == "Idle"
                assert vst_body["wake_word"]["enabled"] is False

                # 2. Save an explicit memory to verify voice transcripts retrieve long-term memory
                mem_res = await client.post(
                    "/api/memories",
                    json={
                        "content": "System status check requires inspecting GPU temperature.",
                        "category": "preference",
                        "importance": 5,
                    },
                )
                assert mem_res.status_code == 201

                # 3. Record & transcribe via /api/voice/record (using FakeSoundDeviceModule with real Whisper CPU)
                rec_res = await client.post("/api/voice/record", json={"duration_seconds": 3.0})
                assert rec_res.status_code == 200
                rec_data = rec_res.json()
                assert "jarvis" in rec_data["text"].lower() or "system" in rec_data["text"].lower()
                assert rec_data["state"] == "WaitingForConfirmation"
                assert rec_data["waiting_for_confirmation"] is True

                # 4. Edit transcript before submission via /api/voice/transcript/edit
                edited_text = "Hello Jarvis, please check the system status and GPU temperature."
                edit_res = await client.post("/api/voice/transcript/edit", json={"text": edited_text})
                assert edit_res.status_code == 200
                assert edit_res.json()["pending_transcript"]["text"] == edited_text

                # 5. Submit the confirmed voice transcript into the existing /api/chat pipeline
                chat_res = await client.post(
                    "/api/chat",
                    json={
                        "messages": [{"role": "user", "content": edited_text}],
                        "stream": False,
                    },
                )
                assert chat_res.status_code == 200
                chat_data = chat_res.json()
                conv_id = chat_data["conversation_id"]
                assert edited_text in chat_data["content"]
                assert len(chat_data["retrieved_memories"]) >= 1

                # 6. Verify conversation persistence in SQLite
                conv_res = await client.get(f"/api/conversations/{conv_id}")
                assert conv_res.status_code == 200
                msgs = conv_res.json()["messages"]
                assert len(msgs) == 2
                assert msgs[0]["role"] == "user"
                assert msgs[0]["content"] == edited_text
                assert msgs[1]["role"] == "assistant"

                # 7. Speak assistant response via /api/voice/speak
                spk_res = await client.post(
                    "/api/voice/speak",
                    json={"text": chat_data["content"], "force": True},
                )
                assert spk_res.status_code == 200
                spk_data = spk_res.json()
                assert spk_data["spoken"] is True
                assert spk_data["played_seconds"] > 0.5
                assert len(fake_sd.played_blocks) >= 1

                # 8. Verify offline operation maintained throughout
                final_settings = (await client.get("/api/settings")).json()
                assert final_settings["active"]["offline_mode"] is True
