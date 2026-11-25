from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

import edge_tts
from aiohttp import ClientError
from edge_tts.exceptions import NoAudioReceived
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


DEFAULT_VOICE_BY_PERSONA = {
    "maria-sandoval": "fil-PH-BlessicaNeural",
    "berto": "fil-PH-AngeloNeural",
    "berto-eco": "fil-PH-AngeloNeural",
    "robert": "fil-PH-AngeloNeural",
    "robert-eco": "fil-PH-AngeloNeural",
    "andrea-guinto": "en-US-JennyNeural",
    "judy-castillo": "en-US-AriaNeural",
    "francis-reyes": "en-US-GuyNeural",
    "dave-santos": "en-US-AndrewNeural",
    "juan-cruz": "en-US-BrianNeural",
    "eva-mendoza": "en-US-EmmaNeural",
    "ara-villanueva": "en-US-AvaNeural",
    "kate-navarro": "en-US-MichelleNeural",
    "ayesha-ramirez": "en-PH-RosaNeural",
    "maelyn-garcia": "en-GB-SoniaNeural",
    "amira-salazar": "en-US-AnaNeural",
    "justin-mercado": "en-PH-JamesNeural",
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
        self._cached_phrases: set[str] = set()
        self._render_locks = [threading.Lock() for _ in range(32)]
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
        for attempt in range(2):
            try:
                communicator = edge_tts.Communicate(text, voice)
                await communicator.save(str(path))
                return
            except (NoAudioReceived, ClientError, TimeoutError) as error:
                path.unlink(missing_ok=True)
                logger.warning(
                    "Edge TTS transient failure voice=%s attempt=%s/2 error=%s",
                    voice, attempt + 1, type(error).__name__,
                )
                if attempt == 1:
                    raise

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
        voice = self._voice_for_persona(persona_id, persona_name, voice)
        started = time.perf_counter()
        cacheable = text.strip() in self._CACHEABLE_TEXTS
        render_lock = nullcontext()
        if cacheable:
            text = text.strip()
            digest = hashlib.sha256(f"edge-mp3-v1|{voice}|{text}".encode()).hexdigest()
            filename = f"ack-{digest}.mp3"
            render_lock = self._render_locks[int(digest[:2], 16) % len(self._render_locks)]
        output_path = self.output_dir / filename
        with render_lock:
            cached = cacheable and output_path.is_file() and output_path.stat().st_size > 0
            if not cached:
                temporary = output_path.with_suffix(f".{uuid.uuid4().hex}.part")
                try:
                    self._run_async(self._save_audio(text, voice, temporary))
                    if temporary.stat().st_size == 0:
                        raise ValueError("Edge TTS returned empty audio")
                    temporary.replace(output_path)
                finally:
                    temporary.unlink(missing_ok=True)
            with self._cache_lock:
                if cacheable:
                    self._cached_phrases.add(filename)
                self._cache_hits += int(cached)
                self._last_generation_ms = round((time.perf_counter() - started) * 1000, 2)
        return f"{self.public_base_url}/audio/{filename}"

    def diagnostics(self) -> dict:
        return {
            "last_generation_ms": self._last_generation_ms,
            "cache_hits": self._cache_hits,
            "cached_phrases": len(self._cached_phrases),
        }

