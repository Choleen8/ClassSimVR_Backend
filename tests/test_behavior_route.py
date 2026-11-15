from fastapi.testclient import TestClient

from classsimvr.app import app
from classsimvr.routes.behavior import get_behavior_service
from classsimvr.routes.behavior import get_tts_service
from classsimvr.services.contracts import BehaviorResponse


class FakeBehaviorService:
    def generate_reply(self, request):
        return BehaviorResponse(
            persona_id=request.persona_id,
            dialogue="I understand.",
            emotion="engaged",
            action="nods",
        )


def test_behavior_route_returns_structured_reply():
    app.dependency_overrides[get_behavior_service] = lambda: FakeBehaviorService()
    client = TestClient(app)

    response = client.post(
        "/sessions/session-1/turn",
        json={
            "persona_id": "persona-1",
            "persona_name": "Ana",
            "persona_profile": "curious learner",
            "trainee_input": "Are you ready?",
        },
    )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["dialogue"] == "I understand."
    assert response.json()["audio_url"] is None


def test_behavior_route_adds_audio_url_for_spoken_reply():
    class SpokenBehaviorService(FakeBehaviorService):
        def generate_reply(self, request):
            reply = super().generate_reply(request)
            return reply.model_copy(update={"speak": True, "dialogue": "I can speak."})

    class FakeTTSService:
        def synthesize(self, text, file_stem, *, persona_id=None, persona_name=None):
            assert text == "I can speak."
            assert file_stem == "session-1-persona-1"
            assert persona_id == "persona-1"
            assert persona_name == "Ana"
            return "http://127.0.0.1:8000/audio/test.mp3"

    app.dependency_overrides[get_behavior_service] = lambda: SpokenBehaviorService()
    app.dependency_overrides[get_tts_service] = lambda: FakeTTSService()
    client = TestClient(app)

    response = client.post(
        "/sessions/session-1/turn",
        json={
            "persona_id": "persona-1",
            "persona_name": "Ana",
            "trainee_input": "Are you ready?",
        },
    )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["audio_url"] == "http://127.0.0.1:8000/audio/test.mp3"


def test_behavior_route_keeps_dialogue_when_tts_fails():
    class SpokenBehaviorService(FakeBehaviorService):
        def generate_reply(self, request):
            reply = super().generate_reply(request)
            return reply.model_copy(update={"speak": True, "dialogue": "Still readable."})

    class FailingTTSService:
        def synthesize(self, text, file_stem, *, persona_id=None, persona_name=None):
            raise RuntimeError("TTS provider unavailable")

    app.dependency_overrides[get_behavior_service] = lambda: SpokenBehaviorService()
    app.dependency_overrides[get_tts_service] = lambda: FailingTTSService()
    client = TestClient(app)

    response = client.post(
        "/sessions/session-1/turn",
        json={
            "persona_id": "persona-1",
            "persona_name": "Ana",
            "trainee_input": "Are you ready?",
        },
    )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["dialogue"] == "Still readable."
    assert response.json()["audio_url"] is None


def test_behavior_route_rejects_blank_trainee_input():
    client = TestClient(app)

    response = client.post(
        "/sessions/session-1/turn",
        json={
            "persona_id": "persona-1",
            "persona_name": "Ana",
            "trainee_input": "   ",
        },
    )

    assert response.status_code == 422


def test_behavior_route_rejects_blank_persona_name():
    client = TestClient(app)

    response = client.post(
        "/sessions/session-1/turn",
        json={
            "persona_id": "persona-1",
            "persona_name": "   ",
            "trainee_input": "Are you ready?",
        },
    )

    assert response.status_code == 422


def test_behavior_route_rejects_oversized_recent_turn_text():
    client = TestClient(app)

    response = client.post(
        "/sessions/session-1/turn",
        json={
            "persona_id": "persona-1",
            "persona_name": "Ana",
            "trainee_input": "Are you ready?",
            "recent_turns": [{"speaker": "learner", "text": "x" * 2001}],
        },
    )

    assert response.status_code == 422
