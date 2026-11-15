from __future__ import annotations

import struct
import wave

from classsimvr.services.speech_to_text import SpeechToTextService


def test_wav_writer_resamples_48khz_audio_to_whisper_rate(tmp_path):
    samples = [0.25] * 4_800
    audio = struct.pack(f"<{len(samples)}f", *samples)
    path = tmp_path / "speech.wav"

    SpeechToTextService._write_float32_wav(path, audio, sample_rate=48_000)

    with wave.open(str(path), "rb") as wav:
        assert wav.getframerate() == 16_000
        assert wav.getnchannels() == 1
        assert wav.getnframes() == 1_600


def test_transcribe_passes_resampled_float32_array_directly_to_model(monkeypatch):
    captured = {}

    class Segment:
        text = "Maria, please stand up"

    class Model:
        def transcribe(self, audio, **kwargs):
            captured["audio"] = audio
            captured["kwargs"] = kwargs
            return iter([Segment()]), object()

    service = SpeechToTextService()
    monkeypatch.setattr(service, "_get_model", lambda: Model())
    audio = struct.pack("<4800f", *([0.25] * 4_800))

    assert service.transcribe(audio, sample_rate=48_000) == "Maria, please stand up"
    assert captured["audio"].dtype.name == "float32"
    assert captured["audio"].shape == (1_600,)
    assert captured["kwargs"]["vad_filter"] is True


def test_transcriber_type_error_is_not_retried_as_signature_fallback():
    from classsimvr.services.voice_listening import VoiceListeningService

    class Recorder:
        sample_rate = 16_000

        def start(self, callback):
            self.callback = callback

        def stop(self):
            pass

    class Transcriber:
        calls = 0

        def transcribe(self, audio_bytes, sample_rate):
            self.calls += 1
            raise TypeError("internal transcription failure")

    transcriber = Transcriber()
    service = VoiceListeningService(
        recorder_factory=lambda device: Recorder(),
        transcriber=transcriber,
    )
    service.start()
    service._enqueue(b"\x00" * 640)
    service.wait_for_idle(timeout=1.0)
    service.cancel()

    assert transcriber.calls == 1
    assert "internal transcription failure" in service.status()["last_transcription_error"]


def test_warmup_loads_whisper_model_once_and_reports_device():
    service = SpeechToTextService()
    model = object()
    calls = []

    def load_model():
        calls.append(True)
        return model

    service._get_model = load_model

    service.warmup()
    service.warmup()

    assert calls == [True]
    diagnostics = service.diagnostics()
    assert diagnostics["model_loaded"] is True
    assert diagnostics["device"] == "cpu"


def test_indonesian_language_detection_retries_with_tagalog(monkeypatch):
    calls = []

    class Segment:
        def __init__(self, text):
            self.text = text

    class Info:
        def __init__(self, language):
            self.language = language

    class Model:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return iter([Segment("Maria berdiri")]), Info("id")
            return iter([Segment("Maria tumayo")]), Info("tl")

    service = SpeechToTextService()
    monkeypatch.setattr(service, "_get_model", lambda: Model())
    audio = struct.pack("<1600f", *([0.25] * 1600))

    assert service.transcribe(audio) == "Maria tumayo"
    assert calls[0]["language"] is None
    assert calls[1]["language"] == "tl"
