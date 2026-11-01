"""Phase 5 Local Voice Interface: Audio Device Manager, Faster-Whisper STT, Piper TTS & openWakeWord."""
from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import struct
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .config import ROOT, Settings

log = logging.getLogger("jarvis.voice")

VOICE_STATES = (
    "Idle",
    "Listening",
    "Recording",
    "Transcribing",
    "WaitingForConfirmation",
    "Thinking",
    "Speaking",
    "Interrupted",
    "Error",
)

WAKE_WORD_PRIVACY_NOTICE = (
    "When enabled, your microphone remains active locally on CPU solely to detect the wake phrase "
    "'Hey Jarvis'. Speech recognition only starts after the wake word is detected. No audio ever "
    "leaves your device or is saved to disk."
)


# ---- Exceptions -------------------------------------------------------------
class VoiceError(RuntimeError):
    """Base error for local voice, audio device, STT, TTS, and wake-word operations."""


class VoiceModelMissingError(VoiceError):
    """Raised when a required local STT, TTS, or wake-word model file is missing on disk."""


class EmptyAudioError(VoiceError, ValueError):
    """Raised when an audio buffer is empty (0 bytes or 0 samples)."""


class SilentAudioError(VoiceError):
    """Raised when recorded audio contains only silence below the detection threshold."""


class InvalidAudioFormatError(VoiceError, ValueError):
    """Raised when audio bytes are corrupt or in an unsupported format."""


class AudioDeviceUnavailableError(VoiceError):
    """Raised when a requested microphone or speaker device does not exist or has no channels."""


class AudioDeviceDisconnectedError(AudioDeviceUnavailableError):
    """Raised when an active microphone or speaker is disconnected during operation."""


class AudioRecordingError(VoiceError):
    """Raised when recording from a microphone fails."""


class AudioPlaybackError(VoiceError):
    """Raised when playing audio to an output device fails."""


class TranscriptionCancelledError(VoiceError):
    """Raised when recording or speech recognition is cancelled by the user."""


class SpeechRecognitionError(VoiceError):
    """Raised when local speech-to-text fails."""


class SpeechSynthesisError(VoiceError):
    """Raised when local text-to-speech synthesis fails."""


class WakeWordError(VoiceError):
    """Raised when wake-word initialization or monitoring fails."""


# ---- Audio / WAV Helpers (In-Memory Only) -----------------------------------
def encode_pcm16_to_wav(samples: np.ndarray | bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Encode 16-bit PCM mono/stereo samples into an in-memory RIFF WAV byte buffer."""
    if isinstance(samples, np.ndarray):
        arr = np.asarray(samples, dtype=np.int16)
        pcm_bytes = arr.tobytes()
    elif isinstance(samples, (bytes, bytearray, memoryview)):
        pcm_bytes = bytes(samples)
    else:
        raise InvalidAudioFormatError("Unsupported sample buffer type for WAV encoding")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(max(1, int(channels)))
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def generate_sine_wav(
    frequency_hz: float = 440.0,
    duration_seconds: float = 1.0,
    sample_rate: int = 16000,
    amplitude: float = 0.4,
) -> bytes:
    """Generate a synthetic in-memory 16-bit mono WAV sine wave (used for tests and tone checks)."""
    n_samples = max(1, int(round(duration_seconds * sample_rate)))
    t = np.linspace(0.0, duration_seconds, n_samples, endpoint=False, dtype=np.float32)
    wave_f32 = float(amplitude) * np.sin(2.0 * math.pi * float(frequency_hz) * t)
    pcm16 = np.clip(wave_f32 * 32767.0, -32768, 32767).astype(np.int16)
    return encode_pcm16_to_wav(pcm16, sample_rate=sample_rate, channels=1)


def generate_silence_wav(duration_seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate a silent in-memory 16-bit mono WAV buffer."""
    n_samples = max(1, int(round(duration_seconds * sample_rate)))
    pcm16 = np.zeros(n_samples, dtype=np.int16)
    return encode_pcm16_to_wav(pcm16, sample_rate=sample_rate, channels=1)


def parse_wav_bytes(audio_bytes: bytes) -> tuple[np.ndarray, int, float]:
    """Validate and parse RIFF WAV bytes into `(mono_int16_samples, sample_rate, duration_seconds)`."""
    if not audio_bytes or len(audio_bytes) == 0:
        raise EmptyAudioError("Audio input is empty (0 bytes).")
    if len(audio_bytes) < 44 or not audio_bytes.startswith(b"RIFF") or b"WAVE" not in audio_bytes[:16]:
        raise InvalidAudioFormatError(
            "Invalid or unsupported audio format: expected a valid RIFF/WAVE audio stream."
        )
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            if channels < 1 or sample_rate <= 0:
                raise InvalidAudioFormatError("WAV header specifies invalid channel count or sample rate.")
            if n_frames == 0:
                raise EmptyAudioError("WAV audio stream contains 0 frames.")
            raw = wf.readframes(n_frames)
    except (wave.Error, EOFError, struct.error) as e:
        raise InvalidAudioFormatError(f"Corrupt WAV audio stream: {e}") from e

    if not raw:
        raise EmptyAudioError("WAV audio data payload is empty.")

    if sampwidth == 2:
        samples = np.frombuffer(raw, dtype=np.int16)
    elif sampwidth == 1:
        u8 = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
        samples = (u8 - 128) * 256
    elif sampwidth == 4:
        i32 = np.frombuffer(raw, dtype=np.int32)
        samples = (i32 >> 16).astype(np.int16)
    else:
        raise InvalidAudioFormatError(f"Unsupported WAV sample width: {sampwidth} bytes.")

    if samples.size == 0:
        raise EmptyAudioError("WAV audio contains 0 decoded samples.")

    if channels > 1:
        usable = (samples.size // channels) * channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1).astype(np.int16)

    duration_s = float(samples.size) / float(sample_rate)
    return samples, int(sample_rate), duration_s


def compute_audio_metrics(
    samples_int16: np.ndarray,
    *,
    silence_rms_threshold: float = 0.0025,
    silence_peak_threshold: float = 0.01,
) -> dict[str, Any]:
    """Compute normalized RMS and peak amplitude in `[0.0, 1.0]` and detect silence."""
    if samples_int16.size == 0:
        return {"rms": 0.0, "peak": 0.0, "is_silent": True}
    f32 = samples_int16.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(np.square(f32))))
    peak = float(np.max(np.abs(f32)))
    is_silent = bool(rms < silence_rms_threshold and peak < silence_peak_threshold)
    return {
        "rms": round(rms, 5),
        "peak": round(peak, 5),
        "is_silent": is_silent,
    }


