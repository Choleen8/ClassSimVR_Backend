from classsimvr.routes.microphone import (
    MicrophoneStartBody,
    cancel_microphone,
    microphone_status,
    start_microphone,
)
from classsimvr.routes.microphone import MicrophoneStopBody, MicrophoneStopResponse, stop_microphone
from classsimvr.services.contracts import BehaviorResponse
from classsimvr.services.microphone_commands import CommandResult
from classsimvr.routes.microphone import ListeningCoordinator
from classsimvr.services.microphone import TranscriptionResult


class FakeManager:
    def start(self, device_name=None):
        return {"state": "recording", "device": device_name or "fake"}

    def stop(self):
        return TranscriptionResult(text="Maria, please stand up.", language="en")

    def cancel(self):
        return {"state": "idle"}

    def status(self):
        return {"state": "idle", "device": None, "duration_s": 0.0, "error": None}

    def begin_processing(self):
        return {"state": "processing"}

    def finish_processing(self):
        return {"state": "idle"}


def test_francis_thinking_and_spoken_idk_reach_poll_queue_in_order():
    from unittest.mock import Mock
    from classsimvr.services.behavior import BehaviorService
    from classsimvr.services.microphone_commands import MicrophoneCommandRouter

    coordinator = ListeningCoordinator()
    tts = Mock()
    tts.synthesize.return_value = "http://localhost/audio/francis.mp3"
    coordinator.router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=object()), tts=tts,
    )
    try:
        coordinator._on_transcription("Francis, what number am I thinking of?")
        items = coordinator.poll("classroom", limit=10, drain=True)
        assert len(items) == 1
        item = items[0]
        assert item.target == "francis-reyes"
        assert [(r.persona_id, r.action, r.speak) for r in item.responses] == [
            ("francis-reyes", "THINKING", False), ("francis-reyes", "IDK", True),
        ]
        assert item.responses[0].audio_url is None
        assert item.responses[1].audio_url == tts.synthesize.return_value
        assert coordinator.poll("classroom", limit=10, drain=True) == []
        tts.synthesize.assert_called_once()
    finally:
        coordinator.shutdown()


def test_microphone_start_status_and_cancel_handlers():
    manager = FakeManager()
    assert start_microphone(MicrophoneStartBody(), manager)["state"] == "recording"
    assert microphone_status(manager)["state"] == "idle"
    assert cancel_microphone(manager)["state"] == "idle"


def test_microphone_stop_returns_transcript_and_existing_response_fields():
    class FakeCommandRouter:
        def handle(self, transcript, **kwargs):
            assert transcript == "Maria, please stand up."
            return CommandResult(
                target="maria-sandoval",
                responses=[
                    BehaviorResponse(
                        persona_id="maria-sandoval",
                        dialogue="",
                        emotion="neutral",
                        action="STAND",
                        speak=False,
                    )
                ],
            )

    response = stop_microphone(
        MicrophoneStopBody(session_id="classroom"),
        FakeManager(),
        FakeCommandRouter(),
    )

    assert response.transcript == "Maria, please stand up."
    assert response.responses[0].model_dump()["action"] == "STAND"


def test_toggle_coordinator_queues_completed_utterance_responses():
    coordinator = ListeningCoordinator()

    class FakeCommandRouter:
        def handle(self, transcript, **kwargs):
            return CommandResult(
                target="maria-sandoval",
                responses=[
                    BehaviorResponse(
                        persona_id="maria-sandoval",
                        dialogue="",
                        emotion="neutral",
                        action="STAND",
                        speak=False,
                    )
                ],
            )

    coordinator.router = FakeCommandRouter()
    coordinator._on_transcription("Maria, please stand up.")
    items = coordinator.poll("classroom", limit=10, drain=True)

    assert len(items) == 1
    assert items[0].transcript == "Maria, please stand up."
    assert items[0].responses[0].action == "STAND"
    coordinator.shutdown()


