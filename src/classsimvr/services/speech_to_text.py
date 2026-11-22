from __future__ import annotations

import os
import sys
import wave
import logging
import math
import re
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from classsimvr.services.student_registry import DEFAULT_STUDENT_PROFILES

logger = logging.getLogger(__name__)

# Acoustic ambiguities are decoder hints, never registry aliases. In particular,
# adding "one" as Juan's alias would turn ordinary arithmetic into student calls.
_SHORT_NAME_HOMOPHONES = {"juan": {"one", "1"}}


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
        self.retry_short_names = os.getenv(
            "WHISPER_RETRY_SHORT_NAMES", "true"
        ).strip().casefold() not in {"0", "false", "no", "off"}
        self.short_name_min_logprob = float(os.getenv("WHISPER_SHORT_NAME_MIN_LOGPROB", "-0.75"))
        self._last_short_name_resolution: dict | None = None
        self._last_detected_language: str | None = None
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
        self._last_short_name_resolution = None
        self._last_detected_language = None
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
        self._last_detected_language = str(getattr(info, "language", "")) or None
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
        if self.language in {None, "en", "tl"}:
            text = self._resolve_short_name(model, audio, text)
        return text

    @staticmethod
    def _name_key(text: str) -> str:
        return " ".join(re.findall(r"\w+", text.casefold()))

    def _resolve_short_name(self, model, audio, text: str) -> str:
        if not self.retry_short_names or len(audio) > 4 * 16_000:
            return text
        normalized = self._name_key(text)
        full_names = self._student_names or tuple(student.name for student in DEFAULT_STUDENT_PROFILES)
        matches = []
        for name in full_names:
            words = self._name_key(name).split()
            if words and normalized in _SHORT_NAME_HOMOPHONES.get(words[0], set()):
                matches.append(name)
        # A first name shared by two students is not enough to select either one.
        if len(matches) != 1:
            return text
        name, = matches
        accepted_names = {self._name_key(name), self._name_key(name).split()[0]}
        resolution = {
            "original": text, "candidate_name": name, "decision": "unresolved",
            "candidates": [], "hint": f"Please repeat the full student name: {name}.",
        }
        self._last_short_name_resolution = resolution
        agreed_name = None
        # Decode the SAME audio at two search widths in its detected language.
        # Forcing Tagalog on a short English name can erase valid acoustic
        # evidence. A hotword is only a hint: accept a name-only result only when
        # both searches agree and have usable speech/confidence scores.
        # Never rewrite a numbered sentence or
        # infer a physical command from an ambiguous single word.
        language = self.language or (
            self._last_detected_language if self._last_detected_language in {"en", "tl"} else None
        )
        for beam_size in (5, 3):
            quality = {}
            try:
                candidate, _ = self._transcribe_once(
                    model, audio, language=language, hotwords=name, quality=quality,
                    beam_size=beam_size,
                )
            except Exception:
                logger.warning("Short-name retry failed language=%s; preserving transcript=%r",
                               language, text, exc_info=True)
                return text
            resolution["candidates"].append({
                "language": language or "auto", "beam_size": beam_size,
                "text": candidate, **quality,
            })
            candidate_key = self._name_key(candidate)
            score = quality.get("avg_logprob")
            no_speech = quality.get("no_speech_prob")
            confident = (
                score is not None and math.isfinite(score) and score >= self.short_name_min_logprob
                and no_speech is not None and math.isfinite(no_speech) and no_speech <= 0.35
            )
            logger.info("Short-name evidence original=%r candidate=%r language=%s quality=%s",
                        text, candidate, language, quality)
            if candidate_key not in accepted_names or not confident:
                logger.info("Short-name ambiguity retained transcript=%r hint=%s", text, resolution["hint"])
                return text
            if agreed_name is not None and candidate_key != agreed_name:
                return text
            agreed_name = candidate_key
        resolution["decision"] = "recovered"
        resolution["hint"] = None
        # Return the registered spelling, rather than a number alias or a model-
        # invented ID. StudentRegistry still enforces the final student identity.
        recovered = name if agreed_name == self._name_key(name) else name.split()[0]
        logger.info("Short-name recovered original=%r student_name=%r", text, recovered)
        return recovered

    def _transcribe_once(
        self, model, audio, *, language: str | None,
        hotwords: str | None = None, quality: dict | None = None, beam_size: int = 5,
    ):
        hints = {"hotwords": hotwords} if hotwords else {}
        segments, info = model.transcribe(
            audio,
            language=language,
            task="transcribe",
            vad_filter=True,
            beam_size=beam_size,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=self._initial_prompt(),
            **hints,
        )
        segments = list(segments)
        if quality is not None:
            for field, choose in (("avg_logprob", min), ("no_speech_prob", max)):
                values = [getattr(segment, field, None) for segment in segments]
                quality[field] = choose(values) if values and all(
                    isinstance(value, (int, float)) and math.isfinite(value) for value in values
                ) else None
        return " ".join(segment.text.strip() for segment in segments).strip(), info

    def _initial_prompt(self) -> str:
        full_names = self._student_names or tuple(student.name for student in DEFAULT_STUDENT_PROFILES)
        names = ", ".join(full_names)
        first_names = ", ".join(dict.fromkeys(name.split()[0] for name in full_names))
        return (
            "Classroom speech in English, Filipino, Tagalog, or Taglish. "
            "Recognize short student names exactly as spoken. Student names include "
            f"{names}. First-name calls include {first_names}. "
            "Preserve the spoken names and distinguish them from ordinary numbers using context."
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
            "retry_short_names": self.retry_short_names,
            "short_name_min_logprob": self.short_name_min_logprob,
            "short_name_resolution": self._last_short_name_resolution,
            "last_detected_language": self._last_detected_language,
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
