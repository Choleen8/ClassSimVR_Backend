from __future__ import annotations

from datetime import timedelta
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..models import AssessmentJob, Session
from ..services.auth import bearer_scheme, get_current_account
from ..services.assessment import AssessmentService, AssessmentStateError
from ..services.assessment_contracts import (
    AssessmentJobResponse,
    AssessmentResult,
    AssessmentResultResponse,
    ReportEvidenceResponse,
    ReportIndicatorResponse,
    ReportResponse,
    list_bti_rubric,
)
from ..services.personas import SessionNotFoundError
from ..services.transcript_assessment import PROVIDER, arm_window, lock_session, run_job, utc, utc_now

router = APIRouter(prefix="/api/v1")


def get_assessment_service() -> AssessmentService:
    return AssessmentService()


def authorize_timed_session(session, credentials, db):
    account = get_current_account(credentials, db)
    if session is None or session.trainee_id != account.trainee_id:
        raise HTTPException(status_code=404, detail="classroom session was not found")


class AssessmentWindowRequest(BaseModel):
    starting_seconds: int = Field(ge=1, le=30)
    assessment_seconds: int = Field(ge=1, le=3600)
    notice_version: str = Field(max_length=50)


class AssessmentWindowResponse(BaseModel):
    session_id: int
    seconds_until_start: float
    assessment_seconds: float


