import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from classsimvr.app import app
from classsimvr.database import get_session
from classsimvr.models import Base, Report, Session, Trainee


@pytest.fixture
def history_client():
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
    with DbSession(engine) as db:
        trainee = Trainee(full_name="Teacher One", email="teacher@example.com")
        other_trainee = Trainee(full_name="Teacher Two", email="other@example.com")
        db.add_all([trainee, other_trainee])
        db.commit()
        trainee_id = trainee.trainee_id
        other_trainee_id = other_trainee.trainee_id
    yield TestClient(app), engine, trainee_id, other_trainee_id
    app.dependency_overrides.clear()


def test_history_returns_newest_sessions_with_report_summary(history_client):
    http, engine, trainee_id, _ = history_client

    first = http.post("/api/v1/sessions", json={"trainee_id": trainee_id})
    second = http.post("/api/v1/sessions", json={"trainee_id": trainee_id})
    assert first.status_code == 201
    assert second.status_code == 201

    with DbSession(engine) as db:
        db.add(
            Report(
                session_id=first.json()["session_id"],
                summary="Clear explanation with useful examples.",
                next_focus="Increase learner checks for understanding.",
            )
        )
        db.commit()

    response = http.get(f"/api/v1/trainees/{trainee_id}/history")

    assert response.status_code == 200
    body = response.json()
    assert [item["session_id"] for item in body] == [
        second.json()["session_id"],
        first.json()["session_id"],
    ]
    assert body[0]["report_available"] is False
    assert body[0]["summary"] is None
    assert body[1]["report_available"] is True
    assert body[1]["summary"] == "Clear explanation with useful examples."
    assert body[1]["next_focus"] == "Increase learner checks for understanding."


def test_history_returns_empty_list_for_existing_trainee(history_client):
    http, _, _, other_trainee_id = history_client

    response = http.get(f"/api/v1/trainees/{other_trainee_id}/history")

    assert response.status_code == 200
    assert response.json() == []


def test_history_returns_not_found_for_unknown_trainee(history_client):
    http, _, _, _ = history_client

    response = http.get("/api/v1/trainees/999/history")

    assert response.status_code == 404
