from __future__ import annotations

import math
import logging
import os
import queue
import threading
import time
import inspect
from collections import deque
from collections.abc import Callable

from .microphone import (
    DEFAULT_MICROPHONE_DEVICE,
    MicrophoneBusyError,
    MicrophoneError,
    MicrophoneRecorder,
    MicrophoneTranscriptionError,
    SoundDeviceRecorder,
    Transcriber,
)
from .speech_to_text import SpeechToTextService

logger = logging.getLogger(__name__)


class SileroSpeechGate:
    """Optional lazy Silero frame gate; unavailable environments use RMS."""

    def __init__(self) -> None:
        self.model = None
        self.available = False
        self._torch = None

    def warmup(self) -> None:
        if self.available:
            return
        try:
            import torch
            from silero_vad import load_silero_vad

            self._torch = torch
            self.model = load_silero_vad()
            self.available = True
            logger.info("Silero VAD loaded successfully")
        except Exception as error:
            self.model = None
            self.available = False
            logger.info("Silero VAD unavailable; using adaptive RMS fallback: %s", error)

    def speech_probability(self, audio_bytes: bytes, sample_rate: int) -> float | None:
        if not self.available or self.model is None:
            return None
        try:
            import numpy as np

            samples = np.frombuffer(audio_bytes, dtype=np.float32)
            if sample_rate != 16_000 and samples.size:
                target_count = max(1, round(samples.size * 16_000 / sample_rate))
                samples = np.interp(
                    np.linspace(0, samples.size - 1, target_count),
                    np.arange(samples.size),
                    samples,
                ).astype(np.float32)
            if samples.size < 512:
                samples = np.pad(samples, (0, 512 - samples.size))
            tensor = self._torch.from_numpy(np.clip(samples[:512], -1.0, 1.0))
            with self._torch.no_grad():
                probability = self.model(tensor, 16_000)
            return float(probability.item() if hasattr(probability, "item") else probability)
        except Exception:
            logger.debug("Silero VAD inference failed; using adaptive RMS fallback", exc_info=True)
            return None


