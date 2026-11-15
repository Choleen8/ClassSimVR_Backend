import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from classsimvr.app import app
from classsimvr.database import get_session
from classsimvr.models import Base, Session, Trainee
from classsimvr.routes.behavior import get_behavior_service


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_get_session():
        with DbSession(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def create_session(client: TestClient) -> int:
    engine = next(iter(app.dependency_overrides.values())).__closure__[0].cell_contents
    with DbSession(engine) as db:
        trainee = Trainee(full_name="Teacher One", email="teacher@example.com")
        db.add(trainee)
        db.flush()
        session = Session(trainee_id=trainee.trainee_id)
        db.add(session)
        db.commit()
        db.refresh(session)
        return session.session_id


def persona_payload():
    return {
        "name": "Ana",
        "language": "English",
        "academic_baseline": "strong in mathematics",
        "avatar_ref": "avatar-ana",
        "personality_traits": ["curious", "shy"],
        "behavior_triggers": ["asks for examples"],
        "response_style": "short and careful",
        "participation_level": "medium",
    }


def test_health_and_persona_list_contract(client):
    assert client.get("/api/v1/health").json() == {
        "status": "ok",
        "api_version": "v1",
    }
    created = client.post("/api/v1/personas", json=persona_payload())
    assert created.status_code == 201
    assert created.json()["name"] == "Ana"
    assert client.get("/api/v1/personas").json()[0]["persona_id"] == created.json()["persona_id"]


def test_trainee_persona_list_aggregates_personas_across_sessions(client):
    persona = client.post("/api/v1/personas", json=persona_payload()).json()
    session_id = create_session(client)
    client.post(
        f"/api/v1/sessions/{session_id}/personas",
        json={"persona_id": persona["persona_id"]},
    )

    response = client.get("/api/v1/trainees/1/personas")

    assert response.status_code == 200
    assert response.json()[0]["persona_id"] == persona["persona_id"]


def test_persona_assignment_and_runtime_state_update(client):
    persona = client.post("/api/v1/personas", json=persona_payload()).json()
    session_id = create_session(client)

    assigned = client.post(
        f"/api/v1/sessions/{session_id}/personas",
        json={"persona_id": persona["persona_id"], "initial_state": "confused"},
    )
    assert assigned.status_code == 201
    assert assigned.json()["current_state"] == "confused"

    updated = client.patch(
        f"/api/v1/sessions/{session_id}/personas/{persona['persona_id']}",
        json={"current_state": "engaged", "current_emotion": "confident"},
    )
    assert updated.status_code == 200
    assert updated.json()["current_emotion"] == "confident"

    listed = client.get(f"/api/v1/sessions/{session_id}/personas")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "Ana"


def test_persona_routes_return_controlled_conflicts_and_not_found(client):
    session_id = create_session(client)
    assert client.post(
        f"/api/v1/sessions/{session_id}/personas",
        json={"persona_id": 999},
    ).status_code == 404


def test_versioned_behavior_route_resolves_assigned_persona_state(client):
    class FakeBehaviorService:
        def generate_reply(self, request):
            assert request.session_id == "1"
            assert request.persona_id == "1"
            assert request.persona_name == "Ana"
            assert "curious" in request.persona_profile
            assert "confused" in request.session_summary
            return {
                "persona_id": request.persona_id,
                "dialogue": "Can you show me an example?",
                "emotion": "curious",
                "action": "raises_hand",
                "fallback": False,
            }

    app.dependency_overrides[get_behavior_service] = lambda: FakeBehaviorService()
    persona = client.post("/api/v1/personas", json=persona_payload()).json()
    session_id = create_session(client)
    client.post(
        f"/api/v1/sessions/{session_id}/personas",
        json={"persona_id": persona["persona_id"], "initial_state": "confused"},
    )
    client.patch(
        f"/api/v1/sessions/{session_id}/personas/{persona['persona_id']}",
        json={"state_summary": "confused about fractions"},
    )

    response = client.post(
        f"/api/v1/sessions/{session_id}/personas/{persona['persona_id']}/turn",
        json={"trainee_input": "What is one half?"},
    )

    assert response.status_code == 200, response.json()
    assert response.json()["persona_id"] == str(persona["persona_id"])


def test_versioned_behavior_route_rejects_inactive_assignment(client):
    class ExplodingBehaviorService:
        def generate_reply(self, request):
            raise AssertionError("inactive persona must not invoke behavior service")

    app.dependency_overrides[get_behavior_service] = lambda: ExplodingBehaviorService()
    persona = client.post("/api/v1/personas", json=persona_payload()).json()
    session_id = create_session(client)
    client.post(
        f"/api/v1/sessions/{session_id}/personas",
        json={"persona_id": persona["persona_id"]},
    )
    client.patch(
        f"/api/v1/sessions/{session_id}/personas/{persona['persona_id']}",
        json={"is_active": False},
    )

    response = client.post(
        f"/api/v1/sessions/{session_id}/personas/{persona['persona_id']}/turn",
        json={"trainee_input": "Hello"},
    )

    assert response.status_code == 404
    assert client.patch(
        f"/api/v1/sessions/{session_id}/personas/999",
        json={"current_state": "engaged"},
    ).status_code == 404
