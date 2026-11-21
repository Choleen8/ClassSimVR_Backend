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


def test_new_students_use_distinct_voices_with_ready_audio_files(tmp_path, monkeypatch):
    FakeCommunicate.calls.clear()
    service = TextToSpeechService(output_dir=tmp_path)
    monkeypatch.setattr(service, "_run_async", fake_run_async)
    urls = [
        service.synthesize("Yes, Ma'am!", "test", persona_id=student_id)
        for student_id in ("dave-santos", "juan-cruz")
    ]
    assert len(set(urls)) == 2
    assert FakeCommunicate.calls == [
        ("Yes, Ma'am!", "en-US-AndrewNeural"), ("Yes, Ma'am!", "en-US-BrianNeural"),
    ]
    assert all((tmp_path / url.rsplit("/", 1)[-1]).read_bytes() == b"fake-mp3" for url in urls)


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


def test_cache_survives_restart_and_keeps_concurrent_voices_separate(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    FakeCommunicate.calls.clear()
    monkeypatch.setattr(TextToSpeechService, "_run_async", staticmethod(fake_run_async))
    service = TextToSpeechService(output_dir=tmp_path)
    def generate(voice):
        return service.synthesize("Yes, Ma'am!", "test", voice=voice)

    with ThreadPoolExecutor(max_workers=4) as pool:
        urls = list(pool.map(generate, ["voice-a", "voice-b", "voice-a", "voice-b"]))
    assert len(set(urls)) == len(FakeCommunicate.calls) == 2
    restarted = TextToSpeechService(output_dir=tmp_path)
    assert restarted.synthesize("Yes, Ma'am!", "other", voice="voice-a") == urls[0]
    assert len(FakeCommunicate.calls) == 2


def test_failed_render_never_publishes_partial_mp3(tmp_path, monkeypatch):
    import pytest

    service = TextToSpeechService(output_dir=tmp_path)
    def partial(coroutine):
        coroutine.cr_frame.f_locals["path"].write_bytes(b"incomplete")
        coroutine.close()
        assert not list(tmp_path.glob("*.mp3"))
        raise OSError("stream interrupted")

    monkeypatch.setattr(service, "_run_async", partial)
    with pytest.raises(OSError, match="interrupted"):
        service.synthesize("Yes, Ma'am!", "test")
    assert not list(tmp_path.iterdir())


def test_transient_no_audio_is_retried_inside_the_student_response_pipeline(tmp_path, monkeypatch):
    from edge_tts.exceptions import NoAudioReceived
    from classsimvr.services.behavior import BehaviorService
    from classsimvr.services.microphone_commands import MicrophoneCommandRouter

    calls = []
    class IntermittentCommunicate:
        def __init__(self, text, voice):
            self.voice = voice
        async def save(self, path):
            calls.append(self.voice)
            if len(calls) == 1:
                Path(path).write_bytes(b"partial")
                raise NoAudioReceived("No audio received")
            Path(path).write_bytes(b"complete-mp3")

    monkeypatch.setattr(tts_module.edge_tts, "Communicate", IntermittentCommunicate)
    service = TextToSpeechService(output_dir=tmp_path)
    router = MicrophoneCommandRouter(behavior=BehaviorService(client=object()), tts=service)
    reply, = router.handle("Dave", session_id="retry-test").responses
    assert reply.dialogue == "Yes, Ma'am!" and reply.speak
    assert reply.audio_url is not None
    assert (tmp_path / reply.audio_url.rsplit("/", 1)[-1]).read_bytes() == b"complete-mp3"
    assert calls == ["en-US-AndrewNeural", "en-US-AndrewNeural"]
    assert not list(tmp_path.glob("*.part"))


def test_exhausted_audio_attempts_keep_subtitles_without_a_broken_url(tmp_path, monkeypatch):
    from edge_tts.exceptions import NoAudioReceived
    from classsimvr.services.behavior import BehaviorService
    from classsimvr.services.microphone_commands import MicrophoneCommandRouter

    calls = []
    class UnavailableCommunicate:
        def __init__(self, text, voice):
            calls.append(voice)
        async def save(self, path):
            Path(path).write_bytes(b"partial")
            raise NoAudioReceived("No audio received")

    monkeypatch.setattr(tts_module.edge_tts, "Communicate", UnavailableCommunicate)
    service = TextToSpeechService(output_dir=tmp_path)
    router = MicrophoneCommandRouter(behavior=BehaviorService(client=object()), tts=service)
    reply, = router.handle("Juan", session_id="failure-test").responses
    assert reply.dialogue == "Yes, Ma'am!" and reply.speak and reply.action == "NONE"
    assert reply.audio_url is None
    assert calls == ["en-US-BrianNeural", "en-US-BrianNeural"]
    assert not list(tmp_path.iterdir())
