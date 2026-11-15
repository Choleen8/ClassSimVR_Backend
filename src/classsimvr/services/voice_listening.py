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


class VoiceActivityDetector:
    """Small RMS-based VAD suitable for push-to-listen classroom speech."""

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        frame_ms: int = 20,
        silence_ms: int | None = None,
        threshold: float | None = None,
        pre_roll_ms: int = 200,
        max_utterance_ms: int = 20_000,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.silence_ms = silence_ms if silence_ms is not None else int(os.getenv("MICROPHONE_VAD_SILENCE_MS", "800"))
        self.threshold = threshold if threshold is not None else float(os.getenv("MICROPHONE_VAD_THRESHOLD", "0.015"))
        self._pre_roll = deque(maxlen=max(1, pre_roll_ms // frame_ms))
        self._max_utterance_ms = max_utterance_ms
        self._current: list[bytes] = []
        self._current_ms = 0
        self._silence_ms = 0
        self._speech_active = False
        self.last_rms = 0.0
        self.last_peak = 0.0

    def process(self, audio_bytes: bytes) -> list[bytes]:
        if not audio_bytes:
            return []
        duration_ms = self._duration_ms(audio_bytes)
        if duration_ms <= 0:
            return []
        self.last_rms, self.last_peak = self._levels(audio_bytes)
        speaking = self.last_rms >= self.threshold
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

    @property
    def speech_active(self) -> bool:
        return self._speech_active

    def _duration_ms(self, audio_bytes: bytes) -> int:
        sample_count = len(audio_bytes) // 4
        return int(sample_count * 1_000 / self.sample_rate)

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
    ) -> None:
        self.device_name = device_name
        self._recorder_factory = recorder_factory or (lambda device: SoundDeviceRecorder(device))
        self._transcriber = transcriber or SpeechToTextService()
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
        self._utterances: queue.Queue[tuple[bytes, float] | None] = queue.Queue(maxsize=8)
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

    def start(self, device_name: str | None = None) -> dict:
        with self._lock:
            if self._state != "idle":
                raise MicrophoneBusyError("microphone listening is already enabled")
            self._clear_queue()
            self._vad.reset()
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
        rms, peak = self._vad._levels(audio_bytes)
        with self._lock:
            self._last_rms = rms
            self._peak_volume = max(self._peak_volume, peak)
            if rms >= self._vad.threshold:
                self._non_silent_frames += 1
                self._last_non_silent_at = time.perf_counter()
            was_active = self._vad.speech_active
        vad_started = time.perf_counter()
        utterances = self._vad.process(audio_bytes)
        self._last_vad_ms = round((time.perf_counter() - vad_started) * 1000, 2)
        with self._lock:
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
            self._enqueue(utterance)

    def _enqueue(self, audio_bytes: bytes) -> None:
        if not audio_bytes:
            return
        try:
            self._utterances.put_nowait((audio_bytes, time.perf_counter()))
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
                audio_bytes, queued_at = item
                self._last_queue_wait_ms = round((time.perf_counter() - queued_at) * 1000, 2)
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
