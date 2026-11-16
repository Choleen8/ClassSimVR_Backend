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


def test_recording_requires_consent_before_start(client):
    http, session_id = client

    response = http.post(
        f"/api/v1/sessions/{session_id}/recording/start",
        json={"mime_type": "video/webm", "storage_ref": "upload-token-1"},
    )

    assert response.status_code == 409


def test_consent_recording_start_and_complete_contract(client):
    http, session_id = client

    consent = http.put(
        f"/api/v1/sessions/{session_id}/consent",
        json={
            "video_recording": True,
            "purpose": "Research assessment of the trainee's teaching simulation.",
            "notice_version": "2026-11-09",
        },
    )
    started = http.post(
        f"/api/v1/sessions/{session_id}/recording/start",
        json={"mime_type": "video/webm", "storage_ref": "upload-token-1"},
    )
    completed = http.post(
        f"/api/v1/sessions/{session_id}/recording/complete",
        json={"duration_s": 125.5, "storage_ref": "object://recordings/session-1.webm"},
    )

    assert consent.status_code == 200
    assert consent.json()["video_recording"] is True
    assert consent.json()["purpose"] == "Research assessment of the trainee's teaching simulation."
    assert consent.json()["notice_version"] == "2026-11-09"
    assert started.status_code == 201
    assert started.json()["status"] == "recording"
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["duration_s"] == 125.5


def test_consent_can_be_withdrawn_and_blocks_new_recording(client):
    http, session_id = client
    http.put(
        f"/api/v1/sessions/{session_id}/consent",
        json={
            "video_recording": True,
            "purpose": "Research assessment.",
            "notice_version": "2026-11-09",
        },
    )

    withdrawn = http.delete(f"/api/v1/sessions/{session_id}/consent")
    start = http.post(
        f"/api/v1/sessions/{session_id}/recording/start",
        json={"mime_type": "video/webm", "storage_ref": "upload-token-2"},
    )

    assert withdrawn.status_code == 200
    assert withdrawn.json()["video_recording"] is False
    assert start.status_code == 409


def test_recording_upload_saves_video_bytes_and_completes_recording(client, tmp_path, monkeypatch):
    http, session_id = client
    monkeypatch.setenv("CLASSSIMVR_RECORDING_DIR", str(tmp_path))

    consent = http.put(
        f"/api/v1/sessions/{session_id}/consent",
        json={
            "video_recording": True,
            "purpose": "Research assessment.",
            "notice_version": "2026-11-09",
        },
    )
    started = http.post(
        f"/api/v1/sessions/{session_id}/recording/start",
        json={"mime_type": "video/webm", "storage_ref": "pending"},
    )

    video_bytes = b"fake-webm-video-for-upload-test"
    uploaded = http.post(
        f"/api/v1/sessions/{session_id}/recording/upload",
        files={"file": ("lesson.webm", video_bytes, "video/webm")},
        data={"duration_s": "12.5"},
    )

    assert consent.status_code == 200
    assert started.status_code == 201
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["status"] == "completed"
    assert body["mime_type"] == "video/webm"
    assert body["duration_s"] == 12.5
    assert body["size_bytes"] == len(video_bytes)
    saved_file = tmp_path / body["storage_ref"]
    assert saved_file.is_file()
    assert saved_file.read_bytes() == video_bytes
