from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
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

router = APIRouter(prefix="/api/v1")


def get_assessment_service() -> AssessmentService:
    return AssessmentService()


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
) -> AssessmentJobResponse:
    try:
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
) -> AssessmentJobResponse:
    try:
        return _job_response(service.get_job(db, job_id))
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
) -> ReportResponse:
    try:
        report = service.get_report(db, session_id)
    except AssessmentStateError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    indicators = []
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
    )
