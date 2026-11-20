from __future__ import annotations

import os
import sys
import wave
import logging
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from classsimvr.services.student_registry import DEFAULT_STUDENT_PROFILES

logger = logging.getLogger(__name__)


class SpeechToTextUnavailableError(RuntimeError):
    """The optional local speech-to-text dependency is not available."""


class SpeechToTextService:
    """Lazy local Whisper adapter for English, Filipino, and Taglish audio."""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        student_names: Iterable[str] | None = None,
    ) -> None:
        self.model_name = model_name or os.getenv("WHISPER_MODEL", "small")
        self.requested_device = os.getenv("WHISPER_DEVICE", "cpu").strip().lower()
        self.device = self.requested_device
        self.compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
        self._cuda_libraries = []
        self._fallback_reason: str | None = None
        configured_language = os.getenv("WHISPER_LANGUAGE", "auto").strip().casefold()
        self.language = None if configured_language in {"", "auto", "none"} else configured_language
        self.retry_tagalog_on_indonesian = os.getenv(
            "WHISPER_RETRY_TAGALOG_ON_INDONESIAN", "true"
        ).strip().casefold() not in {"0", "false", "no", "off"}
        self.retry_tagalog_on_arabic_short_utterance = os.getenv(
            "WHISPER_RETRY_TAGALOG_ON_ARABIC_SHORT_UTTERANCE", "true"
        ).strip().casefold() not in {"0", "false", "no", "off"}
        self._model = None
        self._model_lock = threading.Lock()
        self._model_load_ms: float | None = None
        self._last_preprocessing_ms: float | None = None
        self._last_transcription_ms: float | None = None
        self._student_names = tuple(dict.fromkeys(
            str(name).strip() for name in (student_names or ()) if str(name).strip()
        ))

    def set_student_names(self, student_names: Iterable[str]) -> None:
        """Refresh the prompt vocabulary without reloading the Whisper model."""
        self._student_names = tuple(dict.fromkeys(
            str(name).strip() for name in student_names if str(name).strip()
        ))

    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16_000) -> str:
        if not audio_bytes:
            return ""
        model = self._get_model()
        preprocessing_started = time.perf_counter()
        audio = self._to_whisper_audio(audio_bytes, sample_rate=sample_rate)
        self._last_preprocessing_ms = round((time.perf_counter() - preprocessing_started) * 1000, 2)
        transcription_started = time.perf_counter()
        try:
            text = self._recognize(model, audio)
        except RuntimeError as error:
            if self.requested_device != "auto" or self.device != "cuda" or not any(
                token in str(error).lower() for token in ("cuda", "cublas", "cudnn", "out of memory")
            ):
                raise
            with self._model_lock:
                if self._model is model:
                    self._switch_to_cpu(error)
            text = self._recognize(self._get_model(), audio)
        self._last_transcription_ms = round((time.perf_counter() - transcription_started) * 1000, 2)
        logger.info("Local transcription completed device=%s model=%s sample_rate=%s text=%r",
                    self.device, self.model_name, sample_rate, text)
        return text

    def _recognize(self, model, audio) -> str:
        text, info = self._transcribe_once(model, audio, language=self.language)
        if self.language is None:
            detected_language = str(getattr(info, "language", "")).casefold()
            if self.retry_tagalog_on_indonesian and detected_language in {
                "id", "indonesian", "ms", "malay"
            }:
                logger.warning(
                    "Whisper detected %s; retrying utterance with Tagalog language hint",
                    detected_language,
                )
                tagalog_text, _ = self._transcribe_once(model, audio, language="tl")
                if tagalog_text:
                    text = tagalog_text
            elif (
                self.retry_tagalog_on_arabic_short_utterance
                and detected_language in {"ar", "arabic", "fa", "persian", "ur", "urdu"}
                and self._is_short_utterance(text)
            ) or (
                self.retry_tagalog_on_arabic_short_utterance
                and self._contains_arabic_script(text)
                and self._is_short_utterance(text)
            ):
                logger.warning(
                    "Whisper produced an Arabic-script short utterance; retrying with Tagalog language hint"
                )
                tagalog_text, _ = self._transcribe_once(model, audio, language="tl")
                if tagalog_text and not self._contains_arabic_script(tagalog_text):
                    text = tagalog_text
        return text

    def _transcribe_once(self, model, audio, *, language: str | None):
        segments, info = model.transcribe(
            audio,
            language=language,
            task="transcribe",
            vad_filter=True,
            beam_size=5,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=self._initial_prompt(),
        )
        return " ".join(segment.text.strip() for segment in segments).strip(), info

    def _initial_prompt(self) -> str:
        names = ", ".join(self._student_names)
        if not names:
            names = ", ".join(student.name for student in DEFAULT_STUDENT_PROFILES)
        return (
            "Classroom speech in English, Filipino, Tagalog, or Taglish. "
            "Recognize short student names exactly as spoken. Student names include "
            f"{names}."
        )

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise SpeechToTextUnavailableError(
                    "faster-whisper is not installed in the backend environment"
                ) from error
            started = time.perf_counter()
            if self.device == "auto":
                import ctranslate2
                self.device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
                if self.device == "cpu":
                    self.compute_type = "int8"
            if self.device == "cuda":
                try:
                    self._load_windows_cublas()
                    candidate = WhisperModel(self.model_name, device="cuda", compute_type=self.compute_type)
                    # Loading weights alone succeeds even when CUDA cannot execute a kernel.
                    import numpy as np
                    segments, _ = candidate.transcribe(
                        np.zeros(16000, dtype=np.float32), language="en", beam_size=1,
                        vad_filter=False, condition_on_previous_text=False,
                    )
                    list(segments)
                    self._model = candidate
                except (RuntimeError, OSError) as error:
                    if self.requested_device != "auto":
                        raise
                    self._switch_to_cpu(error)
            if self._model is None:
                self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)
            self._model_load_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "Whisper model loaded model=%s device=%s compute_type=%s load_ms=%.2f",
                self.model_name,
                self.device,
                self.compute_type,
                self._model_load_ms,
            )
        return self._model

    def _switch_to_cpu(self, error: Exception) -> None:
        self._model = None
        self.device = "cpu"
        self.compute_type = "int8"
        self._fallback_reason = str(error)
        logger.warning("GPU transcription unavailable, using CPU int8: %s", error)

    def _load_windows_cublas(self) -> None:
        if sys.platform != "win32" or self._cuda_libraries:
            return
        import ctypes
        import sysconfig

        directory = Path(sysconfig.get_path("purelib")) / "nvidia" / "cublas" / "bin"
        if (directory / "cublas64_12.dll").is_file():
            for name in ("cublasLt64_12.dll", "cublas64_12.dll"):
                self._cuda_libraries.append(ctypes.WinDLL(str(directory / name)))

    def warmup(self) -> None:
        """Load Whisper once before the first utterance is spoken."""
        if self._model is None:
            self._model = self._get_model()

    def diagnostics(self) -> dict:
        return {
            "model": self.model_name,
            "device": self.device,
            "requested_device": self.requested_device,
            "device_fallback": self._fallback_reason,
            "compute_type": self.compute_type,
            "language": self.language or "auto",
            "retry_tagalog_on_indonesian": self.retry_tagalog_on_indonesian,
            "retry_tagalog_on_arabic_short_utterance": self.retry_tagalog_on_arabic_short_utterance,
            "model_loaded": self._model is not None,
            "model_load_ms": self._model_load_ms,
            "last_preprocessing_ms": self._last_preprocessing_ms,
            "last_transcription_ms": self._last_transcription_ms,
        }

    @staticmethod
    def _contains_arabic_script(text: str) -> bool:
        return any("\u0600" <= character <= "\u06ff" for character in text)

    @staticmethod
    def _is_short_utterance(text: str) -> bool:
        return 0 < len(text.split()) <= 4

    @staticmethod
    def _to_whisper_audio(audio_bytes: bytes, *, sample_rate: int = 16_000):
        try:
            import numpy as np
        except ImportError as error:
            raise SpeechToTextUnavailableError(
                "numpy is not installed in the backend environment"
            ) from error
        samples = np.frombuffer(audio_bytes, dtype=np.float32)
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if sample_rate != 16_000 and samples.size:
            target_count = max(1, round(samples.size * 16_000 / sample_rate))
            source_positions = np.arange(samples.size, dtype=np.float64)
            target_positions = np.linspace(0, samples.size - 1, target_count)
            samples = np.interp(target_positions, source_positions, samples).astype(np.float32)
        return np.clip(samples, -1.0, 1.0).astype(np.float32, copy=False)

    @staticmethod
    def _write_float32_wav(path: Path, audio_bytes: bytes, *, sample_rate: int = 16_000) -> None:
        try:
            import numpy as np
        except ImportError as error:
            raise SpeechToTextUnavailableError(
                "numpy is not installed in the backend environment"
            ) from error
        samples = SpeechToTextService._to_whisper_audio(audio_bytes, sample_rate=sample_rate)
        pcm = samples
        pcm = (pcm * 32767.0).astype(np.int16)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            output.writeframes(pcm.tobytes())
