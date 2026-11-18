from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

import edge_tts
from dotenv import load_dotenv


DEFAULT_VOICE_BY_PERSONA = {
    "maria-sandoval": "fil-PH-BlessicaNeural",
    "berto": "fil-PH-AngeloNeural",
    "berto-eco": "fil-PH-AngeloNeural",
    "robert": "fil-PH-AngeloNeural",
    "robert-eco": "fil-PH-AngeloNeural",
    "andrea-guinto": "en-US-JennyNeural",
}


class TTSConfigurationError(RuntimeError):
    """Raised when the text-to-speech service is not configured."""


class TextToSpeechService:
    """Generate local MP3 files with Microsoft Edge's hosted TTS voices."""

    _CACHEABLE_TEXTS = {
        "Yes, Ma'am.",
        "Yes po, Ma'am.",
        "Opo, Ma'am.",
        "Yes, Ma'am?",
        "Yes, Ma'am!",
        "Ano po, Ma'am?",
        "Sorry, I did not understand. Could you please repeat that?",
        "Pasensya na po, hindi ko naintindihan. Maaari mo bang ulitin?",
        "Sorry po, hindi ko naintindihan. Could you please repeat that?",
    }

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        public_base_url: str | None = None,
        voice_by_persona: dict[str, str] | None = None,
        default_voice: str | None = None,
    ) -> None:
        load_dotenv()
        self.output_dir = output_dir or Path(__file__).resolve().parents[3] / "audio"
        self.public_base_url = (
            public_base_url or os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
        ).rstrip("/")
        self.voice_by_persona = {
            key.strip().lower(): value
            for key, value in (voice_by_persona or DEFAULT_VOICE_BY_PERSONA).items()
        }
        self.default_voice = default_voice or os.getenv(
            "EDGE_TTS_DEFAULT_VOICE", "fil-PH-BlessicaNeural"
        )
        self._cache: dict[tuple[str, str], str] = {}
        self._cache_lock = threading.RLock()
        self._cache_hits = 0
        self._last_generation_ms: float | None = None

    def _voice_for_persona(
        self, persona_id: str | None, persona_name: str | None = None, voice: str | None = None
    ) -> str:
        if voice and voice.strip():
            return voice.strip()
        candidates = (
            (persona_id or "").strip().lower(),
            (persona_name or "").strip().lower().replace(" ", "-"),
        )
        for candidate in candidates:
            if candidate in self.voice_by_persona:
                return self.voice_by_persona[candidate]
        return self.voice_by_persona.get(
            (persona_id or "").strip().lower(), self.default_voice
        )

    async def _save_audio(self, text: str, voice: str, path: Path) -> None:
        communicator = edge_tts.Communicate(text, voice)
        await communicator.save(str(path))

    @staticmethod
    def _run_async(coroutine) -> None:
        # On Windows/Python 3.13, creating the default Proactor loop can block
        # in restricted desktop environments while opening its self-pipe. The
        # selector loop is sufficient for Edge TTS HTTP streaming and avoids
        # that startup stall without changing the synchronous route contract.
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                runner.run(coroutine)
            return
        asyncio.run(coroutine)

    def synthesize(
        self,
        text: str,
        file_stem: str,
        *,
        persona_id: str | None = None,
        persona_name: str | None = None,
        voice: str | None = None,
    ) -> str:
        if not text.strip():
            raise ValueError("TTS text must not be blank")

        safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", file_stem).strip("_") or "turn"
        filename = f"{safe_stem}-{uuid.uuid4().hex}.mp3"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / filename
        voice = self._voice_for_persona(persona_id, persona_name, voice)

        if text.strip() in self._CACHEABLE_TEXTS:
            cache_key = (text.strip(), voice)
            with self._cache_lock:
                cached = self._cache.get(cache_key)
                if cached and (self.output_dir / cached.rsplit("/", 1)[-1]).exists():
                    self._cache_hits += 1
                    self._last_generation_ms = 0.0
                    return cached

        # The FastAPI behavior handlers are synchronous by design. Edge TTS is
        # async, so run one synthesis operation without changing the route API.
        started = time.perf_counter()
        self._run_async(self._save_audio(text, voice, output_path))
        self._last_generation_ms = round((time.perf_counter() - started) * 1000, 2)
        audio_url = f"{self.public_base_url}/audio/{filename}"
        if text.strip() in self._CACHEABLE_TEXTS:
            with self._cache_lock:
                self._cache[(text.strip(), voice)] = audio_url
        return audio_url

    def diagnostics(self) -> dict:
        return {
            "last_generation_ms": self._last_generation_ms,
            "cache_hits": self._cache_hits,
            "cached_phrases": len(self._cache),
        }

