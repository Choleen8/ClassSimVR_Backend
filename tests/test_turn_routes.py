import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from classsimvr.app import app
from classsimvr.database import get_session
from classsimvr.models import Base, Persona, Session, SessionPersona, Trainee


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with DbSession(engine) as db:
        trainee = Trainee(full_name="Teacher One", email="teacher@example.com")
        db.add(trainee)
        db.flush()
        session = Session(trainee_id=trainee.trainee_id)
        persona = Persona(name="Ana", language="English")
        db.add_all([session, persona])
        db.flush()
        db.add(SessionPersona(session_id=session.session_id, persona_id=persona.persona_id))
        db.commit()
        session_id = session.session_id
        persona_id = persona.persona_id

    def override_get_session():
        with DbSession(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app), session_id, persona_id
    app.dependency_overrides.clear()


def test_turn_route_persists_teacher_and_learner_event(client):
    http, session_id, persona_id = client

    response = http.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "persona_id": persona_id,
            "teacher_text": "Please explain your answer.",
            "learner_text": "I used the picture to count.",
            "emotion": "confident",
            "action": "points_to_board",
            "source": "behavior_service",
            "t_offset_s": 12.5,
        },
    )

    assert response.status_code == 201
    assert response.json()["persona_id"] == persona_id
    assert response.json()["teacher_text"] == "Please explain your answer."


def test_turn_route_rejects_unassigned_persona(client):
    http, session_id, _ = client

    response = http.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={"persona_id": 999, "learner_text": "Hello"},
    )

    assert response.status_code == 404
