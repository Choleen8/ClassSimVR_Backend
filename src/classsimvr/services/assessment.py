from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DbSession

from ..models import (
    AssessmentJob,
    Bti,
    Evidence,
    NotObserved,
    ObservedScore,
    Recording,
    Report,
    Session,
)
from .assessment_contracts import AssessmentResult, list_bti_rubric
from .personas import SessionNotFoundError


class AssessmentStateError(ValueError):
    def __init__(self, message: str, *, code: str = "assessment_state_invalid"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CompletedAssessment:
    job: AssessmentJob
    report: Report

    @property
    def status(self) -> str:
        return self.job.status


class AssessmentService:
    def queue_assessment(self, db: DbSession, session_id: int) -> AssessmentJob:
        session = db.get(Session, session_id)
        if session is None:
            raise SessionNotFoundError(f"session {session_id} was not found")
        if session.assessment_status == "not_gradable":
            raise AssessmentStateError(
                session.assessment_message
                or "This session cannot be graded.",
                code="recording_consent_withdrawn",
            )
        recording = db.get(Recording, session_id)
        if session.status != "completed" or recording is None or recording.status != "completed":
            raise AssessmentStateError(
                "assessment requires a completed session recording"
            )
        existing = db.query(AssessmentJob).filter_by(session_id=session_id).first()
        if existing is not None:
            raise AssessmentStateError("assessment is already queued for this session")
        job = AssessmentJob(session_id=session_id, status="pending")
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    def get_job(self, db: DbSession, job_id: int) -> AssessmentJob:
        job = db.get(AssessmentJob, job_id)
        if job is None:
            raise AssessmentStateError(f"assessment job {job_id} was not found")
        return job

    def complete_assessment(
        self, db: DbSession, job_id: int, data: AssessmentResult
    ) -> CompletedAssessment:
        job = self.get_job(db, job_id)
        if job.status != "pending":
            raise AssessmentStateError("assessment job is not pending")
        rubric_by_code = {item.bti_code: item for item in list_bti_rubric()}
        report = Report(
            session_id=job.session_id,
            summary=data.summary,
            next_focus=data.next_focus,
            model_assess=data.model_assess,
        )
        db.add(report)
        db.flush()
        for item in data.indicators:
            rubric = rubric_by_code.get(item.bti_code)
            if rubric is None:
                raise AssessmentStateError(f"unknown BTI code {item.bti_code}")
            bti = db.query(Bti).filter_by(strand=item.bti_code).first()
            if bti is None:
                bti = Bti(strand=item.bti_code, descriptor=rubric.indicator)
                db.add(bti)
                db.flush()
            if item.observed:
                score = ObservedScore(
                    report_session_id=report.session_id,
                    bti_id=bti.bti_id,
                    observed=True,
                    score_type="observed",
                    score=item.score,
                )
                score.evidence.extend(
                    Evidence(
                        t_start_s=evidence.t_start_s,
                        t_end_s=evidence.t_end_s,
                        note=evidence.note,
                    )
                    for evidence in item.evidence
                )
            else:
                score = NotObserved(
                    report_session_id=report.session_id,
                    bti_id=bti.bti_id,
                    observed=False,
                    score_type="not_observed",
                )
            report.indicator_scores.append(score)
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)
        db.refresh(report)
        return CompletedAssessment(job=job, report=report)

    def get_report(self, db: DbSession, session_id: int) -> Report:
        report = db.get(Report, session_id)
        if report is None:
            raise AssessmentStateError(f"report for session {session_id} was not found")
        return report
