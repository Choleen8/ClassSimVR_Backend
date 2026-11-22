from __future__ import annotations

import struct
import wave
from types import SimpleNamespace

import pytest

from classsimvr.services.speech_to_text import SpeechToTextService


def _short_name_model(service, monkeypatch, results):
    calls = []

    class Model:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            text, score, no_speech = results[min(len(calls) - 1, len(results) - 1)]
            segment = SimpleNamespace(text=text, avg_logprob=score, no_speech_prob=no_speech)
            return iter([segment]), SimpleNamespace(language="en", language_probability=0.8)

    monkeypatch.setattr(service, "_get_model", lambda: Model())
    return calls


def test_ambiguous_one_audio_recovers_registered_juan_only_after_decoder_agreement(monkeypatch):
    service = SpeechToTextService(student_names=("Maria Sandoval", "Juan Cruz"))
    calls = _short_name_model(service, monkeypatch, [
        ("One.", -0.6, 0.02), ("Juan", -0.55, 0.02), ("Juan!", -0.5, 0.02),
    ])
    assert service.transcribe(struct.pack("<16000f", *([0.1] * 16000))) == "Juan"
    assert [call["language"] for call in calls] == [None, "en", "en"]
    assert [call["beam_size"] for call in calls] == [5, 5, 3]
    assert [call.get("hotwords") for call in calls] == [None, "Juan Cruz", "Juan Cruz"]
    assert service.diagnostics()["short_name_resolution"]["decision"] == "recovered"


@pytest.mark.parametrize("candidates", [
    [("1.", -0.4, 0.02), ("1.", -0.4, 0.02)],
    [("Juan", -0.5, 0.02), ("One", -0.4, 0.02)],
    [("Juan", -1.5, 0.02), ("Juan", -1.5, 0.02)],
    [("Juan", -0.5, 0.8), ("Juan", -0.5, 0.8)],
    [("Juan", float("nan"), 0.02), ("Juan", float("nan"), 0.02)],
    [("Juan, stand up", -0.3, 0.02), ("Juan", -0.3, 0.02)],
])
def test_number_or_uncertain_name_retry_never_selects_juan(monkeypatch, candidates):
    service = SpeechToTextService(student_names=("Juan Cruz",))
    _short_name_model(service, monkeypatch, [("One.", -0.5, 0.02), *candidates])
    assert service.transcribe(bytes(64000)) == "One."
    resolution = service.diagnostics()["short_name_resolution"]
    assert resolution["decision"] == "unresolved"
    assert "Juan Cruz" in resolution["hint"]


@pytest.mark.parametrize("text", [
    "Number one", "One plus one is two.", "Please read question number one.",
    "One, please sit down.", "Juan Cruz", "Maria!", "Juan, stand up.",
])
def test_regular_speech_and_number_sentences_are_not_rewritten_or_retried(monkeypatch, text):
    service = SpeechToTextService()
    calls = _short_name_model(service, monkeypatch, [(text, -0.3, 0.01)])
    assert service.transcribe(bytes(64000)) == text
    assert len(calls) == 1


@pytest.mark.parametrize("names", [("Maria Sandoval",), ("Juan Cruz", "Juan Reyes")])
def test_short_name_retry_requires_a_unique_registered_student(monkeypatch, names):
    service = SpeechToTextService(student_names=names)
    calls = _short_name_model(service, monkeypatch, [("One.", -0.5, 0.02)])
    assert service.transcribe(bytes(64000)) == "One."
    assert len(calls) == 1


def test_short_name_retry_can_be_disabled_and_has_no_stale_diagnostics(monkeypatch):
    service = SpeechToTextService(student_names=("Juan Cruz",))
    _short_name_model(service, monkeypatch, [
        ("One.", -0.5, 0.02), ("Juan", -0.5, 0.02), ("Juan", -0.5, 0.02),
        ("Number one", -0.5, 0.02),
    ])
    assert service.transcribe(bytes(64000)) == "Juan"
    assert service.transcribe(bytes(64000)) == "Number one"
    assert service.diagnostics()["short_name_resolution"] is None
    monkeypatch.setenv("WHISPER_RETRY_SHORT_NAMES", "false")
    disabled = SpeechToTextService()
    calls = _short_name_model(disabled, monkeypatch, [("One.", -0.5, 0.02)])
    assert disabled.transcribe(bytes(64000)) == "One."
    assert len(calls) == 1


