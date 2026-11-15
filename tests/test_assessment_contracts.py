import pytest
import json
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession

from classsimvr.models import AssessmentJob, Base, Recording, Session, Trainee
from classsimvr.services.assessment import AssessmentService, AssessmentStateError
from classsimvr.services.recording_contracts import ConsentRequest
from classsimvr.services.recordings import RecordingService
from classsimvr.routes.assessment import queue_assessment
from classsimvr.services.assessment_contracts import (
    AssessmentResult,
    OBSERVED_SCORE_MAX,
    OBSERVED_SCORE_MIN,
    BtiRubricItem,
    ObservedAssessmentItem,
    list_bti_rubric,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with DbSession(engine) as session:
        yield session


def create_completed_session(db: DbSession) -> Session:
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
    return session


def test_paper_bti_rubric_has_ten_structured_indicators():
    rubric = list_bti_rubric()

    assert len(rubric) == 10
    assert rubric[0].bti_code == "1.1.1"
    assert rubric[-1].bti_code == "3.1.1"


def test_observed_assessment_score_is_limited_to_one_through_four():
    assert ObservedAssessmentItem(bti_code="1.1.1", score=OBSERVED_SCORE_MIN)
    assert ObservedAssessmentItem(bti_code="1.1.1", score=OBSERVED_SCORE_MAX)
    with pytest.raises(ValidationError):
        ObservedAssessmentItem(bti_code="1.1.1", score=0)
    with pytest.raises(ValidationError):
        ObservedAssessmentItem(bti_code="1.1.1", score=5)


def test_assessment_queue_requires_completed_recording(db):
    trainee = Trainee(full_name="Teacher One", email="teacher@example.com")
    db.add(trainee)
    db.flush()
    session = Session(trainee_id=trainee.trainee_id, status="running")
    db.add(session)
    db.commit()

    with pytest.raises(AssessmentStateError):
        AssessmentService().queue_assessment(db, session.session_id)


def test_assessment_queue_returns_pending_job(db):
    session = create_completed_session(db)

    job = AssessmentService().queue_assessment(db, session.session_id)

    assert job.session_id == session.session_id
    assert job.status == "pending"
    assert AssessmentService().get_job(db, job.job_id).status == "pending"


def test_assessment_is_not_gradable_after_recording_consent_withdrawal(db):
    session = create_completed_session(db)
    recording_service = RecordingService()
    recording_service.set_consent(
        db,
        session.session_id,
        ConsentRequest(
            video_recording=True,
            purpose="approved training review",
            notice_version="2026-11-09",
        ),
    )
    recording_service.withdraw_consent(db, session.session_id)

    with pytest.raises(
        AssessmentStateError,
        match="Recording consent was withdrawn; this session cannot be graded.",
    ):
        AssessmentService().queue_assessment(db, session.session_id)

    assert db.query(AssessmentJob).filter_by(session_id=session.session_id).first() is None


def test_assessment_route_returns_non_gradable_message():
    class NonGradableAssessmentService:
        def queue_assessment(self, db, session_id):
            raise AssessmentStateError(
                "Recording consent was withdrawn; this session cannot be graded.",
                code="recording_consent_withdrawn",
            )

    response = queue_assessment(
        1,
        db=None,
        service=NonGradableAssessmentService(),
    )

    assert response.status_code == 409
    assert json.loads(response.body) == {
        "code": "recording_consent_withdrawn",
        "status": "not_gradable",
        "message": "Recording consent was withdrawn; this session cannot be graded.",
    }


def test_assessment_result_persists_report_scores_and_evidence(db):
    session = create_completed_session(db)
    service = AssessmentService()
    job = service.queue_assessment(db, session.session_id)

    result = service.complete_assessment(
        db,
        job.job_id,
        AssessmentResult(
            summary="The trainee used clear explanations.",
            next_focus="Ask more higher-order questions.",
            indicators=[
                {
                    "bti_code": "1.1.1",
                    "observed": True,
                    "score": 3,
                    "evidence": [
                        {"t_start_s": 12.0, "t_end_s": 18.0, "note": "Clear example."}
                    ],
                }
            ],
        ),
    )

    assert result.status == "completed"
    assert result.report.summary == "The trainee used clear explanations."
    assert len(result.report.indicator_scores) == 1
    assert result.report.indicator_scores[0].evidence[0].note == "Clear example."
