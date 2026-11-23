import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from classsimvr.app import app
from classsimvr.database import get_session
from classsimvr.models import Base, Persona, Session, SessionPersona, Trainee
from classsimvr.routes.behavior import get_tts_service
from classsimvr.routes.websocket import get_websocket_behavior_service
from classsimvr.services.contracts import BehaviorResponse


@pytest.fixture
def websocket_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_get_session():
        with DbSession(engine) as db:
            yield db

    class FakeBehaviorService:
        def generate_reply(self, request):
            return BehaviorResponse(
                persona_id=request.persona_id,
                dialogue="I understand.",
                emotion="engaged",
                action="nods",
            )

    class FakeTTSService:
        def synthesize(self, text, file_stem, *, persona_id=None, persona_name=None):
            return None

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_websocket_behavior_service] = lambda: FakeBehaviorService()
    app.dependency_overrides[get_tts_service] = lambda: FakeTTSService()
    client = TestClient(app)
    with DbSession(engine) as db:
        trainee = Trainee(full_name="Teacher One", email="teacher@example.com")
        db.add(trainee)
        db.flush()
        session = Session(trainee_id=trainee.trainee_id)
        db.add(session)
        db.flush()
        persona = Persona(name="Ana", language="English")
        db.add(persona)
        db.flush()
        db.add(SessionPersona(session_id=session.session_id, persona_id=persona.persona_id))
        db.commit()
        session_id = session.session_id
        persona_id = persona.persona_id

    yield client, session_id, persona_id
    app.dependency_overrides.clear()


def test_websocket_acknowledges_connection_and_ping(websocket_client):
    client, session_id, _persona_id = websocket_client

    with client.websocket_connect(f"/api/v1/ws/sessions/{session_id}") as socket:
        assert socket.receive_json() == {
            "type": "connected",
            "session_id": session_id,
        }
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}


def test_websocket_returns_behavior_response_for_assigned_persona(websocket_client):
    client, session_id, persona_id = websocket_client

    with client.websocket_connect(f"/api/v1/ws/sessions/{session_id}") as socket:
        socket.receive_json()
        socket.send_json(
            {
                "type": "turn",
                "persona_id": persona_id,
                "trainee_input": "Are you ready?",
            }
        )
        event = socket.receive_json()

    assert event["type"] == "turn_response"
    assert event["data"]["dialogue"] == "I understand."


def test_websocket_rejects_persona_not_assigned_to_session(websocket_client):
    client, session_id, _persona_id = websocket_client

    with client.websocket_connect(f"/api/v1/ws/sessions/{session_id}") as socket:
        socket.receive_json()
        socket.send_json(
            {
                "type": "turn",
                "persona_id": 999,
                "trainee_input": "Hello",
            }
        )
        assert socket.receive_json() == {
            "type": "error",
            "code": "persona_not_assigned",
            "message": "persona is not assigned to this session",
        }
