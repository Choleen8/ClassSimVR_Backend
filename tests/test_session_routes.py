import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from classsimvr.app import app
from classsimvr.database import get_session
from classsimvr.models import Base, Session, Trainee


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
    with DbSession(engine) as db:
        trainee = Trainee(full_name="Teacher One", email="teacher@example.com")
        db.add(trainee)
        db.flush()
        session = Session(trainee_id=trainee.trainee_id)
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.session_id
    yield TestClient(app), session_id
    app.dependency_overrides.clear()


def test_generate_students_route_returns_roster_and_assignments(client):
    http, session_id = client

    response = http.post(
        f"/api/v1/sessions/{session_id}/students/generate",
        json={"class_size": 15, "seed": 42},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] == session_id
    assert body["class_size"] == 15
    assert body["seed"] == 42
    assert len(body["students"]) == 15
    assert all(student["session_id"] == session_id for student in body["students"])


def test_generate_students_route_rejects_out_of_range_size(client):
    http, session_id = client

    response = http.post(
        f"/api/v1/sessions/{session_id}/students/generate",
        json={"class_size": 31},
    )

    assert response.status_code == 422


def test_generate_students_route_rejects_second_generation(client):
    http, session_id = client
    url = f"/api/v1/sessions/{session_id}/students/generate"
    assert http.post(url, json={"class_size": 15, "seed": 1}).status_code == 201

    response = http.post(url, json={"class_size": 15, "seed": 2})

    assert response.status_code == 409


def test_session_lifecycle_routes_return_status(client):
    http, session_id = client

    started = http.post(f"/api/v1/sessions/{session_id}/start")
    paused = http.post(f"/api/v1/sessions/{session_id}/pause")
    resumed = http.post(f"/api/v1/sessions/{session_id}/resume")
    completed = http.post(f"/api/v1/sessions/{session_id}/complete")

    assert started.status_code == 200
    assert started.json()["status"] == "running"
    assert paused.json()["status"] == "paused"
    assert resumed.json()["status"] == "running"
    assert completed.json()["status"] == "completed"


def test_reset_route_clears_generated_roster(client):
    http, session_id = client
    generate_url = f"/api/v1/sessions/{session_id}/students/generate"
    assert http.post(generate_url, json={"class_size": 15, "seed": 8}).status_code == 201

    response = http.post(f"/api/v1/sessions/{session_id}/reset")
    roster = http.get(f"/api/v1/sessions/{session_id}/personas")

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert roster.json() == []


def test_session_creation_stores_scenario_configuration(client):
    http, _ = client

    response = http.post(
        "/api/v1/sessions",
        json={
            "trainee_id": 1,
            "grade": "Grade 6",
            "competency": "Explain equivalent fractions",
            "lesson_plan": "Use visual fraction strips.",
            "answer_key": "Equivalent fractions have the same value.",
            "class_size": 20,
            "difficulty": "moderate",
        },
    )

    assert response.status_code == 201
    assert response.json()["class_size"] == 20
    assert response.json()["status"] == "created"


def test_session_creation_stores_teaching_setup_configuration(client):
    http, _ = client

    response = http.post(
        "/api/v1/sessions",
        json={
            "trainee_id": 1,
            "grade": "High School",
            "subject": "Science",
            "topic": "Ecosystems",
            "session_mode": "assessment",
        },
    )

    assert response.status_code == 201
    assert response.json()["grade"] == "High School"
    assert response.json()["subject"] == "Science"
    assert response.json()["topic"] == "Ecosystems"
    assert response.json()["session_mode"] == "assessment"


def test_session_history_includes_teaching_setup_configuration(client):
    http, _ = client

    created = http.post(
        "/api/v1/sessions",
        json={
            "trainee_id": 1,
            "grade": "College",
            "subject": "Education",
            "topic": "Lesson planning",
            "session_mode": "practice",
        },
    )
    assert created.status_code == 201

    history = http.get("/api/v1/trainees/1/history")

    assert history.status_code == 200
    item = next(row for row in history.json() if row["session_id"] == created.json()["session_id"])
    assert item["subject"] == "Education"
    assert item["topic"] == "Lesson planning"
    assert item["session_mode"] == "practice"


def test_session_creation_rejects_topic_from_another_subject(client):
    http, _ = client

    response = http.post(
        "/api/v1/sessions",
        json={
            "trainee_id": 1,
            "grade": "High School",
            "subject": "Science",
            "topic": "Linear equations",
        },
    )

    assert response.status_code == 422


def test_session_creation_rejects_subject_outside_grade_level(client):
    http, _ = client

    response = http.post(
        "/api/v1/sessions",
        json={
            "trainee_id": 1,
            "grade": "Elementary",
            "subject": "Practical Research",
            "topic": "Research problem",
        },
    )

    assert response.status_code == 422
