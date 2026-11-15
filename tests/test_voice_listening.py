from __future__ import annotations

import struct
import time

from classsimvr.services.voice_listening import (
    VoiceActivityDetector,
    VoiceListeningService,
)


def pcm_frame(value: float, count: int = 320) -> bytes:
    return struct.pack(f"<{count}f", *([value] * count))


def test_vad_emits_one_utterance_after_speech_and_pause():
    detector = VoiceActivityDetector(
        sample_rate=16_000,
        frame_ms=20,
        silence_ms=60,
        threshold=0.01,
        pre_roll_ms=0,
    )

    assert detector.process(pcm_frame(0.0)) == []
    assert detector.process(pcm_frame(0.2)) == []
    assert detector.process(pcm_frame(0.0)) == []
    assert detector.process(pcm_frame(0.0)) == []
    emitted = detector.process(pcm_frame(0.0))

    assert len(emitted) == 1
    assert len(emitted[0]) > len(pcm_frame(0.2))


def test_toggle_service_pauses_and_resumes_without_restarting_recording():
    class Recorder:
        def __init__(self):
            self.callback = None
            self.stop_count = 0

        def start(self, callback):
            self.callback = callback

        def stop(self):
            self.stop_count += 1

    class Transcriber:
        def transcribe(self, audio_bytes):
            return "Maria, please stand up."

    recorder = Recorder()
    results = []
    service = VoiceListeningService(
        recorder_factory=lambda device: recorder,
        transcriber=Transcriber(),
        on_transcription=results.append,
        vad=VoiceActivityDetector(silence_ms=20, pre_roll_ms=0),
    )

    service.start()
    service.pause()
    assert service.status()["state"] == "paused"
    service.resume()
    assert service.status()["state"] == "listening"
    recorder.callback(pcm_frame(0.2))
    recorder.callback(pcm_frame(0.0))
    recorder.callback(pcm_frame(0.0))
    recorder.callback(pcm_frame(0.0))
    recorder.callback(pcm_frame(0.0))
    service.wait_for_idle(timeout=1.0)

    assert results == ["Maria, please stand up."]
    service.stop()
    assert recorder.stop_count == 1
    assert service.status()["state"] == "idle"


def test_listener_reports_audio_and_transcription_diagnostics():
    class Recorder:
        sample_rate = 48_000
        host_api = "Windows WASAPI"

        def __init__(self):
            self.callback = None

        def start(self, callback):
            self.callback = callback

        def stop(self):
            pass

    class Transcriber:
        def transcribe(self, audio_bytes, sample_rate):
            assert sample_rate == 48_000
            return "Maria, please stand up"

    recorder = Recorder()
    results = []
    service = VoiceListeningService(
        recorder_factory=lambda device: recorder,
        transcriber=Transcriber(),
        on_transcription=results.append,
        vad=VoiceActivityDetector(
            sample_rate=16_000,
            silence_ms=40,
            threshold=0.01,
            pre_roll_ms=0,
        ),
    )

    service.start()
    recorder.callback(pcm_frame(0.2, count=960))
    recorder.callback(pcm_frame(0.0, count=960))
    recorder.callback(pcm_frame(0.0, count=960))
    service.wait_for_idle(timeout=1.0)
    status = service.status()
    service.stop()

    assert status["captured_chunks"] == 3
    assert status["captured_bytes"] > 0
    assert status["non_silent_frames"] >= 1
    assert status["speech_started"] == 1
    assert status["utterances_detected"] == 1
    assert status["utterances_transcribed"] == 1
    assert status["last_transcript"] == "Maria, please stand up"
    assert results == ["Maria, please stand up"]


def test_listener_warms_transcriber_before_recording():
    class Recorder:
        sample_rate = 48_000

        def start(self, callback):
            self.callback = callback

        def stop(self):
            pass

    class Transcriber:
        def __init__(self):
            self.warmup_calls = 0

        def warmup(self):
            self.warmup_calls += 1

        def transcribe(self, audio_bytes, sample_rate):
            return ""

    transcriber = Transcriber()
    service = VoiceListeningService(
        recorder_factory=lambda device: Recorder(),
        transcriber=transcriber,
    )

    service.start()
    service.stop()

    assert transcriber.warmup_calls == 1