class VoiceActivityDetector:
    """Hybrid speech detector with adaptive RMS fallback and optional Silero."""

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        frame_ms: int = 20,
        silence_ms: int | None = None,
        threshold: float | None = None,
        pre_roll_ms: int = 200,
        max_utterance_ms: int = 20_000,
        adaptive_noise: bool = True,
        noise_floor: float | None = None,
        noise_floor_alpha: float | None = None,
        calibration_ms: int | None = None,
        speech_start_multiplier: float | None = None,
        speech_hold_multiplier: float | None = None,
        silero=None,
        silero_threshold: float | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.silence_ms = silence_ms if silence_ms is not None else int(os.getenv("MICROPHONE_VAD_SILENCE_MS", "1500"))
        self.threshold = threshold if threshold is not None else float(os.getenv("MICROPHONE_VAD_THRESHOLD", "0.015"))
        self.adaptive_noise = adaptive_noise
        self.noise_floor_alpha = noise_floor_alpha if noise_floor_alpha is not None else float(
            os.getenv("MICROPHONE_VAD_NOISE_FLOOR_ALPHA", "0.05")
        )
        self.calibration_ms = calibration_ms if calibration_ms is not None else int(
            os.getenv("MICROPHONE_VAD_CALIBRATION_MS", "1000")
        )
        self._calibration_frames_remaining = max(0, self.calibration_ms // self.frame_ms)
        self.noise_floor = noise_floor if noise_floor is not None else float(
            os.getenv("MICROPHONE_VAD_NOISE_FLOOR", str(self.threshold / 3.0))
        )
        self.speech_start_multiplier = speech_start_multiplier if speech_start_multiplier is not None else float(
            os.getenv("MICROPHONE_VAD_START_MULTIPLIER", "2.5")
        )
        self.speech_hold_multiplier = speech_hold_multiplier if speech_hold_multiplier is not None else float(
            os.getenv("MICROPHONE_VAD_HOLD_MULTIPLIER", "1.5")
        )
        self.silero_threshold = silero_threshold if silero_threshold is not None else float(
            os.getenv("MICROPHONE_VAD_SILERO_THRESHOLD", "0.5")
        )
        self.silero = silero if silero is not None else self._default_silero()
        self.last_silero_probability: float | None = None
        self._pre_roll = deque(maxlen=max(1, pre_roll_ms // frame_ms))
        self._max_utterance_ms = max_utterance_ms
        self._current: list[bytes] = []
        self._current_ms = 0
        self._silence_ms = 0
        self._speech_active = False
        self.last_rms = 0.0
        self.last_peak = 0.0
        self.last_speech_detected = False

    def process(self, audio_bytes: bytes) -> list[bytes]:
        if not audio_bytes:
            return []
        duration_ms = self._duration_ms(audio_bytes)
        if duration_ms <= 0:
            return []
        self.last_rms, self.last_peak = self._levels(audio_bytes)
        if not self._current and self._calibration_frames_remaining > 0:
            self._adapt_noise_floor(self.last_rms, alpha=max(self.noise_floor_alpha, 0.25))
            self._calibration_frames_remaining -= 1
        speaking = self._speech_detected(audio_bytes, active=bool(self._current))
        self.last_speech_detected = speaking
        emitted: list[bytes] = []
        if not self._current and speaking:
            self._speech_active = True
            self._current = [*self._pre_roll, audio_bytes]
            self._current_ms = sum(self._duration_ms(chunk) for chunk in self._current)
            self._silence_ms = 0
        elif self._current:
            self._current.append(audio_bytes)
            self._current_ms += duration_ms
            if speaking:
                self._silence_ms = 0
            else:
                self._silence_ms += duration_ms
            if self._silence_ms >= self.silence_ms or self._current_ms >= self._max_utterance_ms:
                emitted.append(b"".join(self._current))
                self.reset()
        else:
            self._update_noise_floor(self.last_rms)
            self._pre_roll.append(audio_bytes)
        return emitted

    def flush(self) -> list[bytes]:
        if not self._current:
            return []
        emitted = [b"".join(self._current)]
        self.reset()
        return emitted

    def reset(self) -> None:
        self._current = []
        self._current_ms = 0
        self._silence_ms = 0
        self._pre_roll.clear()
        self._speech_active = False
        self.last_silero_probability = None
        self.last_speech_detected = False

    @property
    def speech_active(self) -> bool:
        return self._speech_active

    def _duration_ms(self, audio_bytes: bytes) -> int:
        sample_count = len(audio_bytes) // 4
        return int(sample_count * 1_000 / self.sample_rate)

    def warmup(self) -> None:
        warmup = getattr(self.silero, "warmup", None)
        if callable(warmup):
            warmup()

    def begin_calibration(self) -> None:
        self._calibration_frames_remaining = max(0, self.calibration_ms // self.frame_ms)

    def diagnostics(self) -> dict:
        return {
            "provider": "silero" if self._silero_available() else "adaptive_rms",
            "adaptive_noise": self.adaptive_noise,
            "noise_floor": round(self.noise_floor, 6),
            "start_threshold": round(self._start_threshold(), 6),
            "hold_threshold": round(self._hold_threshold(), 6),
            "silero_threshold": self.silero_threshold,
            "silero_probability": self.last_silero_probability,
            "silero_available": self._silero_available(),
            "silence_ms": self.silence_ms,
            "calibration_frames_remaining": self._calibration_frames_remaining,
        }

    @staticmethod
    def _default_silero():
        if os.getenv("MICROPHONE_VAD_PROVIDER", "silero").strip().casefold() in {
            "rms", "adaptive_rms", "off", "none"
        }:
            return None
        try:
            return SileroSpeechGate()
        except Exception:
            return None

    def _silero_available(self) -> bool:
        return self.silero is not None and bool(getattr(self.silero, "available", True))

    def _start_threshold(self) -> float:
        if not self.adaptive_noise:
            return self.threshold
        return max(self.threshold, self.noise_floor * self.speech_start_multiplier)

    def _hold_threshold(self) -> float:
        if not self.adaptive_noise:
            return self.threshold
        return max(self.threshold, self.noise_floor * self.speech_hold_multiplier)

    def _update_noise_floor(self, rms: float) -> None:
        if not self.adaptive_noise or rms > self._start_threshold():
            return
        self._adapt_noise_floor(rms)

    def _adapt_noise_floor(self, rms: float, *, alpha: float | None = None) -> None:
        if not self.adaptive_noise:
            return
        update_alpha = min(1.0, max(0.001, alpha or self.noise_floor_alpha))
        self.noise_floor = (1.0 - update_alpha) * self.noise_floor + update_alpha * rms

    def _speech_detected(self, audio_bytes: bytes, *, active: bool) -> bool:
        rms_threshold = self._hold_threshold() if active else self._start_threshold()
        energy_speech = self.last_rms >= rms_threshold
        if not energy_speech:
            self.last_silero_probability = None
            return False
        if self.silero is None:
            return True
        probability_fn = getattr(self.silero, "speech_probability", None)
        if not callable(probability_fn):
            return True
        try:
            probability = probability_fn(audio_bytes, self.sample_rate)
        except Exception:
            logger.debug("Silero VAD frame failed; using adaptive RMS fallback", exc_info=True)
            self.last_silero_probability = None
            return True
        if probability is None:
            self.last_silero_probability = None
            return True
        self.last_silero_probability = round(float(probability), 4)
        return float(probability) >= self.silero_threshold

    @staticmethod
    def _rms(audio_bytes: bytes) -> float:
        return VoiceActivityDetector._levels(audio_bytes)[0]

    @staticmethod
    def _levels(audio_bytes: bytes) -> tuple[float, float]:
        try:
            import numpy as np

            samples = np.frombuffer(audio_bytes, dtype=np.float32)
            if samples.size == 0:
                return 0.0, 0.0
            return (
                float(math.sqrt(float(np.mean(samples * samples)))),
                float(np.max(np.abs(samples))),
            )
        except ImportError as error:
            raise MicrophoneError("numpy is required for microphone VAD") from error


class VoiceListeningService:
    """Persistent recorder that emits bounded, completed speech utterances."""

    def __init__(
        self,
        *,
        recorder_factory: Callable[[str], MicrophoneRecorder] | None = None,
        transcriber: Transcriber | None = None,
        on_transcription: Callable[[str], None] | None = None,
        device_name: str = DEFAULT_MICROPHONE_DEVICE,
        vad: VoiceActivityDetector | None = None,
        student_names: tuple[str, ...] | None = None,
    ) -> None:
        self.device_name = device_name
        self._recorder_factory = recorder_factory or (lambda device: SoundDeviceRecorder(device))
        self._transcriber = transcriber or SpeechToTextService(student_names=student_names)
        self._on_transcription = on_transcription
        self._vad = vad or VoiceActivityDetector()
        self._sample_rate = int(getattr(self._vad, "sample_rate", 48_000))
        self._host_api: str | None = None
        self._channels = 1
        self._lock = threading.RLock()
        self._state = "idle"
        self._active_device: str | None = None
        self._started_at: float | None = None
        self._recorder: MicrophoneRecorder | None = None
        self._utterances: queue.Queue[tuple[bytes, float, float | None, float] | None] = queue.Queue(maxsize=8)
        self._command_timing = threading.local()
        self._worker: threading.Thread | None = None
        self._stop_worker = False
        self._error: str | None = None
        self._dropped_utterances = 0
        self._accept_results = False
        self._captured_chunks = 0
        self._captured_bytes = 0
        self._non_silent_frames = 0
        self._last_rms = 0.0
        self._peak_volume = 0.0
        self._speech_started = 0
        self._speech_ended = 0
        self._utterances_detected = 0
        self._utterances_transcribed = 0
        self._last_transcript: str | None = None
        self._last_transcription_error: str | None = None
        self._last_vad_ms: float | None = None
        self._last_end_of_speech_ms: float | None = None
        self._last_queue_wait_ms: float | None = None
        self._last_utterance_end_at: float | None = None
        self._last_non_silent_at: float | None = None

    def set_student_names(self, student_names: tuple[str, ...]) -> None:
        setter = getattr(self._transcriber, "set_student_names", None)
        if setter is not None:
            setter(student_names)

    def start(self, device_name: str | None = None) -> dict:
        with self._lock:
            if self._state != "idle":
                raise MicrophoneBusyError("microphone listening is already enabled")
            self._clear_queue()
            self._vad.reset()
            begin_calibration = getattr(self._vad, "begin_calibration", None)
            if callable(begin_calibration):
                begin_calibration()
            try:
                self._vad.warmup()
            except Exception as error:
                logger.warning("Silero VAD warmup failed; adaptive RMS fallback will be used: %s", error)
            warmup = getattr(self._transcriber, "warmup", None)
            if callable(warmup):
                try:
                    warmup()
                except Exception as error:
                    self._last_transcription_error = f"{type(error).__name__}: {error}"
                    logger.exception("Whisper warmup failed; listening will continue")
            self._error = None
            self._dropped_utterances = 0
            self._captured_chunks = 0
            self._captured_bytes = 0
            self._non_silent_frames = 0
            self._last_rms = 0.0
            self._peak_volume = 0.0
            self._speech_started = 0
            self._speech_ended = 0
            self._utterances_detected = 0
            self._utterances_transcribed = 0
            self._last_transcript = None
            self._last_transcription_error = None
            self._last_vad_ms = None
            self._last_end_of_speech_ms = None
            self._last_queue_wait_ms = None
            self._last_utterance_end_at = None
            self._last_non_silent_at = None
            selected_device = device_name or self.device_name
            recorder = self._recorder_factory(selected_device)
            recorder_rate = getattr(recorder, "sample_rate", None)
            if recorder_rate:
                self._sample_rate = int(recorder_rate)
            self._vad.sample_rate = self._sample_rate
            self._stop_worker = False
            self._accept_results = True
            self._worker = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker.start()
            self._recorder = recorder
            self._active_device = selected_device
            self._started_at = time.monotonic()
            self._state = "listening"
            try:
                recorder.start(self._on_audio_chunk)
                actual_rate = getattr(recorder, "sample_rate", None)
                if actual_rate:
                    self._sample_rate = int(actual_rate)
                    self._vad.sample_rate = self._sample_rate
                actual_host_api = getattr(recorder, "host_api", None)
                if actual_host_api:
                    self._host_api = actual_host_api
            except Exception:
                self._state = "idle"
                self._recorder = None
                self._active_device = None
                self._started_at = None
                self._stop_worker = True
                self._put_sentinel()
                raise
            return self.status()

    def pause(self) -> dict:
        with self._lock:
            if self._state == "idle":
                raise MicrophoneError("microphone listening is not enabled")
            self._vad.reset()
            self._state = "paused"
            return self.status()

    def resume(self) -> dict:
        with self._lock:
            if self._state != "paused":
                raise MicrophoneError("microphone listening is not paused")
            self._state = "listening"
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            if self._state == "idle":
                return self.status()
            recorder = self._recorder
            self._recorder = None
            self._state = "stopping"
            was_active = self._vad.speech_active
            pending = self._vad.flush()
            if was_active and pending:
                self._speech_ended += 1
        for audio_bytes in pending:
            self._enqueue(audio_bytes)
        if recorder is not None:
            try:
                recorder.stop()
            except Exception as error:
                self._utterances.join()
                self._shutdown_worker()
                with self._lock:
                    self._state = "idle"
                    self._active_device = None
                    self._started_at = None
                raise MicrophoneError("microphone could not be stopped") from error
        self._utterances.join()
        self._shutdown_worker()
        with self._lock:
            self._state = "idle"
            self._active_device = None
            self._started_at = None
        return self.status()

    def cancel(self) -> dict:
        with self._lock:
            recorder = self._recorder
            self._recorder = None
            self._vad.reset()
            self._clear_queue()
            self._accept_results = False
            self._state = "stopping"
        if recorder is not None:
            recorder.stop()
        self._shutdown_worker()
        with self._lock:
            self._state = "idle"
            self._active_device = None
            self._started_at = None
        return self.status()

    def wait_for_idle(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._utterances.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._utterances.unfinished_tasks == 0

    def status(self) -> dict:
        with self._lock:
            duration = 0.0
            if self._started_at is not None:
                duration = max(0.0, time.monotonic() - self._started_at)
            return {
                "state": self._state,
                "device": self._active_device,
                "host_api": self._host_api,
                "sample_rate": self._sample_rate,
                "channels": self._channels,
                "duration_s": round(duration, 3),
                "queued_utterances": self._utterances.qsize(),
                "dropped_utterances": self._dropped_utterances,
                "error": self._error,
                "captured_chunks": self._captured_chunks,
                "captured_bytes": self._captured_bytes,
                "last_rms": round(self._last_rms, 6),
                "peak_volume": round(self._peak_volume, 6),
                "non_silent_frames": self._non_silent_frames,
                "vad_threshold": self._vad.threshold,
                "vad_silence_ms": self._vad.silence_ms,
                "vad_speech_active": self._vad.speech_active,
                "vad": self._vad.diagnostics(),
                "speech_started": self._speech_started,
                "speech_ended": self._speech_ended,
                "utterances_detected": self._utterances_detected,
                "utterances_transcribed": self._utterances_transcribed,
                "last_transcript": self._last_transcript,
                "last_transcription_error": self._last_transcription_error,
                "latency_ms": {
                    "vad": self._last_vad_ms,
                    "end_of_speech": self._last_end_of_speech_ms,
                    "audio_preprocessing": self._transcriber_diagnostics().get("last_preprocessing_ms"),
                    "whisper_transcription": self._transcriber_diagnostics().get("last_transcription_ms"),
                    "utterance_queue_wait": self._last_queue_wait_ms,
                },
                "whisper": self._transcriber_diagnostics(),
            }

    def shutdown(self) -> None:
        try:
            self.cancel()
        except Exception:
            pass

    def _on_audio_chunk(self, audio_bytes: bytes) -> None:
        with self._lock:
            if self._state != "listening":
                return
            self._captured_chunks += 1
            self._captured_bytes += len(audio_bytes)
            was_active = self._vad.speech_active
            hold_threshold = self._vad._hold_threshold()
        vad_started = time.perf_counter()
        utterances = self._vad.process(audio_bytes)
        self._last_vad_ms = round((time.perf_counter() - vad_started) * 1000, 2)
        with self._lock:
            rms, peak = self._vad.last_rms, self._vad.last_peak
            self._last_rms = rms
            self._peak_volume = max(self._peak_volume, peak)
            if rms >= hold_threshold:
                self._non_silent_frames += 1
            if self._vad.last_speech_detected:
                self._last_non_silent_at = time.perf_counter()
            is_active = self._vad.speech_active
            if not was_active and is_active:
                self._speech_started += 1
                logger.info("VAD speech started rms=%.5f peak=%.5f threshold=%.5f", rms, peak, self._vad.threshold)
            if was_active and (not is_active or utterances):
                self._speech_ended += 1
        for utterance in utterances:
            with self._lock:
                self._utterances_detected += 1
                utterance_end = time.perf_counter()
                self._last_utterance_end_at = utterance_end
                if self._last_non_silent_at is not None:
                    self._last_end_of_speech_ms = round(
                        max(0.0, utterance_end - self._last_non_silent_at) * 1000, 2
                    )
            logger.info("VAD completed utterance bytes=%s sample_rate=%s", len(utterance), self._sample_rate)
            self._enqueue(utterance, self._last_non_silent_at, self._last_vad_ms)

    def _enqueue(self, audio_bytes: bytes, last_speech_at: float | None = None, vad_ms: float = 0.0) -> None:
        if not audio_bytes:
            return
        try:
            self._utterances.put_nowait((audio_bytes, time.perf_counter(), last_speech_at, vad_ms))
        except queue.Full:
            with self._lock:
                self._dropped_utterances += 1
                self._error = "utterance_queue_full"

    def _worker_loop(self) -> None:
        while True:
            item = self._utterances.get()
            try:
                if item is None:
                    return
                audio_bytes, queued_at, last_speech_at, vad_ms = item
                self._last_queue_wait_ms = round((time.perf_counter() - queued_at) * 1000, 2)
                self._command_timing.speech_at = last_speech_at
                self._command_timing.stages = {
                    "vad": vad_ms,
                    "end_of_speech": round((queued_at - last_speech_at) * 1000, 2) if last_speech_at is not None else None,
                    "utterance_queue_wait": self._last_queue_wait_ms,
                }
                try:
                    text = self._transcribe(audio_bytes).strip()
                    with self._lock:
                        if text:
                            self._utterances_transcribed += 1
                            self._last_transcript = text
                    logger.info("Transcription completed text=%r", text)
                    if text and self._on_transcription is not None and self._accept_results:
                        self._on_transcription(text)
                except Exception as error:
                    with self._lock:
                        self._error = type(error).__name__
                        self._last_transcription_error = f"{type(error).__name__}: {error}"
                    logger.exception("Transcription failed")
            finally:
                self._utterances.task_done()

    def current_command_timing(self) -> tuple[float | None, dict]:
        diagnostics = self._transcriber_diagnostics()
        return getattr(self._command_timing, "speech_at", None), {
            **getattr(self._command_timing, "stages", {}),
            "audio_preprocessing": diagnostics.get("last_preprocessing_ms"),
            "whisper_transcription": diagnostics.get("last_transcription_ms"),
        }

    def _transcribe(self, audio_bytes: bytes) -> str:
        transcribe = self._transcriber.transcribe
        try:
            signature = inspect.signature(transcribe)
            positional = [
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            accepts_two = any(
                parameter.kind == inspect.Parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            ) or len(positional) >= 2
        except (TypeError, ValueError):
            accepts_two = True
        if accepts_two:
            return transcribe(audio_bytes, self._sample_rate)
        return transcribe(audio_bytes)

    def _shutdown_worker(self) -> None:
        self._stop_worker = True
        self._put_sentinel()
        if self._worker is not None:
            self._worker.join(timeout=5.0)
        self._worker = None

    def _put_sentinel(self) -> None:
        try:
            self._utterances.put_nowait(None)
        except queue.Full:
            # stop() has already joined the queued work before shutdown.
            pass

    def _clear_queue(self) -> None:
        while True:
            try:
                self._utterances.get_nowait()
                self._utterances.task_done()
            except queue.Empty:
                return

    def _transcriber_diagnostics(self) -> dict:
        diagnostics = getattr(self._transcriber, "diagnostics", None)
        if not callable(diagnostics):
            return {}
        try:
            return diagnostics()
        except Exception:
            return {}
