from __future__ import annotations

import os
import wave
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class SpeechToTextUnavailableError(RuntimeError):
    """The optional local speech-to-text dependency is not available."""


class SpeechToTextService:
    """Lazy local Whisper adapter for English, Filipino, and Taglish audio."""

    def __init__(self, *, model_name: str | None = None) -> None:
        self.model_name = model_name or os.getenv("WHISPER_MODEL", "small")
        self.device = os.getenv("WHISPER_DEVICE", "cpu")
        self.compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
        configured_language = os.getenv("WHISPER_LANGUAGE", "auto").strip().casefold()
        self.language = None if configured_language in {"", "auto", "none"} else configured_language
        self.retry_tagalog_on_indonesian = os.getenv(
            "WHISPER_RETRY_TAGALOG_ON_INDONESIAN", "true"
        ).strip().casefold() not in {"0", "false", "no", "off"}
        self._model = None
        self._model_lock = threading.Lock()
        self._model_load_ms: float | None = None
        self._last_preprocessing_ms: float | None = None
        self._last_transcription_ms: float | None = None

    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16_000) -> str:
        if not audio_bytes:
            return ""
        model = self._get_model()
        preprocessing_started = time.perf_counter()
        audio = self._to_whisper_audio(audio_bytes, sample_rate=sample_rate)
        self._last_preprocessing_ms = round((time.perf_counter() - preprocessing_started) * 1000, 2)
        transcription_started = time.perf_counter()
        text, info = self._transcribe_once(model, audio, language=self.language)
        if self.language is None and self.retry_tagalog_on_indonesian:
            detected_language = str(getattr(info, "language", "")).casefold()
            if detected_language in {"id", "indonesian", "ms", "malay"}:
                logger.warning(
                    "Whisper detected %s; retrying utterance with Tagalog language hint",
                    detected_language,
                )
                tagalog_text, _ = self._transcribe_once(model, audio, language="tl")
                if tagalog_text:
                    text = tagalog_text
        self._last_transcription_ms = round((time.perf_counter() - transcription_started) * 1000, 2)
        logger.info("Local transcription completed model=%s sample_rate=%s text=%r", self.model_name, sample_rate, text)
        return text

    @staticmethod
    def _transcribe_once(model, audio, *, language: str | None):
        segments, info = model.transcribe(
            audio,
            language=language,
            task="transcribe",
            vad_filter=True,
            beam_size=5,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=(
                "Classroom speech in English, Filipino, Tagalog, or Taglish. "
                "Names include Maria, Maria Sandoval, Robert, and Robert Eco."
            ),
        )
        return " ".join(segment.text.strip() for segment in segments).strip(), info

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
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
            self._model_load_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "Whisper model loaded model=%s device=%s compute_type=%s load_ms=%.2f",
                self.model_name,
                self.device,
                self.compute_type,
                self._model_load_ms,
            )
        return self._model

    def warmup(self) -> None:
        """Load Whisper once before the first utterance is spoken."""
        if self._model is None:
            self._model = self._get_model()

    def diagnostics(self) -> dict:
        return {
            "model": self.model_name,
            "device": self.device,
            "compute_type": self.compute_type,
            "language": self.language or "auto",
            "retry_tagalog_on_indonesian": self.retry_tagalog_on_indonesian,
            "model_loaded": self._model is not None,
            "model_load_ms": self._model_load_ms,
            "last_preprocessing_ms": self._last_preprocessing_ms,
            "last_transcription_ms": self._last_transcription_ms,
        }

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
