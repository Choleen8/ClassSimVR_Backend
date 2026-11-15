from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol
import logging
import os

DEFAULT_MICROPHONE_DEVICE = "Headset Microphone (Oculus Virtual Audio Device)"
logger = logging.getLogger(__name__)


class MicrophoneError(RuntimeError):
    pass


class MicrophoneBusyError(MicrophoneError):
    pass


class MicrophoneUnavailableError(MicrophoneError):
    pass


class MicrophoneSilenceError(MicrophoneError):
    pass


class MicrophoneTranscriptionError(MicrophoneError):
    pass


class MicrophoneRecorder(Protocol):
    def start(self, callback: Callable[[bytes], None]) -> None: ...
    def stop(self) -> None: ...


class Transcriber(Protocol):
    def transcribe(self, audio_bytes: bytes) -> str: ...


class SoundDeviceRecorder:
    def __init__(
        self,
        device_name: str,
        *,
        sample_rate: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.device_name = device_name
        self.sample_rate = sample_rate
        self.max_retries = max(1, max_retries or int(os.getenv("MICROPHONE_START_RETRIES", "3")))
        self.device_index: int | None = None
        self.host_api: str | None = None
        self._stream = None

    def start(self, callback: Callable[[bytes], None]) -> None:
        try:
            import sounddevice as sd
        except ImportError as error:
            raise MicrophoneUnavailableError(
                "sounddevice is not installed in the backend environment"
            ) from error

        candidates = self._resolve_input_devices(sd)
        failures: list[str] = []
        for device_index, device_info in candidates:
            host_api = sd.query_hostapis(device_info["hostapi"])["name"]
            for selected_rate in self._candidate_sample_rates(device_info):
                for attempt in range(1, self.max_retries + 1):
                    stream = None
                    try:
                        extra_settings = None
                        if host_api.casefold() == "windows wasapi":
                            # Avoid WDM-KS exclusive/open conflicts with Quest Link,
                            # Unreal, and other Windows audio clients.
                            extra_settings = sd.WasapiSettings(exclusive=False)
                        sd.check_input_settings(
                            device=device_index,
                            channels=1,
                            dtype="float32",
                            samplerate=selected_rate,
                            extra_settings=extra_settings,
                        )
                        logger.info(
                            "Opening microphone device name=%s index=%s host_api=%s mode=%s sample_rate=%s channels=1 attempt=%s/%s",
                            self.device_name,
                            device_index,
                            host_api,
                            "shared" if extra_settings is not None else "default",
                            selected_rate,
                            attempt,
                            self.max_retries,
                        )
                        stream = sd.InputStream(
                            device=device_index,
                            samplerate=selected_rate,
                            channels=1,
                            dtype="float32",
                            extra_settings=extra_settings,
                            callback=lambda indata, _frames, _time, _status: callback(indata.copy().tobytes()),
                        )
                        stream.start()
                        self._stream = stream
                        self.device_index = device_index
                        self.sample_rate = selected_rate
                        self.host_api = host_api
                        return
                    except Exception as error:
                        failures.append(
                            f"index={device_index} host={host_api} rate={selected_rate} attempt={attempt}: {error}"
                        )
                        logger.warning(
                            "Microphone attempt failed name=%s index=%s host_api=%s rate=%s attempt=%s/%s: %s",
                            self.device_name,
                            device_index,
                            host_api,
                            selected_rate,
                            attempt,
                            self.max_retries,
                            error,
                        )
                        if stream is not None:
                            try:
                                stream.stop()
                            except Exception:
                                pass
                            try:
                                stream.close()
                            except Exception:
                                pass
                        time.sleep(0.1)
        self._stream = None
        logger.error(
            "Microphone initialization failed name=%s candidates=%s failures=%s",
            self.device_name,
            [index for index, _info in candidates],
            failures,
        )
        raise MicrophoneUnavailableError(
            f"unable to open microphone device '{self.device_name}' after {len(failures)} attempts"
        )

    def _resolve_input_devices(self, sd):
        requested = self.device_name.casefold().strip()
        matches = []
        for index, info in enumerate(sd.query_devices()):
            if info["max_input_channels"] <= 0:
                continue
            if info["name"].casefold().strip() == requested:
                matches.append((index, info))
        if not matches:
            raise MicrophoneUnavailableError(
                f"input device '{self.device_name}' was not found"
            )
        wasapi_matches = [
            item
            for item in matches
            if sd.query_hostapis(item[1]["hostapi"])["name"].casefold() == "windows wasapi"
        ]
        directsound_matches = [
            item
            for item in matches
            if sd.query_hostapis(item[1]["hostapi"])["name"].casefold() == "windows directsound"
        ]
        ordered = wasapi_matches + directsound_matches + [item for item in matches if item not in wasapi_matches + directsound_matches]
        if len(matches) > 1:
            logger.info(
                "Multiple microphone matches found name=%s matches=%s ordered_candidates=%s",
                self.device_name,
                [index for index, _info in matches],
                [index for index, _info in ordered],
            )
        return ordered

    def _candidate_sample_rates(self, device_info) -> list[int]:
        preferred = self.sample_rate or int(device_info["default_samplerate"])
        return list(dict.fromkeys([preferred, 48_000, 44_100, 16_000]))

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
        finally:
            self._stream.close()
            self._stream = None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None = None


class MicrophoneManager:
    def __init__(
        self,
        *,
        recorder_factory: Callable[[str], MicrophoneRecorder] | None = None,
        transcriber: Transcriber | None = None,
        device_name: str = DEFAULT_MICROPHONE_DEVICE,
    ) -> None:
        self.device_name = device_name
        self._recorder_factory = recorder_factory or (lambda device: SoundDeviceRecorder(device))
        if transcriber is None:
            from .speech_to_text import SpeechToTextService

            transcriber = SpeechToTextService()
        self._transcriber = transcriber
        self._lock = threading.RLock()
        self._state = "idle"
        self._started_at: float | None = None
        self._active_device: str | None = None
        self._recorder: MicrophoneRecorder | None = None
        self._chunks: list[bytes] = []
        self._error: str | None = None

    def start(self, device_name: str | None = None) -> dict:
        with self._lock:
            if self._state != "idle":
                raise MicrophoneBusyError("microphone is already busy")
            selected_device = device_name or self.device_name
            recorder = self._recorder_factory(selected_device)
            self._chunks = []
            self._error = None
            self._state = "recording"
            self._started_at = time.monotonic()
            self._active_device = selected_device
            self._recorder = recorder
            try:
                recorder.start(self._chunks.append)
            except Exception:
                self._state = "idle"
                self._started_at = None
                self._active_device = None
                self._recorder = None
                raise
            return self.status()

    def stop(self) -> TranscriptionResult:
        with self._lock:
            if self._state != "recording" or self._recorder is None:
                raise MicrophoneError("microphone is not recording")
            self._state = "transcribing"
            recorder = self._recorder
            self._recorder = None
        succeeded = False
        try:
            recorder.stop()
            audio_bytes = b"".join(self._chunks)
            try:
                text = self._transcriber.transcribe(audio_bytes).strip()
            except MicrophoneError:
                raise
            except Exception as error:
                raise MicrophoneTranscriptionError("speech transcription failed") from error
            if not text:
                raise MicrophoneSilenceError("no speech was detected")
            succeeded = True
            return TranscriptionResult(text=text)
        except Exception as error:
            with self._lock:
                self._error = type(error).__name__
            raise
        finally:
            with self._lock:
                self._chunks = []
                self._started_at = None
                self._active_device = None
                self._state = "processing" if succeeded else "idle"

    def cancel(self) -> dict:
        with self._lock:
            recorder = self._recorder
            self._recorder = None
            self._chunks = []
            self._started_at = None
            self._active_device = None
            self._state = "idle"
        if recorder is not None:
            try:
                recorder.stop()
            except Exception as error:
                raise MicrophoneError("microphone could not be stopped") from error
        return self.status()

    def begin_processing(self) -> dict:
        with self._lock:
            if self._state != "idle":
                raise MicrophoneBusyError("microphone is busy")
            self._state = "processing"
            return self.status()

    def finish_processing(self) -> dict:
        with self._lock:
            self._state = "idle"
            self._started_at = None
            self._active_device = None
            return self.status()

    def status(self) -> dict:
        with self._lock:
            duration = 0.0
            if self._started_at is not None:
                duration = max(0.0, time.monotonic() - self._started_at)
            return {
                "state": self._state,
                "device": self._active_device,
                "duration_s": round(duration, 3),
                "error": self._error,
            }

    def shutdown(self) -> None:
        try:
            self.cancel()
        except Exception:
            # Shutdown must not prevent the FastAPI process from closing.
            pass
