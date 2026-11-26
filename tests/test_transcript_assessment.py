import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classsimvr.app import app
from classsimvr.database import get_session
from classsimvr.models import AssessmentJob, AuthAccount, Base, Report, Session, Trainee, Turn
from classsimvr.routes import assessment as routes
from classsimvr.routes.microphone import ListeningCoordinator, MicrophoneStartBody, get_microphone_manager
from classsimvr.services import transcript_assessment as assessment
from classsimvr.services.auth import create_access_token
from classsimvr.services.session_contracts import ClassroomContext


@pytest.fixture
def database(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "assessment-test-bearer-key-only-for-isolated-tests")
    monkeypatch.delenv("ASSESSMENT_APPROVED_SCORE_ANCHORS", raising=False)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db_factory = sessionmaker(bind=engine)
    with db_factory() as db:
        headers = []
        for number in (1, 2):
            email = f"fixture{number}@example.invalid"
            trainee = Trainee(full_name=f"Teacher {number}", email=email)
            db.add(trainee)
            db.flush()
            account = AuthAccount(trainee_id=trainee.trainee_id, username=f"fixture{number}",
                                  email=email, password_hash="unused", is_active=True)
            db.add(account)
            db.flush()
            token, _ = create_access_token(account)
            headers.append({"Authorization": f"Bearer {token}"})
        db.add_all([
            Session(trainee_id=1, session_mode="assessment", status="running", grade="Grade 4", subject="Mathematics"),
            Session(trainee_id=2, session_mode="assessment", status="running", grade="Grade 4", subject="Science"),
            Session(trainee_id=1, session_mode="practice", status="running"),
        ])
        db.commit()
    return db_factory, headers


def test_owned_timed_microphone_to_report(database, monkeypatch):
    db_factory, headers = database
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(routes, "utc_now", lambda: now[0])
    monkeypatch.setattr(assessment, "utc_now", lambda: now[0])
    monkeypatch.setattr(assessment, "SessionLocal", db_factory)
    coordinator = ListeningCoordinator()
    coordinator.listener = Mock()
    coordinator.listener.start.return_value = {"state": "listening"}
    coordinator.listener.current_command_timing.return_value = (None, {})
    coordinator.listener.has_pending_assessment_audio.return_value = False
    coordinator.router = Mock(wraps=coordinator.router)
    coordinator.router.handle = Mock(return_value=SimpleNamespace(
        target="maria-sandoval", responses=[SimpleNamespace(
            persona_id="maria-sandoval", dialogue="The parts are equal.", emotion="curious",
            action="NONE", speak=True, audio_url="/audio/maria.mp3",
        ).__dict__],
    ))
    # Use the exact production queue object contract.
    from classsimvr.services.contracts import BehaviorResponse
    coordinator.router.handle.return_value.responses = [
        BehaviorResponse.model_validate(reply) for reply in coordinator.router.handle.return_value.responses
    ]
    calls = []

    def evaluate(context, events, anchors):
        calls.append((context, events))
        if len(calls) == 1:
            raise TimeoutError("Mock provider unavailable")
        event_id = events[0]["event_id"]
        return assessment.Feedback.model_validate({
            "findings": [{"code": "1.5.1", "interpretation": "The teacher asks for a fraction explanation.",
                          "event_ids": [event_id], "score": 4}],
            "recommendations": [{
                "opportunity": "Ask students to justify equal parts.",
                "rationale": "Reasoning helps show whether they understand fractions.",
                "strategy": "Compare two drawings and ask which one shows equal halves.",
                "event_ids": [event_id],
            }],
        }), "mock-openai"

    def worker(*args):
        return assessment.run_job(*args, db_factory=db_factory, evaluator=evaluate)

    def database_override():
        with db_factory() as db:
            yield db

    monkeypatch.setattr(routes, "run_job", worker)
    app.dependency_overrides[get_session] = database_override
    app.dependency_overrides[get_microphone_manager] = lambda: coordinator
    # The job drains the production singleton in-process.
    monkeypatch.setattr("classsimvr.routes.microphone.get_microphone_manager", lambda: coordinator)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            path = "/api/v1/sessions/1/assessment"
            setup = {"starting_seconds": 10, "assessment_seconds": 60, "notice_version": assessment.NOTICE}
            assert (await http.post(path + "/window", json=setup)).status_code == 401
            assert (await http.post(path + "/window", headers=headers[1], json=setup)).status_code == 404
            assert (await http.post("/api/v1/sessions/3/assessment/window", headers=headers[0], json=setup)).status_code == 409
            opened = await http.post(path + "/window", headers=headers[0], json=setup)
            assert opened.json()["seconds_until_start"] == 10
            assert (await http.post(path, headers=headers[0])).status_code == 409
            coordinator.start(MicrophoneStartBody(session_id="db:1"),
                              classroom_context=ClassroomContext.from_selection("Grade 4", "Mathematics", "assessment"))

            def speech(text):
                stamp = assessment.time.perf_counter()
                coordinator.listener.current_speech_bounds.return_value = (stamp - 0.4, stamp - 0.1)
                coordinator._on_transcription(text)

            speech("Before countdown finishes")
            now[0] += timedelta(seconds=15)
            speech("Maria, why do fractions need equal parts?")
            now[0] += timedelta(seconds=56)
            speech("After assessment")
            with db_factory() as db:
                events = list(db.scalars(select(Turn).order_by(Turn.turn_id)))
                assert [e.source for e in events] == [assessment.TEACHER_SOURCE, assessment.STUDENT_SOURCE]
                assert events[1].student_actor_tag == "maria-sandoval"
                assert all(e.session_id == 1 and 4 <= e.t_offset_s < 6 for e in events)
                assert events[0].t_end_offset_s < events[1].t_offset_s
            queued = await http.post(path, headers=headers[0])
            assert queued.status_code == 202, queued.text
            job_id = queued.json()["job_id"]
            assert (await http.get(f"/api/v1/assessment/jobs/{job_id}", headers=headers[1])).status_code == 404
            status = await http.get(f"/api/v1/assessment/jobs/{job_id}", headers=headers[0])
            assert status.json()["status"] == "failed"
            assert (await http.get("/api/v1/sessions/1/report", headers=headers[0])).status_code == 404
            assert (await http.post(path, headers=headers[0])).json()["job_id"] == job_id
            status = await http.get(f"/api/v1/assessment/jobs/{job_id}", headers=headers[0])
            assert status.json()["status"] == "completed"
            assert (await http.post(path, headers=headers[0])).json()["job_id"] == job_id
            assert (await http.get("/api/v1/sessions/1/report", headers=headers[1])).status_code == 404
            report = (await http.get("/api/v1/sessions/1/report", headers=headers[0])).json()
            feedback = report["transcript_feedback"]
            assert feedback["overall_score"] is None and feedback["duration_seconds"] == 60
            assert set(feedback["qualitative"]) == {"1.5.1"}
            assert len(report["indicators"]) == 9
            assert all(not i["observed"] and i["score"] is None for i in report["indicators"])
            assert "2.6.1" in feedback["titles"]
            assert feedback["recommendations"][0]["evidence"][0]["text"] == "Maria, why do fractions need equal parts?"
            assert (await http.delete(path + "/window", headers=headers[1])).status_code == 404
            assert (await http.delete(path + "/window", headers=headers[0])).status_code == 204
            assert (await http.get("/api/v1/sessions/1/report", headers=headers[0])).status_code == 409
        assert len(calls) == 2
        assert calls[0][0] == {"grade": "Grade 4", "subject": "Mathematics", "mode": "assessment"}
        with db_factory() as db:
            assert len(list(db.scalars(select(Report)))) == 1
            assert len(list(db.scalars(select(AssessmentJob)))) == 1

    try:
        asyncio.run(exercise())
    finally:
        app.dependency_overrides.clear()


