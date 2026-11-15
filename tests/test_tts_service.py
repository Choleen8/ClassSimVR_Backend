from pathlib import Path

from classsimvr.services import tts as tts_module
from classsimvr.services.tts import TextToSpeechService


class FakeCommunicate:
    calls: list[tuple[str, str]] = []

    def __init__(self, text: str, voice: str):
        self.text = text
        self.voice = voice
        type(self).calls.append((text, voice))

    async def save(self, output_path: str):
        Path(output_path).write_bytes(b"fake-mp3")


def fake_run_async(coroutine):
    """Run the fake Edge TTS operation without Windows event-loop setup."""
    frame = coroutine.cr_frame
    assert frame is not None
    text = frame.f_locals["text"]
    voice = frame.f_locals["voice"]
    output_path = frame.f_locals["path"]
    FakeCommunicate(text, voice)
    Path(output_path).write_bytes(b"fake-mp3")
    coroutine.close()


def test_tts_service_writes_unique_audio_file_and_maps_persona_voices(
    tmp_path: Path, monkeypatch
):
    FakeCommunicate.calls.clear()
    monkeypatch.setattr(tts_module.edge_tts, "Communicate", FakeCommunicate)
    service = TextToSpeechService(
        output_dir=tmp_path,
        public_base_url="http://127.0.0.1:8000",
    )
    monkeypatch.setattr(service, "_run_async", fake_run_async)

    first_url = service.synthesize(
        "Hello, teacher.", "session-1", persona_id="maria-sandoval"
    )
    second_url = service.synthesize(
        "Hello again.", "session-1", persona_id="numeric-2", persona_name="Berto Eco"
    )

    assert first_url.startswith("http://127.0.0.1:8000/audio/session-1-")
    assert first_url.endswith(".mp3")
    assert first_url != second_url
    assert (tmp_path / first_url.rsplit("/", 1)[-1]).read_bytes() == b"fake-mp3"
    assert FakeCommunicate.calls == [
        ("Hello, teacher.", "fil-PH-BlessicaNeural"),
        ("Hello again.", "fil-PH-AngeloNeural"),
    ]


def test_tts_caches_fixed_acknowledgement_audio_per_voice(tmp_path: Path, monkeypatch):
    FakeCommunicate.calls.clear()
    monkeypatch.setattr(tts_module.edge_tts, "Communicate", FakeCommunicate)
    service = TextToSpeechService(output_dir=tmp_path)
    monkeypatch.setattr(service, "_run_async", fake_run_async)

    first = service.synthesize(
        "Yes, Ma'am.", "session-1", persona_id="maria-sandoval", voice="fil-PH-BlessicaNeural"
    )
    second = service.synthesize(
        "Yes, Ma'am.", "session-2", persona_id="other", voice="fil-PH-BlessicaNeural"
    )

    assert first == second
    assert FakeCommunicate.calls == [("Yes, Ma'am.", "fil-PH-BlessicaNeural")]
    assert service.diagnostics()["cache_hits"] == 1
