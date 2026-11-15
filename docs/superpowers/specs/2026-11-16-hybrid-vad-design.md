# Hybrid VAD and End-of-Speech Design

## Intent

Reduce delayed submissions caused by background noise while preserving natural pauses in English, Filipino, and Taglish classroom speech.

## Goals

- Use a 1.5-second end-of-speech pause by default, configurable to 3 seconds.
- Calibrate an adaptive noise floor while the detector is idle.
- Prefer Silero VAD speech probabilities when installed.
- Fall back safely to adaptive RMS detection when Silero is unavailable.
- Keep existing microphone endpoints, response formats, pause/resume behavior, STT, routing, memory, TTS, and Unreal integration unchanged.
- Expose detection and latency diagnostics through the existing status response.

## Approaches considered

1. Fixed RMS-only detection: rejected because steady background noise can keep resetting the silence timer.
2. Adaptive RMS-only detection: lightweight fallback, but nearby speech can still resemble the teacher.
3. Hybrid adaptive RMS plus Silero VAD: selected because it combines low-cost gating, noise-floor adaptation, and speech-aware confirmation with a safe dependency fallback.

## Design

The detector maintains a noise floor from idle/non-speech frames, applies start/hold hysteresis, and requires consecutive speech frames before opening an utterance. During an active utterance, a frame extends the speech timeout only when it passes both the adaptive energy gate and the Silero probability gate when Silero is available. Otherwise the adaptive RMS gate is used. End-of-speech is emitted after the configured silence window, and counters remain visible through `/microphone/status`.

Configuration uses `MICROPHONE_VAD_SILENCE_MS`, default `1500`, plus adaptive threshold and Silero settings documented in the backend README.

## Verification

Regression tests cover quiet speech, steady background noise, natural pauses, configurable 3-second pauses, Silero confirmation/fallback behavior, and existing microphone route compatibility. Hardware-dependent Quest 3S tests remain manual.