def resample_int16(samples: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Linearly resample 1D int16 audio from `orig_sr` to `target_sr` when needed."""
    if orig_sr == target_sr or samples.size == 0:
        return samples
    duration = samples.size / float(orig_sr)
    target_len = max(1, int(round(duration * target_sr)))
    x_old = np.linspace(0.0, 1.0, samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, target_len, endpoint=False)
    interp = np.interp(x_new, x_old, samples.astype(np.float32))
    return np.clip(interp, -32768, 32767).astype(np.int16)


# ---- 1. Audio Device Management Service (Section 6) -------------------------
class AudioDeviceManager:
    """Manages local microphones and speakers safely without altering OS sound settings."""

    def __init__(self, sd_module: Any = None):
        self._sd = sd_module
        self._io_lock = threading.RLock()
        self._active_playback_stop: threading.Event | None = None

    def _get_sd(self) -> Any:
        if self._sd is not None:
            return self._sd
        import sounddevice as sd
        return sd

    def _query_raw_devices(self) -> tuple[list[dict[str, Any]], tuple[int, int]]:
        sd = self._get_sd()
        try:
            raw_list = list(sd.query_devices())
            default_pair = getattr(sd.default, "device", (-1, -1))
            try:
                def_in = int(default_pair[0]) if default_pair[0] is not None else -1
            except Exception:
                def_in = -1
            try:
                def_out = int(default_pair[1]) if default_pair[1] is not None else -1
            except Exception:
                def_out = -1
            return raw_list, (def_in, def_out)
        except Exception as e:
            raise AudioDeviceUnavailableError(f"Failed to query local audio devices: {e}") from e

    @staticmethod
    def _clean_device_name(name: str) -> str:
        cleaned = re.sub(r"[\r\n]+", " ", str(name or "")).strip()
        return cleaned or "Unnamed Audio Device"

    def list_input_devices(self) -> list[dict[str, Any]]:
        raw_list, (def_in, _) = self._query_raw_devices()
        out: list[dict[str, Any]] = []
        for idx, dev in enumerate(raw_list):
            ch = int(dev.get("max_input_channels", 0) or 0)
            if ch <= 0:
                continue
            name = self._clean_device_name(dev.get("name", f"Input {idx}"))
            out.append({
                "index": idx,
                "id": str(idx),
                "name": name,
                "channels": ch,
                "default_sample_rate": float(dev.get("default_samplerate", 16000.0) or 16000.0),
                "hostapi": int(dev.get("hostapi", 0) or 0),
                "is_default": idx == def_in,
            })
        if out and not any(d["is_default"] for d in out):
            out[0]["is_default"] = True
        return out

    def list_output_devices(self) -> list[dict[str, Any]]:
        raw_list, (_, def_out) = self._query_raw_devices()
        out: list[dict[str, Any]] = []
        for idx, dev in enumerate(raw_list):
            ch = int(dev.get("max_output_channels", 0) or 0)
            if ch <= 0:
                continue
            name = self._clean_device_name(dev.get("name", f"Output {idx}"))
            out.append({
                "index": idx,
                "id": str(idx),
                "name": name,
                "channels": ch,
                "default_sample_rate": float(dev.get("default_samplerate", 44100.0) or 44100.0),
                "hostapi": int(dev.get("hostapi", 0) or 0),
                "is_default": idx == def_out,
            })
        if out and not any(d["is_default"] for d in out):
            out[0]["is_default"] = True
        return out

    def resolve_input_device(self, selection: str | int | None = "default") -> dict[str, Any]:
        devices = self.list_input_devices()
        if not devices:
            raise AudioDeviceUnavailableError("No microphone input devices are available on this system.")
        sel = "default" if selection is None else str(selection).strip()
        if not sel or sel.lower() == "default":
            return next((d for d in devices if d["is_default"]), devices[0])
        for d in devices:
            if d["id"] == sel or str(d["index"]) == sel:
                return d
        sel_lower = sel.lower()
        for d in devices:
            if sel_lower in d["name"].lower():
                return d
        raise AudioDeviceUnavailableError(
            f"Selected microphone {selection!r} is unavailable or disconnected."
        )

    def resolve_output_device(self, selection: str | int | None = "default") -> dict[str, Any]:
        devices = self.list_output_devices()
        if not devices:
            raise AudioDeviceUnavailableError("No speaker or headphone output devices are available on this system.")
        sel = "default" if selection is None else str(selection).strip()
        if not sel or sel.lower() == "default":
            return next((d for d in devices if d["is_default"]), devices[0])
        for d in devices:
            if d["id"] == sel or str(d["index"]) == sel:
                return d
        sel_lower = sel.lower()
        for d in devices:
            if sel_lower in d["name"].lower():
                return d
        raise AudioDeviceUnavailableError(
            f"Selected speaker/headphone device {selection!r} is unavailable or disconnected."
        )

    def stop_playback(self) -> None:
        ev = self._active_playback_stop
        if ev is not None:
            ev.set()

    def record_wav(
        self,
        duration_seconds: float = 5.0,
        *,
        sample_rate: int = 16000,
        device_selection: str | int | None = "default",
        cancel_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
        level_callback: Callable[[float], None] | None = None,
    ) -> bytes:
        """Record mono 16-bit PCM audio from the selected microphone into an in-memory WAV buffer."""
        if duration_seconds <= 0.0 or duration_seconds > 60.0:
            raise ValueError("Recording duration must be between 0.1 and 60.0 seconds")

        # Avoid simultaneous playback and recording
        self.stop_playback()

        dev = self.resolve_input_device(device_selection)
        sd = self._get_sd()
        blocksize = 1024
        max_blocks = max(1, int(math.ceil((duration_seconds * sample_rate) / blocksize)))
        frames: list[np.ndarray] = []

        with self._io_lock:
            try:
                stream = sd.InputStream(
                    samplerate=sample_rate,
                    device=dev["index"],
                    channels=1,
                    dtype="int16",
                    blocksize=blocksize,
                )
            except Exception as e:
                raise AudioRecordingError(
                    f"Could not open microphone {dev['name']!r} (index {dev['index']}): {e}"
                ) from e

            try:
                with stream:
                    for _ in range(max_blocks):
                        if cancel_event is not None and cancel_event.is_set():
                            raise TranscriptionCancelledError("Microphone recording was cancelled by the user.")
                        if stop_event is not None and stop_event.is_set():
                            break
                        try:
                            chunk, _overflowed = stream.read(blocksize)
                        except Exception as e:
                            raise AudioDeviceDisconnectedError(
                                f"Microphone {dev['name']!r} disconnected or failed during recording: {e}"
                            ) from e
                        arr = np.asarray(chunk, dtype=np.int16).reshape(-1)
                        frames.append(arr.copy())
                        if level_callback is not None and arr.size > 0:
                            rms = float(np.sqrt(np.mean(np.square(arr.astype(np.float32) / 32768.0))))
                            level_callback(min(1.0, round(rms * 4.0, 4)))
            finally:
                if level_callback is not None:
                    level_callback(0.0)

        if cancel_event is not None and cancel_event.is_set():
            raise TranscriptionCancelledError("Microphone recording was cancelled by the user.")

        if not frames:
            raise EmptyAudioError("No audio frames were captured from the microphone.")

        all_samples = np.concatenate(frames)
        max_samples = int(round(duration_seconds * sample_rate))
        if all_samples.size > max_samples:
            all_samples = all_samples[:max_samples]
        return encode_pcm16_to_wav(all_samples, sample_rate=sample_rate, channels=1)

    def play_wav_bytes(
        self,
        wav_bytes: bytes,
        *,
        device_selection: str | int | None = "default",
        volume: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Play in-memory WAV bytes through the selected output device with immediate stop support."""
        samples, sample_rate, duration_s = parse_wav_bytes(wav_bytes)
        dev = self.resolve_output_device(device_selection)
        sd = self._get_sd()

        if volume != 1.0:
            scaled = np.clip(samples.astype(np.float32) * float(volume), -32768, 32767).astype(np.int16)
        else:
            scaled = samples

        local_stop = stop_event or threading.Event()
        self._active_playback_stop = local_stop
        blocksize = 2048
        interrupted = False
        played_samples = 0

        with self._io_lock:
            try:
                stream = sd.OutputStream(
                    samplerate=sample_rate,
                    device=dev["index"],
                    channels=1,
                    dtype="int16",
                    blocksize=blocksize,
                )
            except Exception as e:
                raise AudioPlaybackError(
                    f"Could not open speaker/headphone device {dev['name']!r} (index {dev['index']}): {e}"
                ) from e

            try:
                with stream:
                    for offset in range(0, scaled.size, blocksize):
                        if local_stop.is_set():
                            interrupted = True
                            break
                        block = scaled[offset : offset + blocksize].reshape(-1, 1)
                        try:
                            stream.write(block)
                            played_samples += block.shape[0]
                        except Exception as e:
                            raise AudioDeviceDisconnectedError(
                                f"Speaker/headphone device {dev['name']!r} disconnected or failed during playback: {e}"
                            ) from e
            finally:
                if self._active_playback_stop is local_stop:
                    self._active_playback_stop = None

        return {
            "device": dev,
            "sample_rate": sample_rate,
            "duration_seconds": round(duration_s, 3),
            "played_seconds": round(played_samples / float(sample_rate), 3),
            "interrupted": interrupted,
        }


# ---- 2. Local Speech-to-Text Service (Faster-Whisper on CPU, Section 3) -----
class LocalSpeechToText:
    """Offline Speech-to-Text powered by Faster-Whisper on CPU (int8) with zero GPU VRAM usage."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        model_factory: Callable[[str], Any] | None = None,
        max_duration_seconds: float = 60.0,
        silence_rms_threshold: float = 0.0025,
    ):
        self.model_path = Path(model_path)
        self._model_factory = model_factory
        self.max_duration_seconds = float(max_duration_seconds)
        self.silence_rms_threshold = float(silence_rms_threshold)
        self._model: Any = None
        self._lock = threading.RLock()
        self.load_seconds: float | None = None

    def check_model_available(self) -> dict[str, Any]:
        p = self.model_path
        exists = p.exists() and p.is_dir()
        model_bin = (p / "model.bin") if exists else None
        config_json = (p / "config.json") if exists else None
        ok = bool(exists and model_bin and model_bin.is_file() and config_json and config_json.is_file())
        return {
            "available": ok,
            "engine": "faster-whisper (ctranslate2 CPU int8)",
            "model_path": str(p),
            "has_model_bin": bool(model_bin and model_bin.is_file()),
            "has_config_json": bool(config_json and config_json.is_file()),
            "device": "cpu",
            "compute_type": "int8",
        }

    def ensure_loaded(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            status = self.check_model_available()
            if not status["available"]:
                raise VoiceModelMissingError(
                    f"Local speech-to-text model not found at {self.model_path}. "
                    "Expected a faster-whisper directory containing 'model.bin' and 'config.json'."
                )
            t0 = time.perf_counter()
            try:
                if self._model_factory is not None:
                    self._model = self._model_factory(str(self.model_path))
                else:
                    import faster_whisper

                    self._model = faster_whisper.WhisperModel(
                        str(self.model_path),
                        device="cpu",
                        compute_type="int8",
                        local_files_only=True,
                    )
                self.load_seconds = round(time.perf_counter() - t0, 3)
                log.info("loaded local faster-whisper model on CPU", extra={"path": str(self.model_path), "seconds": self.load_seconds})
                return self._model
            except Exception as e:
                raise SpeechRecognitionError(f"Failed to initialize local speech recognition model: {e}") from e

    def transcribe_wav_bytes(
        self,
        wav_bytes: bytes,
        *,
        cancel_event: threading.Event | None = None,
        raise_on_silence: bool = False,
    ) -> dict[str, Any]:
        """Transcribe an in-memory WAV buffer locally on CPU."""
        if cancel_event is not None and cancel_event.is_set():
            raise TranscriptionCancelledError("Transcription cancelled before start.")

        samples, sample_rate, duration_s = parse_wav_bytes(wav_bytes)
        if duration_s > self.max_duration_seconds + 0.5:
            raise InvalidAudioFormatError(
                f"Recording duration ({duration_s:.1f}s) exceeds maximum allowed duration ({self.max_duration_seconds:.1f}s)."
            )

        metrics = compute_audio_metrics(samples, silence_rms_threshold=self.silence_rms_threshold)
        if metrics["is_silent"]:
            if raise_on_silence:
                raise SilentAudioError("No speech detected: the audio recording was silent.")
            return {
                "text": "",
                "is_silent": True,
                "duration_seconds": round(duration_s, 3),
                "processing_ms": 0.0,
                "language": "en",
                "rms_level": metrics["rms"],
                "peak_level": metrics["peak"],
                "confidence": {
                    "avg_logprob": None,
                    "no_speech_prob": 1.0,
                    "heuristic_score": None,
                    "is_calibrated": False,
                    "note": "Recording was below the silence threshold; no speech detected.",
                },
            }

        model = self.ensure_loaded()
        t0 = time.perf_counter()

        # Resample to 16 kHz float32 in [-1.0, 1.0] for deterministic CPU transcription
        samples_16k = resample_int16(samples, sample_rate, 16000)
        audio_f32 = samples_16k.astype(np.float32) / 32768.0

        try:
            with self._lock:
                segments_iter, info = model.transcribe(
                    audio_f32,
                    beam_size=3,
                    vad_filter=False,
                    condition_on_previous_text=False,
                )
                texts: list[str] = []
                logprobs: list[float] = []
                no_speech_probs: list[float] = []

                for seg in segments_iter:
                    if cancel_event is not None and cancel_event.is_set():
                        raise TranscriptionCancelledError("Transcription was cancelled by the user.")
                    seg_text = str(getattr(seg, "text", "") or "").strip()
                    if seg_text:
                        texts.append(seg_text)
                    lp = getattr(seg, "avg_logprob", None)
                    if isinstance(lp, (int, float)) and not math.isnan(lp):
                        logprobs.append(float(lp))
                    nsp = getattr(seg, "no_speech_prob", None)
                    if isinstance(nsp, (int, float)) and not math.isnan(nsp):
                        no_speech_probs.append(float(nsp))
        except TranscriptionCancelledError:
            raise
        except Exception as e:
            raise SpeechRecognitionError(f"Local speech transcription failed: {e}") from e

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        full_text = " ".join(texts).strip()
        avg_lp = round(sum(logprobs) / len(logprobs), 4) if logprobs else None
        avg_nsp = round(sum(no_speech_probs) / len(no_speech_probs), 4) if no_speech_probs else None
        heuristic = round(min(1.0, max(0.0, math.exp(avg_lp))), 3) if avg_lp is not None else None

        is_silent_out = bool(not full_text)
        if is_silent_out and raise_on_silence:
            raise SilentAudioError("No recognizable speech was found in the recording.")

        return {
            "text": full_text,
            "is_silent": is_silent_out,
            "duration_seconds": round(duration_s, 3),
            "processing_ms": elapsed_ms,
            "language": str(getattr(info, "language", "en") or "en"),
            "rms_level": metrics["rms"],
            "peak_level": metrics["peak"],
            "confidence": {
                "avg_logprob": avg_lp,
                "no_speech_prob": avg_nsp,
                "heuristic_score": heuristic,
                "is_calibrated": False,
                "note": (
                    "Whisper avg_logprob and no_speech_prob are uncalibrated model log-likelihood "
                    "heuristics, not calibrated probabilities."
                ),
            },
        }


# ---- 3. Local Text-to-Speech Service (Piper TTS on CPU, Section 4) ----------
def clean_text_for_speech(text: str) -> str:
    """Clean markdown syntax so TTS reads natural prose without spelling out formatting tokens."""
    if not text:
        return ""
    s = str(text)
    # Replace fenced code blocks with a brief spoken note or their inner lines
    s = re.sub(r"```[a-zA-Z0-9_-]*\n?", " ", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"(?m)^#{1,6}\s*", "", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def chunk_text_for_tts(text: str, max_chunk_chars: int = 240) -> list[str]:
    """Split text into manageable speech chunks without EVER cutting a word mid-word."""
    cleaned = clean_text_for_speech(text)
    if not cleaned:
        return []
    if len(cleaned) <= max_chunk_chars:
        return [cleaned]

    # First split on sentence boundaries (. ! ? followed by whitespace)
    sentences = [seg.strip() for seg in re.split(r"(?<=[.!?])\s+", cleaned) if seg.strip()]
    chunks: list[str] = []
    current = ""

    def _flush_current() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for sent in sentences:
        if len(sent) <= max_chunk_chars:
            if not current:
                current = sent
            elif len(current) + 1 + len(sent) <= max_chunk_chars:
                current = f"{current} {sent}"
            else:
                _flush_current()
                current = sent
            continue

        # Sentence itself exceeds max_chunk_chars -> split by words without cutting any word
        _flush_current()
        words = sent.split(" ")
        for word in words:
            if not word:
                continue
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= max_chunk_chars:
                current = f"{current} {word}"
            else:
                _flush_current()
                current = word
        _flush_current()

    _flush_current()
    return chunks


class LocalTextToSpeech:
    """Offline Text-to-Speech powered by Piper ONNX on CPU with zero GPU VRAM usage."""

    def __init__(
        self,
        model_path: str | Path,
        config_path: str | Path | None = None,
        *,
        voice_factory: Callable[[str, str], Any] | None = None,
        max_chunk_chars: int = 240,
    ):
        self.model_path = Path(model_path)
        self.config_path = Path(config_path) if config_path else Path(f"{self.model_path}.json")
        self._voice_factory = voice_factory
        self.max_chunk_chars = int(max_chunk_chars)
        self._loaded_voices: dict[str, Any] = {}
        self._lock = threading.RLock()
        self.load_seconds: float | None = None

    def discover_local_voices(self) -> list[dict[str, Any]]:
        """Discover installed local Piper ONNX voices (those with a companion .onnx.json file)."""
        search_dirs: list[Path] = []
        if self.model_path.parent.exists():
            search_dirs.append(self.model_path.parent)
        ws_models = ROOT / "models"
        if ws_models.exists() and ws_models not in search_dirs:
            search_dirs.append(ws_models)

        voices: list[dict[str, Any]] = []
        seen_paths: set[str] = set()

        for d in search_dirs:
            for onnx_file in sorted(d.glob("*.onnx")):
                cfg_file = Path(f"{onnx_file}.json")
                if not cfg_file.is_file():
                    continue
                resolved_key = str(onnx_file.resolve()).lower()
                if resolved_key in seen_paths:
                    continue
                seen_paths.add(resolved_key)
                sample_rate = 22050
                lang_label = ""
                try:
                    meta = json.loads(cfg_file.read_text(encoding="utf-8"))
                    sample_rate = int(meta.get("audio", {}).get("sample_rate", 22050))
                    lang_label = str(meta.get("language", {}).get("name_english", "") or "")
                except Exception:
                    pass
                voice_id = onnx_file.stem
                voices.append({
                    "id": voice_id,
                    "name": f"{voice_id} ({lang_label})" if lang_label else voice_id,
                    "model_path": str(onnx_file),
                    "config_path": str(cfg_file),
                    "sample_rate": sample_rate,
                })
        return voices

    def resolve_voice_paths(self, voice_id: str | None = None) -> tuple[Path, Path]:
        voices = self.discover_local_voices()
        if voice_id and voice_id.strip():
            vid = voice_id.strip()
            for v in voices:
                if v["id"] == vid or v["model_path"] == vid:
                    return Path(v["model_path"]), Path(v["config_path"])
            # If a specific non-default voice_id was requested and not found, raise Missing Error
            if vid != self.model_path.stem and vid != "default":
                raise VoiceModelMissingError(f"Requested local TTS voice {voice_id!r} is not installed.")

        if self.model_path.is_file() and self.config_path.is_file():
            return self.model_path, self.config_path
        if voices:
            return Path(voices[0]["model_path"]), Path(voices[0]["config_path"])
        raise VoiceModelMissingError(
            f"Local TTS voice assets not found at {self.model_path} / {self.config_path}."
        )

    def check_model_available(self, voice_id: str | None = None) -> dict[str, Any]:
        voices = self.discover_local_voices()
        try:
            m_path, c_path = self.resolve_voice_paths(voice_id)
            ok = m_path.is_file() and c_path.is_file()
        except VoiceModelMissingError:
            m_path, c_path = self.model_path, self.config_path
            ok = False
        return {
            "available": ok,
            "engine": "piper-tts (ONNX CPU)",
            "model_path": str(m_path),
            "config_path": str(c_path),
            "voices": voices,
            "device": "cpu",
        }

    def _get_voice(self, voice_id: str | None = None) -> Any:
        m_path, c_path = self.resolve_voice_paths(voice_id)
        key = str(m_path.resolve())
        with self._lock:
            if key in self._loaded_voices:
                return self._loaded_voices[key]
            t0 = time.perf_counter()
            try:
                if self._voice_factory is not None:
                    voice = self._voice_factory(str(m_path), str(c_path))
                else:
                    import piper

                    voice = piper.PiperVoice.load(
                        str(m_path),
                        config_path=str(c_path),
                        use_cuda=False,
                    )
                self.load_seconds = round(time.perf_counter() - t0, 3)
                self._loaded_voices[key] = voice
                log.info("loaded local Piper TTS voice on CPU", extra={"path": str(m_path), "seconds": self.load_seconds})
                return voice
            except Exception as e:
                raise SpeechSynthesisError(f"Failed to load local Piper TTS voice {m_path.name}: {e}") from e

    def _synthesize_single_chunk_wav(
        self,
        voice: Any,
        chunk_text: str,
        *,
        speed: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        buf = io.BytesIO()
        wf = wave.open(buf, "wb")
        try:
            try:
                from piper.config import SynthesisConfig

                syn_cfg = SynthesisConfig(
                    length_scale=round(1.0 / max(0.5, min(2.0, float(speed))), 4),
                    volume=max(0.0, min(2.0, float(volume))),
                )
                voice.synthesize_wav(chunk_text, wf, syn_config=syn_cfg)
            except ImportError:
                voice.synthesize_wav(chunk_text, wf)
        finally:
            wf.close()
        return buf.getvalue()

    def synthesize_to_wav_bytes(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        speed: float = 1.0,
        volume: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Synthesize text (handling long responses in sentence chunks) into a single WAV byte buffer."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Text for speech synthesis must not be empty.")
        if not (0.5 <= float(speed) <= 2.0):
            raise ValueError("speech_speed must be between 0.5 and 2.0.")
        if not (0.0 <= float(volume) <= 2.0):
            raise ValueError("speech_volume must be between 0.0 and 2.0.")

        chunks = chunk_text_for_tts(text, max_chunk_chars=self.max_chunk_chars)
        if not chunks:
            raise ValueError("Text contains no speakable words after formatting cleanup.")

        voice = self._get_voice(voice_id)
        t0 = time.perf_counter()
        all_samples: list[np.ndarray] = []
        sample_rate = int(getattr(getattr(voice, "config", None), "sample_rate", 22050) or 22050)

        for chunk in chunks:
            if stop_event is not None and stop_event.is_set():
                break
            with self._lock:
                chunk_wav = self._synthesize_single_chunk_wav(
                    voice, chunk, speed=speed, volume=volume
                )
            s_arr, sr, _ = parse_wav_bytes(chunk_wav)
            sample_rate = sr
            all_samples.append(s_arr)
            # Small 60 ms inter-chunk pause for natural sentence pacing
            pause = np.zeros(int(sample_rate * 0.06), dtype=np.int16)
            all_samples.append(pause)

        if not all_samples:
            raise SpeechSynthesisError("Speech synthesis produced no audio samples.")

        combined = np.concatenate(all_samples)
        wav_bytes = encode_pcm16_to_wav(combined, sample_rate=sample_rate, channels=1)
        duration_s = round(combined.size / float(sample_rate), 3)
        return {
            "wav_bytes": wav_bytes,
            "sample_rate": sample_rate,
            "duration_seconds": duration_s,
            "chunk_count": len(chunks),
            "chunks": chunks,
            "synthesis_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }

    def speak(
        self,
        text: str,
        *,
        audio_devices: AudioDeviceManager,
        speaker_selection: str | int | None = "default",
        voice_id: str | None = None,
        speed: float = 1.0,
        volume: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Synthesize and play text chunk-by-chunk so playback can be interrupted at any point."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Text for speech synthesis must not be empty.")
        chunks = chunk_text_for_tts(text, max_chunk_chars=self.max_chunk_chars)
        if not chunks:
            raise ValueError("Text contains no speakable words.")

        voice = self._get_voice(voice_id)
        local_stop = stop_event or threading.Event()
        audio_devices._active_playback_stop = local_stop

        played_chunks = 0
        total_duration = 0.0
        interrupted = False
        dev_info = audio_devices.resolve_output_device(speaker_selection)

        try:
            for chunk in chunks:
                if local_stop.is_set():
                    interrupted = True
                    break
                with self._lock:
                    chunk_wav = self._synthesize_single_chunk_wav(
                        voice, chunk, speed=speed, volume=1.0
                    )
                play_res = audio_devices.play_wav_bytes(
                    chunk_wav,
                    device_selection=speaker_selection,
                    volume=volume,
                    stop_event=local_stop,
                )
                played_chunks += 1
                total_duration += float(play_res["played_seconds"])
                if play_res["interrupted"] or local_stop.is_set():
                    interrupted = True
                    break
        finally:
            if audio_devices._active_playback_stop is local_stop:
                audio_devices._active_playback_stop = None

        return {
            "spoken": played_chunks > 0,
            "chunk_count": len(chunks),
            "played_chunks": played_chunks,
            "played_seconds": round(total_duration, 3),
            "interrupted": interrupted,
            "device": dev_info,
        }


# ---- 4. Wake-Word Detector Service (openWakeWord on CPU, Section 5) ---------
class WakeWordDetector:
    """Optional local 'Hey Jarvis' wake-word detector using openWakeWord on CPU.

    - Strictly disabled by default (`enabled = False`).
    - Never auto-starts on application restart.
    - Avoids continuous Whisper transcription; only triggers command capture after wake word.
    - Enforces sensitivity, cooldown (duplicate activation prevention), and inactivity timeout.
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        detector_factory: Callable[[str], Any] | None = None,
        on_detected: Callable[[dict[str, Any]], None] | None = None,
        cooldown_seconds: float = 3.0,
    ):
        self.model_path = Path(model_path)
        self._detector_factory = detector_factory
        self.on_detected = on_detected
        self.cooldown_seconds = float(cooldown_seconds)

        self._lock = threading.RLock()
        self._model: Any = None
        self.enabled: bool = False
        self.sensitivity: float = 0.5
        self.timeout_seconds: float = 10.0
        self.wake_phrase: str = "Hey Jarvis"

        self._command_window_active: bool = False
        self._command_window_expires_at: float = 0.0
        self._last_trigger_monotonic: float = -9999.0
        self.detections_count: int = 0
        self.last_detected_at: float | None = None
        self.last_score: float = 0.0

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def check_model_available(self) -> dict[str, Any]:
        ok = self.model_path.is_file()
        return {
            "available": ok,
            "engine": "openWakeWord (ONNX CPU)",
            "wake_phrase": self.wake_phrase,
            "model_path": str(self.model_path),
            "device": "cpu",
        }

    def ensure_loaded(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            if not self.model_path.is_file():
                raise VoiceModelMissingError(
                    f"Local wake-word model not found at {self.model_path}."
                )
            try:
                if self._detector_factory is not None:
                    self._model = self._detector_factory(str(self.model_path))
                else:
                    from openwakeword.model import Model as OWWModel

                    self._model = OWWModel(
                        wakeword_models=[str(self.model_path)],
                        inference_framework="onnx",
                    )
                return self._model
            except Exception as e:
                raise WakeWordError(f"Failed to initialize local wake-word model: {e}") from e

    def configure(
        self,
        *,
        sensitivity: float | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        with self._lock:
            if sensitivity is not None:
                val = float(sensitivity)
                if not (0.05 <= val <= 0.99):
                    raise ValueError("wake_word_sensitivity must be between 0.05 and 0.99")
                self.sensitivity = val
            if timeout_seconds is not None:
                tval = float(timeout_seconds)
                if not (1.0 <= tval <= 120.0):
                    raise ValueError("wake_word_timeout_seconds must be between 1.0 and 120.0")
                self.timeout_seconds = tval

    def enable(
        self,
        *,
        audio_devices: AudioDeviceManager | None = None,
        device_selection: str | int | None = "default",
        sensitivity: float | None = None,
        timeout_seconds: float | None = None,
        start_background_stream: bool = False,
    ) -> dict[str, Any]:
        """Explicitly enable wake-word monitoring."""
        with self._lock:
            self.configure(sensitivity=sensitivity, timeout_seconds=timeout_seconds)
            self.ensure_loaded()
            if audio_devices is not None:
                audio_devices.resolve_input_device(device_selection)
            self.enabled = True
            self._command_window_active = False
            self._stop_event.clear()
            if start_background_stream and audio_devices is not None and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(
                    target=self._monitor_loop,
                    args=(audio_devices, device_selection),
                    name="jarvis-wakeword-monitor",
                    daemon=True,
                )
                self._thread.start()
            return self.status()

    def disable(self) -> dict[str, Any]:
        """Immediately disable wake-word monitoring and release microphone monitoring state."""
        with self._lock:
            self.enabled = False
            self._command_window_active = False
            self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.5)
        with self._lock:
            self._thread = None
            if self._model is not None and hasattr(self._model, "reset"):
                try:
                    self._model.reset()
                except Exception:
                    pass
            return self.status()

    def evaluate_frame(
        self,
        pcm16_frame: np.ndarray,
        *,
        now_monotonic: float | None = None,
        busy_recording: bool = False,
    ) -> dict[str, Any]:
        """Evaluate a 16 kHz int16 audio frame against the wake-word model."""
        now = float(now_monotonic if now_monotonic is not None else time.monotonic())
        with self._lock:
            self.check_inactivity_timeout(now_monotonic=now)
            if not self.enabled:
                return {"detected": False, "reason": "disabled", "score": 0.0}

            model = self.ensure_loaded()
            arr = np.asarray(pcm16_frame, dtype=np.int16).reshape(-1)
            scores = model.predict(arr)
            score = float(max(scores.values())) if isinstance(scores, dict) and scores else float(scores or 0.0)
            self.last_score = round(score, 4)

            if score < self.sensitivity:
                return {"detected": False, "reason": "below_threshold", "score": self.last_score}

            if busy_recording or self._command_window_active:
                return {"detected": False, "reason": "duplicate_suppressed_active_window", "score": self.last_score}

            if (now - self._last_trigger_monotonic) < self.cooldown_seconds:
                return {"detected": False, "reason": "duplicate_suppressed_cooldown", "score": self.last_score}

            self._last_trigger_monotonic = now
            self._command_window_active = True
            self._command_window_expires_at = now + self.timeout_seconds
            self.detections_count += 1
            self.last_detected_at = time.time()
            event = {
                "detected": True,
                "wake_phrase": self.wake_phrase,
                "score": self.last_score,
                "sensitivity": self.sensitivity,
                "timeout_seconds": self.timeout_seconds,
                "detections_count": self.detections_count,
            }

        if self.on_detected is not None:
            try:
                self.on_detected(event)
            except Exception as e:
                log.warning("wake word callback error", extra={"error": str(e)})
        return event

    def check_inactivity_timeout(self, *, now_monotonic: float | None = None) -> bool:
        """Check whether an active post-wake-word listening window has timed out due to inactivity."""
        now = float(now_monotonic if now_monotonic is not None else time.monotonic())
        with self._lock:
            if self._command_window_active and now >= self._command_window_expires_at:
                self._command_window_active = False
                return True
            return False

    def complete_command_window(self) -> None:
        with self._lock:
            self._command_window_active = False

    def _monitor_loop(self, audio_devices: AudioDeviceManager, device_selection: str | int | None) -> None:
        try:
            dev = audio_devices.resolve_input_device(device_selection)
            sd = audio_devices._get_sd()
            with sd.InputStream(
                samplerate=16000,
                device=dev["index"],
                channels=1,
                dtype="int16",
                blocksize=1280,
            ) as stream:
                while not self._stop_event.is_set() and self.enabled:
                    chunk, _ = stream.read(1280)
                    arr = np.asarray(chunk, dtype=np.int16).reshape(-1)
                    self.evaluate_frame(arr)
        except Exception as e:
            log.warning("wake word monitor loop stopped", extra={"error": str(e)})
            with self._lock:
                self.enabled = False
                self._command_window_active = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            self.check_inactivity_timeout()
            return {
                "enabled": self.enabled,
                "wake_phrase": self.wake_phrase,
                "sensitivity": self.sensitivity,
                "timeout_seconds": self.timeout_seconds,
                "command_window_active": self._command_window_active,
                "detections_count": self.detections_count,
                "last_score": self.last_score,
                "last_detected_at": self.last_detected_at,
                "microphone_active": self.enabled,
                "privacy_notice": WAKE_WORD_PRIVACY_NOTICE,
                "model": self.check_model_available(),
            }


# ---- 5. Unified VoiceService Coordinator (Sections 7, 8, 9, 10, 11) ---------
class VoiceService:
    """Coordinates local STT, TTS, Wake-Word detection, Audio Devices, and UI Voice States."""

    def __init__(
        self,
        settings_provider: Callable[[], Settings],
        *,
        audio_devices: AudioDeviceManager | None = None,
        stt: LocalSpeechToText | None = None,
        tts: LocalTextToSpeech | None = None,
        wake_word: WakeWordDetector | None = None,
    ):
        self.settings_provider = settings_provider
        s = self.settings_provider()

        self.audio_devices = audio_devices or AudioDeviceManager()
        self.stt = stt or LocalSpeechToText(s.stt_model_path)
        self.tts = tts or LocalTextToSpeech(s.tts_model_path, s.tts_config_path)
        self.wake_word = wake_word or WakeWordDetector(
            s.wake_word_model_path,
            on_detected=self._on_wake_word_detected,
        )

        # Enforce startup privacy guarantee: wake-word detection is ALWAYS disabled on startup/restart
        self.wake_word.disable()

        self._lock = threading.RLock()
        self._state: str = "Idle"
        self._last_error: str | None = None
        self._audio_level: float = 0.0
        self._recording_started_at: float | None = None
        self._cancel_recording_event: threading.Event | None = None
        self._stop_recording_event: threading.Event | None = None
        self._stop_speaking_event: threading.Event | None = None
        self._pending_transcript: dict[str, Any] | None = None

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == "Idle" and self.wake_word.enabled:
                return "Listening"
            return self._state

    def set_state(self, new_state: str, *, error: str | None = None) -> str:
        if new_state not in VOICE_STATES:
            raise ValueError(f"Invalid voice state: {new_state!r}")
        with self._lock:
            self._state = new_state
            self._last_error = error
            return self.state

    def _on_wake_word_detected(self, event: dict[str, Any]) -> None:
        with self._lock:
            if self._state in ("Idle", "Listening"):
                self._state = "Listening"

    def notify_user_input_started(self) -> None:
        """Stop TTS playback immediately when new user input or recording starts if configured."""
        s = self.settings_provider()
        if s.stop_speaking_on_input:
            self.stop_speaking()

    def get_devices(self) -> dict[str, Any]:
        s = self.settings_provider()
        inputs = self.audio_devices.list_input_devices()
        outputs = self.audio_devices.list_output_devices()
        sel_mic = None
        sel_spk = None
        mic_error = None
        spk_error = None
        try:
            sel_mic = self.audio_devices.resolve_input_device(s.selected_microphone)
        except AudioDeviceUnavailableError as e:
            mic_error = str(e)
        try:
            sel_spk = self.audio_devices.resolve_output_device(s.selected_speaker)
        except AudioDeviceUnavailableError as e:
            spk_error = str(e)
        return {
            "microphones": inputs,
            "speakers": outputs,
            "selected_microphone": s.selected_microphone,
            "selected_speaker": s.selected_speaker,
            "resolved_microphone": sel_mic,
            "resolved_speaker": sel_spk,
            "microphone_error": mic_error,
            "speaker_error": spk_error,
        }

    def get_status(self) -> dict[str, Any]:
        s = self.settings_provider()
        with self._lock:
            cur_state = self.state
            rec_elapsed = (
                round(time.time() - self._recording_started_at, 2)
                if self._recording_started_at is not None and cur_state == "Recording"
                else 0.0
            )
            return {
                "state": cur_state,
                "last_error": self._last_error,
                "audio_level": self._audio_level,
                "recording_elapsed_seconds": rec_elapsed,
                "microphone_active": cur_state in ("Listening", "Recording") or self.wake_word.enabled,
                "speaking_active": cur_state == "Speaking",
                "pending_transcript": dict(self._pending_transcript) if self._pending_transcript else None,
                "settings": {
                    "voice_input_enabled": s.voice_input_enabled,
                    "voice_output_enabled": s.voice_output_enabled,
                    "wake_word_enabled": self.wake_word.enabled,
                    "selected_microphone": s.selected_microphone,
                    "selected_speaker": s.selected_speaker,
                    "selected_tts_voice": s.selected_tts_voice,
                    "speech_speed": s.speech_speed,
                    "speech_volume": s.speech_volume,
                    "listening_duration": s.listening_duration,
                    "wake_word_sensitivity": s.wake_word_sensitivity,
                    "wake_word_timeout_seconds": s.wake_word_timeout_seconds,
                    "auto_submit_transcript": s.auto_submit_transcript,
                    "stop_speaking_on_input": s.stop_speaking_on_input,
                },
                "models": {
                    "stt": self.stt.check_model_available(),
                    "tts": self.tts.check_model_available(s.selected_tts_voice),
                    "wake_word": self.wake_word.check_model_available(),
                },
                "wake_word": self.wake_word.status(),
            }

    def record_and_transcribe(
        self,
        *,
        duration_seconds: float | None = None,
        device_selection: str | int | None = None,
    ) -> dict[str, Any]:
        """Record from the local microphone and transcribe locally on CPU."""
        s = self.settings_provider()
        if not s.voice_input_enabled:
            raise VoiceError("Voice input is currently disabled in settings.")

        self.notify_user_input_started()
        eff_duration = float(duration_seconds if duration_seconds is not None else s.listening_duration)
        eff_device = device_selection if device_selection is not None else s.selected_microphone

        cancel_ev = threading.Event()
        stop_ev = threading.Event()
        with self._lock:
            self._cancel_recording_event = cancel_ev
            self._stop_recording_event = stop_ev
            self._recording_started_at = time.time()
            self._state = "Recording"
            self._last_error = None

        def _on_level(lvl: float) -> None:
            with self._lock:
                self._audio_level = lvl

        try:
            wav_bytes = self.audio_devices.record_wav(
                duration_seconds=eff_duration,
                sample_rate=16000,
                device_selection=eff_device,
                cancel_event=cancel_ev,
                stop_event=stop_ev,
                level_callback=_on_level,
            )
            with self._lock:
                self._state = "Transcribing"
                self._recording_started_at = None
                self._audio_level = 0.0

            result = self.stt.transcribe_wav_bytes(wav_bytes, cancel_event=cancel_ev)
            return self._finalize_transcription_result(result, auto_submit=s.auto_submit_transcript)
        except TranscriptionCancelledError as e:
            with self._lock:
                self._state = "Idle"
                self._recording_started_at = None
                self._audio_level = 0.0
                self._last_error = None
            raise e
        except Exception as e:
            with self._lock:
                self._state = "Error"
                self._recording_started_at = None
                self._audio_level = 0.0
                self._last_error = str(e)
            raise
        finally:
            with self._lock:
                self._cancel_recording_event = None
                self._stop_recording_event = None
                self._recording_started_at = None
                self._audio_level = 0.0
                self.wake_word.complete_command_window()

    def transcribe_audio_bytes(
        self,
        wav_bytes: bytes,
        *,
        raise_on_silence: bool = False,
    ) -> dict[str, Any]:
        """Transcribe an in-memory WAV buffer (e.g. from browser microphone capture or test fixture)."""
        s = self.settings_provider()
        if not s.voice_input_enabled:
            raise VoiceError("Voice input is currently disabled in settings.")

        self.notify_user_input_started()
        cancel_ev = threading.Event()
        with self._lock:
            self._cancel_recording_event = cancel_ev
            self._state = "Transcribing"
            self._last_error = None

        try:
            result = self.stt.transcribe_wav_bytes(
                wav_bytes,
                cancel_event=cancel_ev,
                raise_on_silence=raise_on_silence,
            )
            return self._finalize_transcription_result(result, auto_submit=s.auto_submit_transcript)
        except TranscriptionCancelledError:
            with self._lock:
                self._state = "Idle"
                self._last_error = None
            raise
        except Exception as e:
            with self._lock:
                self._state = "Error"
                self._last_error = str(e)
            raise
        finally:
            with self._lock:
                self._cancel_recording_event = None
                self.wake_word.complete_command_window()

    def _finalize_transcription_result(self, result: dict[str, Any], *, auto_submit: bool) -> dict[str, Any]:
        with self._lock:
            if result.get("is_silent") or not result.get("text"):
                self._pending_transcript = None
                self._state = "Idle"
                return {
                    **result,
                    "state": self.state,
                    "auto_submit": False,
                    "waiting_for_confirmation": False,
                }
            transcript_obj = {
                "id": uuid.uuid4().hex,
                "text": result["text"],
                "duration_seconds": result["duration_seconds"],
                "processing_ms": result["processing_ms"],
                "confidence": result["confidence"],
                "created_at": time.time(),
            }
            self._pending_transcript = transcript_obj
            self._state = "Idle" if auto_submit else "WaitingForConfirmation"
            return {
                **result,
                "transcript_id": transcript_obj["id"],
                "state": self.state,
                "auto_submit": bool(auto_submit),
                "waiting_for_confirmation": not bool(auto_submit),
            }

    def edit_pending_transcript(self, new_text: str) -> dict[str, Any]:
        if not isinstance(new_text, str) or not new_text.strip():
            raise ValueError("Transcript text must not be empty.")
        with self._lock:
            if self._pending_transcript is None:
                self._pending_transcript = {
                    "id": uuid.uuid4().hex,
                    "text": new_text.strip(),
                    "created_at": time.time(),
                }
            else:
                self._pending_transcript["text"] = new_text.strip()
            self._state = "WaitingForConfirmation"
            return dict(self._pending_transcript)

    def clear_pending_transcript(self) -> None:
        with self._lock:
            self._pending_transcript = None
            if self._state == "WaitingForConfirmation":
                self._state = "Idle"

    def stop_recording(self) -> None:
        with self._lock:
            if self._stop_recording_event is not None:
                self._stop_recording_event.set()

    def cancel_recording(self) -> dict[str, Any]:
        with self._lock:
            if self._cancel_recording_event is not None:
                self._cancel_recording_event.set()
            self._pending_transcript = None
            self._recording_started_at = None
            self._audio_level = 0.0
            self._state = "Idle"
            self._last_error = None
            return self.get_status()

    def synthesize_speech(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        speed: float | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        s = self.settings_provider()
        return self.tts.synthesize_to_wav_bytes(
            text,
            voice_id=voice_id or s.selected_tts_voice,
            speed=float(speed if speed is not None else s.speech_speed),
            volume=float(volume if volume is not None else s.speech_volume),
        )

    def speak_response(
        self,
        text: str,
        *,
        speaker_selection: str | int | None = None,
        voice_id: str | None = None,
        speed: float | None = None,
        volume: float | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Speak an assistant response through the selected local output device."""
        s = self.settings_provider()
        if not force and not s.voice_output_enabled:
            return {"spoken": False, "reason": "voice_output_disabled", "state": self.state}

        self.stop_speaking()
        stop_ev = threading.Event()
        with self._lock:
            self._stop_speaking_event = stop_ev
            self._state = "Speaking"
            self._last_error = None

        try:
            res = self.tts.speak(
                text,
                audio_devices=self.audio_devices,
                speaker_selection=speaker_selection or s.selected_speaker,
                voice_id=voice_id or s.selected_tts_voice,
                speed=float(speed if speed is not None else s.speech_speed),
                volume=float(volume if volume is not None else s.speech_volume),
                stop_event=stop_ev,
            )
            with self._lock:
                self._state = "Interrupted" if res.get("interrupted") else "Idle"
            return {**res, "state": self.state}
        except VoiceModelMissingError as e:
            # Handle missing voice assets gracefully so text responses continue working
            log.warning("TTS voice assets missing", extra={"error": str(e)})
            with self._lock:
                self._state = "Idle"
                self._last_error = str(e)
            return {"spoken": False, "reason": "missing_voice_assets", "error": str(e), "state": self.state}
        except Exception as e:
            with self._lock:
                self._state = "Error"
                self._last_error = str(e)
            raise
        finally:
            with self._lock:
                if self._stop_speaking_event is stop_ev:
                    self._stop_speaking_event = None

    def stop_speaking(self) -> dict[str, Any]:
        with self._lock:
            ev = self._stop_speaking_event
            if ev is not None:
                ev.set()
            self.audio_devices.stop_playback()
            if self._state == "Speaking":
                self._state = "Interrupted"
            return self.get_status()

    def set_wake_word(
        self,
        enabled: bool,
        *,
        sensitivity: float | None = None,
        timeout_seconds: float | None = None,
        start_background_stream: bool = False,
    ) -> dict[str, Any]:
        s = self.settings_provider()
        if enabled:
            res = self.wake_word.enable(
                audio_devices=self.audio_devices,
                device_selection=s.selected_microphone,
                sensitivity=sensitivity if sensitivity is not None else s.wake_word_sensitivity,
                timeout_seconds=timeout_seconds if timeout_seconds is not None else s.wake_word_timeout_seconds,
                start_background_stream=start_background_stream,
            )
            with self._lock:
                if self._state == "Idle":
                    self._state = "Listening"
            return res
        else:
            res = self.wake_word.disable()
            with self._lock:
                if self._state == "Listening":
                    self._state = "Idle"
            return res

    def close(self) -> None:
        self.cancel_recording()
        self.stop_speaking()
        self.wake_word.disable()