def test_name_retry_does_not_process_long_recordings_or_invent_missing_confidence(monkeypatch):
    service = SpeechToTextService(student_names=("Juan Cruz",))
    calls = _short_name_model(service, monkeypatch, [("One.", -0.5, 0.02)])
    assert service.transcribe(bytes(4 * 16000 * 5)) == "One."
    assert len(calls) == 1
    calls = _short_name_model(service, monkeypatch, [
        ("One.", -0.5, 0.02), ("Juan", None, None),
    ])
    assert service.transcribe(bytes(64000)) == "One."
    assert len(calls) == 2


def test_optional_name_retry_failure_keeps_original_transcript(monkeypatch):
    service = SpeechToTextService(student_names=("Juan Cruz",))

    class Model:
        def transcribe(self, audio, **kwargs):
            if kwargs.get("hotwords"):
                raise RuntimeError("decoder failed")
            return iter([SimpleNamespace(text="One.")]), SimpleNamespace(language="en")

    monkeypatch.setattr(service, "_get_model", lambda: Model())
    assert service.transcribe(bytes(64000)) == "One."
    assert service.diagnostics()["short_name_resolution"]["decision"] == "unresolved"


def test_nonword_registered_display_name_does_not_break_short_name_detection(monkeypatch):
    service = SpeechToTextService(student_names=("---", "Juan Cruz"))
    _short_name_model(service, monkeypatch, [("Number one", -0.5, 0.02)])
    assert service.transcribe(bytes(64000)) == "Number one"


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


def test_warmup_loads_whisper_model_once_and_reports_device(monkeypatch):
    monkeypatch.setenv("WHISPER_DEVICE", "cpu")
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


def test_auto_gpu_memory_failure_replays_same_utterance_on_cpu(monkeypatch):
    monkeypatch.setenv("WHISPER_DEVICE", "auto")
    service = SpeechToTextService()
    gpu, cpu = object(), object()
    service._model = gpu
    service.device = "cuda"
    monkeypatch.setattr(service, "_get_model", lambda: service._model or cpu)
    calls = []

    def recognize(model, samples):
        calls.append((model, samples))
        if model is gpu:
            raise RuntimeError("CUDA out of memory")
        return "Robert, umupo ka na."

    monkeypatch.setattr(service, "_recognize", recognize)
    assert service.transcribe(bytes(640)) == "Robert, umupo ka na."
    assert [model for model, _ in calls] == [gpu, cpu]
    assert calls[0][1] is calls[1][1]
    assert service.device == "cpu" and service.compute_type == "int8"


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


def test_initial_prompt_contains_all_registered_student_names():
    captured = {}

    class Model:
        def transcribe(self, audio, **kwargs):
            captured.update(kwargs)
            return iter([]), type("Info", (), {"language": "en"})()

    service = SpeechToTextService(student_names=(
        "Maria Sandoval", "Robert Eco", "Ethan Luna", "Andrea Guinto", "Judy Castillo",
        "Francis Reyes", "Dave Santos", "Juan Cruz",
    ))
    service._get_model = lambda: Model()
    service.transcribe(b"\x00" * 64)

    prompt = captured["initial_prompt"]
    for name in ("Maria Sandoval", "Robert Eco", "Ethan Luna", "Andrea Guinto", "Judy Castillo", "Dave Santos", "Juan Cruz"):
        assert name in prompt
    assert "First-name calls include Maria, Robert, Ethan, Andrea, Judy, Francis, Dave, Juan." in prompt


def test_short_arabic_script_name_retries_with_tagalog_hint(monkeypatch):
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
                return iter([Segment("جودي")]), Info("ar")
            return iter([Segment("Judy")]), Info("tl")

    service = SpeechToTextService(student_names=("Judy Castillo",))
    monkeypatch.setattr(service, "_get_model", lambda: Model())

    assert service.transcribe(b"\x00" * 64) == "Judy"
    assert [call["language"] for call in calls] == [None, "tl"]