@router.post("/sessions/{session_id}/assessment/window", response_model=AssessmentWindowResponse)
def start_assessment_window(
    session_id: int,
    data: AssessmentWindowRequest,
    db: DbSession = Depends(get_session),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    session = lock_session(db, session_id)
    authorize_timed_session(session, credentials, db)
    try:
        if db.scalar(select(AssessmentJob).where(AssessmentJob.session_id == session_id)) is not None and session.assessment_started_at is None:
            raise AssessmentStateError("An assessment already exists for this session.")
        start, end = arm_window(session, data.starting_seconds, data.assessment_seconds, data.notice_version, utc_now())
        db.commit()
    except AssessmentStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return AssessmentWindowResponse(
        session_id=session_id, seconds_until_start=(start - utc_now()).total_seconds(),
        assessment_seconds=(end - start).total_seconds(),
    )


@router.delete("/sessions/{session_id}/assessment/window", status_code=204)
def withdraw_transcript_acknowledgment(
    session_id: int,
    db: DbSession = Depends(get_session),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    session = lock_session(db, session_id)
    authorize_timed_session(session, credentials, db)
    if session.assessment_started_at is None:
        raise HTTPException(status_code=409, detail="No timed assessment was started.")
    session.assessment_status = "not_gradable"
    session.assessment_message = "Transcript assessment acknowledgment was withdrawn."
    db.commit()


def queue_transcript_job(session, db, tasks):
    from .microphone import get_microphone_manager

    if session.assessment_status != "gradable" or session.session_mode != "assessment":
        raise HTTPException(status_code=409, detail="Assessment is unavailable for this session.")
    now = utc_now()
    cutoff = utc(session.assessment_ended_at)
    if now < cutoff:
        raise HTTPException(status_code=409, detail="Assessment time has not ended.")
    session_id = session.session_id
    job = db.scalar(select(AssessmentJob).where(AssessmentJob.session_id == session_id))
    if job is not None:
        if job.provider != PROVIDER:
            raise HTTPException(status_code=409, detail="A recording-based assessment already exists.")
        if job.status == "completed" or (
            job.status == "running" and now - utc(job.started_at) < timedelta(seconds=120)
        ):
            return job
    else:
        job = AssessmentJob(session_id=session_id, provider=PROVIDER)
        db.add(job)
    job.status, job.started_at, job.error = "running", now, None
    db.commit()
    cutoff_clock = time.perf_counter() + (cutoff - utc_now()).total_seconds()
    tasks.add_task(
        run_job, job.job_id, session_id, now,
        lambda: get_microphone_manager().drain_assessment_evidence(f"db:{session_id}", cutoff_clock),
    )
    return job


def _job_response(job) -> AssessmentJobResponse:
    return AssessmentJobResponse(
        job_id=job.job_id,
        session_id=job.session_id,
        status=job.status,
        provider=job.provider,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/assessment/rubric")
def get_assessment_rubric():
    return list_bti_rubric()


@router.post(
    "/sessions/{session_id}/assessment",
    response_model=AssessmentJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_assessment(
    session_id: int,
    db: DbSession = Depends(get_session),
    service: AssessmentService = Depends(get_assessment_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    tasks: BackgroundTasks = None,
) -> AssessmentJobResponse:
    try:
        session = lock_session(db, session_id) if db is not None else None
        if session is not None and (session.session_mode == "assessment" or session.assessment_started_at is not None):
            authorize_timed_session(session, credentials, db)
            if session.assessment_started_at is not None:
                return _job_response(queue_transcript_job(session, db, tasks))
        return _job_response(service.queue_assessment(db, session_id))
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except AssessmentStateError as error:
        if error.code == "recording_consent_withdrawn":
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "code": error.code,
                    "status": "not_gradable",
                    "message": str(error),
                },
            )
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/assessment/jobs/{job_id}", response_model=AssessmentJobResponse)
def get_assessment_job(
    job_id: int,
    db: DbSession = Depends(get_session),
    service: AssessmentService = Depends(get_assessment_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AssessmentJobResponse:
    try:
        job = service.get_job(db, job_id)
        session = db.get(Session, job.session_id)
        if job.provider == PROVIDER or (session and session.session_mode == "assessment"):
            authorize_timed_session(session, credentials, db)
        return _job_response(job)
    except AssessmentStateError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/assessment/jobs/{job_id}/result",
    response_model=AssessmentResultResponse,
)
def complete_assessment(
    job_id: int,
    data: AssessmentResult,
    db: DbSession = Depends(get_session),
    service: AssessmentService = Depends(get_assessment_service),
) -> AssessmentResultResponse:
    try:
        job = service.get_job(db, job_id)
        session = lock_session(db, job.session_id)
        if job.provider == PROVIDER or (session and session.session_mode == "assessment"):
            raise HTTPException(status_code=403, detail="Transcript assessment results are generated by the server.")
        completed = service.complete_assessment(db, job_id, data)
    except AssessmentStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return AssessmentResultResponse(
        job_id=completed.job.job_id,
        session_id=completed.job.session_id,
        status=completed.status,
        report_session_id=completed.report.session_id,
    )


@router.get("/sessions/{session_id}/report", response_model=ReportResponse)
def get_report(
    session_id: int,
    db: DbSession = Depends(get_session),
    service: AssessmentService = Depends(get_assessment_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> ReportResponse:
    session = db.get(Session, session_id)
    if session and (session.session_mode == "assessment" or session.assessment_started_at is not None):
        authorize_timed_session(session, credentials, db)
        if session.assessment_status != "gradable":
            raise HTTPException(status_code=409, detail="Assessment is unavailable for this session.")
    try:
        report = service.get_report(db, session_id)
    except AssessmentStateError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    indicators = []
    rubric = {item.bti_code: item.title for item in list_bti_rubric()}
    for score in report.indicator_scores:
        observed_score = getattr(score, "score", None)
        evidence = [
            ReportEvidenceResponse(
                t_start_s=item.t_start_s,
                t_end_s=item.t_end_s,
                note=item.note,
            )
            for item in getattr(score, "evidence", [])
        ]
        indicators.append(
            ReportIndicatorResponse(
                bti_code=score.bti.strand,
                title=rubric.get(score.bti.strand),
                observed=score.observed,
                score=observed_score,
                evidence=evidence,
            )
        )
    return ReportResponse(
        session_id=report.session_id,
        summary=report.summary,
        next_focus=report.next_focus,
        model_assess=report.model_assess,
        indicators=indicators,
        transcript_feedback=report.transcript_feedback,
    )
