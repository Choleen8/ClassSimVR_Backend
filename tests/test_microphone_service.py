from __future__ import annotations

from dataclasses import dataclass

import pytest

from classsimvr.services.microphone import (
    MicrophoneBusyError,
    MicrophoneManager,
    MicrophoneSilenceError,
    MicrophoneTranscriptionError,
)


@dataclass
class FakeRecorder:
    chunks: list[bytes]
    closed: bool = False

    def start(self, callback):
        callback(b"spoken audio")

    def stop(self):
        self.closed = True


class FakeTranscriber:
    def transcribe(self, audio_bytes: bytes):
        assert audio_bytes == b"spoken audio"
        return "Maria, please stand up."


def test_microphone_manager_records_and_transcribes_once():
    recorder = FakeRecorder([])
    manager = MicrophoneManager(recorder_factory=lambda device: recorder, transcriber=FakeTranscriber())

    started = manager.start()
    result = manager.stop()

    assert started["state"] == "recording"
    assert result.text == "Maria, please stand up."
    assert recorder.closed is True
    assert manager.status()["state"] == "processing"
    manager.finish_processing()
    assert manager.status()["state"] == "idle"


def test_microphone_manager_rejects_duplicate_start_and_can_cancel():
    recorder = FakeRecorder([])
    manager = MicrophoneManager(recorder_factory=lambda device: recorder, transcriber=FakeTranscriber())

    manager.start()
    with pytest.raises(MicrophoneBusyError):
        manager.start()

    cancelled = manager.cancel()
    assert cancelled["state"] == "idle"
    assert recorder.closed is True


def test_microphone_manager_rejects_empty_transcription():
    recorder = FakeRecorder([])

    class EmptyTranscriber:
        def transcribe(self, audio_bytes: bytes):
            return ""

    manager = MicrophoneManager(recorder_factory=lambda device: recorder, transcriber=EmptyTranscriber())
    manager.start()

    with pytest.raises(MicrophoneSilenceError):
        manager.stop()

    assert manager.status()["state"] == "idle"


def test_microphone_manager_wraps_transcription_failures_and_releases_device():
    recorder = FakeRecorder([])

    class FailingTranscriber:
        def transcribe(self, audio_bytes: bytes):
            raise RuntimeError("model unavailable")

    manager = MicrophoneManager(
        recorder_factory=lambda device: recorder,
        transcriber=FailingTranscriber(),
    )
    manager.start()

    with pytest.raises(MicrophoneTranscriptionError):
        manager.stop()

    assert manager.status()["state"] == "idle"