def test_invalid_citations_and_supported_scores():
    events = [{"event_id": 1, "kind": "teacher_transcript", "text": "Explain why.",
               "t_start_s": 1, "t_end_s": 2}]
    finding = assessment.Finding(code="1.5.1", interpretation="Prompts for reasoning.", event_ids=[99], score=3)
    feedback = assessment.Feedback(findings=[finding], recommendations=[])
    with pytest.raises(ValueError, match="known teacher evidence"):
        assessment.build_report(feedback, events, {})
    finding.event_ids = [1]
    indicators, details = assessment.build_report(feedback, events, {"1.5.1": ["a", "b", "c", "d"]})
    assert next(item for item in indicators if item["bti_code"] == "1.5.1")["score"] == 3
    assert details["findings"]["1.5.1"]["evidence"][0]["text"] == "Explain why."
    finding.code = "unapproved"
    with pytest.raises(ValueError, match="Unknown"):
        assessment.build_report(feedback, events, {})


def test_no_speech_stores_not_observed_without_a_provider_call(database):
    db_factory, _ = database
    now = datetime.now(timezone.utc)
    with db_factory() as db:
        session = db.get(Session, 1)
        assessment.arm_window(session, 10, 60, assessment.NOTICE, now - timedelta(seconds=80))
        job = AssessmentJob(session_id=1, status="running", provider=assessment.PROVIDER, started_at=now)
        db.add(job)
        db.commit()
        job_id = job.job_id
    model = Mock(side_effect=AssertionError("No teaching evidence should reach the provider"))
    assessment.run_job(job_id, 1, now, lambda: True, db_factory=db_factory, evaluator=model)
    model.assert_not_called()
    with db_factory() as db:
        report = db.get(Report, 1)
        assert len(report.indicator_scores) == 10
        assert all(not score.observed for score in report.indicator_scores)
        assert report.transcript_feedback["first_teacher_moment"] is None
