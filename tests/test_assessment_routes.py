import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from classsimvr.app import app
from classsimvr.database import get_session
from classsimvr.models import Base, Recording, Session, Trainee


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
        session = Session(trainee_id=trainee.trainee_id, status="completed")
        db.add(session)
        db.flush()
        db.add(
            Recording(
                session_id=session.session_id,
                status="completed",
                file_path="object://recordings/session-1.webm",
                duration_s=120,
            )
        )
        db.commit()
        db.refresh(session)
        session_id = session.session_id

    def override_get_session():
        with DbSession(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app), session_id
    app.dependency_overrides.clear()


def test_assessment_routes_expose_rubric_and_job_status(client):
    http, session_id = client

    rubric = http.get("/api/v1/assessment/rubric")
    queued = http.post(f"/api/v1/sessions/{session_id}/assessment")
    status = http.get(f"/api/v1/assessment/jobs/{queued.json()['job_id']}")

    assert rubric.status_code == 200
    assert len(rubric.json()) == 10
    assert queued.status_code == 202
    assert queued.json()["status"] == "pending"
    assert status.status_code == 200
    assert status.json()["status"] == "pending"


def test_assessment_result_route_completes_job(client):
    http, session_id = client
    queued = http.post(f"/api/v1/sessions/{session_id}/assessment")
    job_id = queued.json()["job_id"]

    response = http.post(
        f"/api/v1/assessment/jobs/{job_id}/result",
        json={
            "summary": "Clear lesson delivery.",
            "next_focus": "Ask more questions.",
            "indicators": [
                {
                    "bti_code": "1.1.1",
                    "observed": True,
                    "score": 3,
                    "evidence": [
                        {"t_start_s": 1, "t_end_s": 4, "note": "Used an example."}
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["report_session_id"] == session_id


def test_report_route_returns_persisted_summary(client):
    http, session_id = client
    job_id = http.post(f"/api/v1/sessions/{session_id}/assessment").json()["job_id"]
    http.post(
        f"/api/v1/assessment/jobs/{job_id}/result",
        json={
            "summary": "Clear lesson delivery.",
            "indicators": [
                {"bti_code": "1.1.1", "observed": False}
            ],
        },
    )

    response = http.get(f"/api/v1/sessions/{session_id}/report")

    assert response.status_code == 200
    assert response.json()["summary"] == "Clear lesson delivery."
    assert response.json()["indicators"][0]["bti_code"] == "1.1.1"