def test_microphone_status_exposes_pipeline_latency_stages():
    coordinator = ListeningCoordinator()
    status = coordinator.status()

    assert set((
        "vad",
        "audio_preprocessing",
        "whisper_transcription",
        "command_recognition",
        "student_routing",
        "response_queue",
        "student_name_detection",
        "conversation_memory_retrieval",
        "llm_generation",
        "tts_generation",
        "response_delivery",
        "total_response",
    )).issubset(status["latency_ms"])
    coordinator.shutdown()


def test_start_clears_pending_responses_from_previous_listening_session():
    coordinator = ListeningCoordinator()

    class FakeListener:
        def set_student_names(self, names):
            self.names = names

        def start(self, device_name=None):
            return {"state": "listening", "device": device_name}

        def stop(self):
            return {"state": "idle"}

        def shutdown(self):
            pass

        def status(self):
            return {"state": "idle", "latency_ms": {}}

    coordinator.listener = FakeListener()
    coordinator._queues["classroom"].append(
        MicrophoneStopResponse(transcript="stale", target="maria-sandoval", responses=[])
    )

    coordinator.start(MicrophoneStartBody(session_id="classroom"))

    assert "Francis Reyes" in coordinator.listener.names
    assert coordinator.poll("classroom", limit=10, drain=False) == []
    coordinator.shutdown()


def test_duplicate_transcription_is_executed_once():
    coordinator = ListeningCoordinator()

    class FakeCommandRouter:
        def handle(self, transcript, **kwargs):
            return CommandResult(target="maria-sandoval", responses=[])

        def latency_status(self):
            return {"command_recognition": 0.1, "student_routing": 0.1}

    coordinator.router = FakeCommandRouter()
    coordinator._on_transcription("Maria, please stand")
    coordinator._on_transcription("Maria, please stand")

    items = coordinator.poll("classroom", limit=10, drain=True)
    assert len(items) == 1
    coordinator.shutdown()


def test_repeated_start_stop_invalidates_previous_generation_safely():
    coordinator = ListeningCoordinator()

    class FakeListener:
        def __init__(self):
            self.starts = 0
            self.stops = 0

        def set_student_names(self, names):
            self.names = names

        def start(self, device_name=None):
            self.starts += 1
            return {"state": "listening", "device": device_name}

        def stop(self):
            self.stops += 1
            return {"state": "idle"}

        def shutdown(self):
            pass

        def status(self):
            return {"state": "idle", "latency_ms": {}}

    listener = FakeListener()
    coordinator.listener = listener

    coordinator.start(MicrophoneStartBody(session_id="classroom"))
    coordinator.stop(MicrophoneStopBody(session_id="classroom"))
    coordinator.start(MicrophoneStartBody(session_id="classroom"))
    coordinator.stop(MicrophoneStopBody(session_id="classroom"))

    assert listener.starts == 2
    assert listener.stops == 2
    assert coordinator.status()["queued_responses"] == 0
    coordinator.shutdown()


def test_pause_resume_preserves_memory_and_explicit_reset_clears_it():
    coordinator = ListeningCoordinator()

    class FakeListener:
        def pause(self):
            return {"state": "paused"}

        def resume(self):
            return {"state": "listening"}

        def shutdown(self):
            pass

    coordinator.listener = FakeListener()
    coordinator.router.registry.record_conversation_turn(
        "classroom", "maria-sandoval", "What is a computer?", "It processes information."
    )

    assert coordinator.pause("classroom")["state"] == "paused"
    assert coordinator.resume("classroom")["state"] == "listening"
    assert coordinator.router.registry.get_conversation_memory(
        "classroom", "maria-sandoval"
    )["recent_turns"]

    assert coordinator.reset_memory("classroom")["state"] == "memory_reset"
    assert coordinator.router.registry.get_conversation_memory(
        "classroom", "maria-sandoval"
    )["recent_turns"] == []
    coordinator.shutdown()
