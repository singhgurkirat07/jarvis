"""Interactive Real Hardware Verification Script for JARVIS Phase 5.

Run with:
    py -3.12 scripts/voice_hardware_check.py

Performs interactive physical verification of:
  1. Available recording (microphone) and playback (speaker/headphone) devices.
  2. Local Piper TTS synthesis and speaker playback confirmation.
  3. Live physical microphone capture (in-memory only; never saved to disk).
  4. Local Faster-Whisper speech-to-text transcription of the recorded phrase.
  5. Optional local OpenWakeWord ("Hey Jarvis") detection check.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import load_settings
from backend.voice import (
    AudioDeviceManager,
    LocalSpeechToText,
    LocalTextToSpeech,
    VoiceError,
    WakeWordDetector,
)


def _prompt_yes_no(prompt_text: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        raw = input(f"{prompt_text} {suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not raw:
        return default
    return raw in ("y", "yes")


def _prompt_choice(prompt_text: str, default_val: str = "") -> str:
    try:
        raw = input(f"{prompt_text}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default_val
    return raw if raw else default_val


def main() -> int:
    print("=" * 72)
    print("JARVIS PHASE 5 — INTERACTIVE VOICE HARDWARE VERIFICATION")
    print("=" * 72)

    settings = load_settings()
    audio = AudioDeviceManager()
    stt = LocalSpeechToText(model_path=settings.stt_model_path)
    tts = LocalTextToSpeech(
        model_path=settings.tts_model_path,
        config_path=settings.tts_config_path,
    )

    # 1. List available recording and playback devices
    print("\n[Step 1/5] Discovering Audio Input & Output Devices...")
    mics = audio.list_input_devices()
    speakers = audio.list_output_devices()

    print(f"\nFound {len(mics)} Microphone Input Device(s):")
    for m in mics:
        default_tag = " [SYSTEM DEFAULT]" if m.get("is_default") else ""
        print(f"  - ID {m['id']}: {m['name']} ({m['channels']} ch @ {m['default_sample_rate']} Hz){default_tag}")

    print(f"\nFound {len(speakers)} Speaker / Output Device(s):")
    for s in speakers:
        default_tag = " [SYSTEM DEFAULT]" if s.get("is_default") else ""
        print(f"  - ID {s['id']}: {s['name']} ({s['channels']} ch @ {s['default_sample_rate']} Hz){default_tag}")

    if not mics:
        print("\nERROR: No microphone input devices detected on this machine.")
        return 1
    if not speakers:
        print("\nERROR: No speaker output devices detected on this machine.")
        return 1

    selected_mic = _prompt_choice(
        "\nEnter Microphone Device ID to test (or press Enter for 'default')",
        default_val="default",
    )
    selected_speaker = _prompt_choice(
        "Enter Speaker Device ID to test (or press Enter for 'default')",
        default_val="default",
    )

    # 2. Play a short local test sentence through TTS
    print("\n[Step 2/5] Testing Local Piper Text-to-Speech Playback...")
    test_sentence = "Hello, I am Jarvis. This is a local text to speech hardware check running on your CPU."
    print(f"  Synthesizing & playing: \"{test_sentence}\"")
    tts_ok = False
    try:
        tts_res = tts.speak(
            test_sentence,
            audio_devices=audio,
            speaker_selection=selected_speaker,
            speed=settings.speech_speed,
            volume=settings.speech_volume,
        )
        print(
            f"  Playback complete ({tts_res['played_chunks']}/{tts_res['chunk_count']} chunks, "
            f"duration: {tts_res['played_seconds']:.2f}s)."
        )
        tts_ok = _prompt_yes_no("  Did you hear the spoken test sentence clearly?", default=True)
    except VoiceError as exc:
        print(f"  TTS Playback failed: {exc}")
        tts_ok = False

    # 3 & 4. Record a short microphone sample & transcribe locally
    print("\n[Step 3/5] Testing Physical Microphone Recording & Local Speech-to-Text...")
    print("  NOTE: Audio is captured strictly in RAM and never saved to disk.")
    record_seconds = 5.0
    _prompt_choice(
        f"  Press Enter when ready, then speak a short phrase for {record_seconds:.0f} seconds "
        "(e.g. 'Hello Jarvis, what is the current time')"
    )
    print(f"  >>> RECORDING NOW ({record_seconds:.0f} seconds)... Speak into your microphone! <<<")
    stt_ok = False
    recognized_transcript = ""
    try:
        wav_bytes = audio.record_wav(
            duration_seconds=record_seconds,
            sample_rate=16000,
            device_selection=selected_mic,
        )
        print("\n[Step 4/5] Transcribing recorded audio locally with Faster-Whisper (CPU int8)...")
        tr = stt.transcribe_wav_bytes(wav_bytes)
        recognized_transcript = tr.get("text", "")
        conf = tr.get("confidence", {})
        print(
            f"  Recording duration: {tr.get('duration_seconds')}s "
            f"(peak={tr.get('peak_level')}, rms={tr.get('rms_level')}, processing={tr.get('processing_ms')} ms)."
        )
        print(f"  Recognized Transcript: \"{recognized_transcript}\"")
        print(
            f"  Confidence Info (uncalibrated): avg_logprob={conf.get('avg_logprob')}, "
            f"no_speech_prob={conf.get('no_speech_prob')}"
        )
        stt_ok = _prompt_yes_no("  Does the recognized transcript match what you spoke?", default=True)
    except VoiceError as exc:
        print(f"  Microphone / STT check failed: {exc}")
        stt_ok = False

    # 5. Optionally test wake-word activation
    print("\n[Step 5/5] Optional Local Wake-Word ('Hey Jarvis') Test...")
    wake_tested = False
    wake_ok = False
    if _prompt_yes_no("  Would you like to test saying 'Hey Jarvis' to trigger wake-word detection?", default=True):
        wake_tested = True
        detected_events = []

        def _on_wake(ev: dict) -> None:
            detected_events.append(ev)
            print(f"\n  >>> WAKE WORD DETECTED! Score={ev.get('score')} Phrase={ev.get('wake_phrase')} <<<")

        wake = WakeWordDetector(
            model_path=settings.wake_word_model_path,
            on_detected=_on_wake,
        )
        try:
            wake.enable(
                audio_devices=audio,
                device_selection=selected_mic,
                sensitivity=settings.wake_word_sensitivity,
                timeout_seconds=settings.wake_word_timeout_seconds,
                start_background_stream=True,
            )
            print("  Listening for 'Hey Jarvis' for up to 10 seconds... Say 'Hey Jarvis' now!")
            deadline = time.time() + 10.0
            while time.time() < deadline and not detected_events:
                time.sleep(0.2)
            wake.disable()
            if detected_events:
                wake_ok = True
            else:
                print("  No wake-word trigger detected within 10 seconds.")
                wake_ok = False
        except VoiceError as exc:
            wake.disable()
            print(f"  Wake-word test error: {exc}")
            wake_ok = False

    print("\n" + "=" * 72)
    print("RESULTS")
    print("=" * 72)
    print(f"  Microphones detected       : {len(mics)}")
    print(f"  Speakers detected          : {len(speakers)}")
    print(f"  Selected microphone        : {selected_mic}")
    print(f"  Selected speaker           : {selected_speaker}")
    print(f"  Speaker TTS playback clear : {'PASS' if tts_ok else 'FAIL / UNCONFIRMED'}")
    print(f"  Microphone STT transcript  : {recognized_transcript!r}")
    print(f"  Microphone STT confirmed   : {'PASS' if stt_ok else 'FAIL / UNCONFIRMED'}")
    if wake_tested:
        print(f"  Wake-word ('Hey Jarvis')   : {'PASS' if wake_ok else 'NOT DETECTED'}")
    else:
        print("  Wake-word ('Hey Jarvis')   : SKIPPED (optional)")
    print("=" * 72)
    return 0 if (tts_ok and stt_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
