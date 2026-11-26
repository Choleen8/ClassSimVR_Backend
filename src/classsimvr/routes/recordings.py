from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..models import Session
from ..services.auth import bearer_scheme, get_current_account
from ..services.personas import SessionNotFoundError
from ..services.recording_contracts import (
    ConsentRequest,
    ConsentResponse,
    RecordingCompleteRequest,
    RecordingResponse,
    RecordingStartRequest,
)
from ..services.recordings import (
    ConsentRequiredError,
    RecordingService,
    RecordingStateError,
)

router = APIRouter(prefix="/api/v1")


def get_recording_service() -> RecordingService:
    return RecordingService()


def _protect_assessment_recording(db, session_id, credentials):
    session = db.get(Session, session_id)
    if session is not None and session.session_mode == "assessment":
        if get_current_account(credentials, db).trainee_id != session.trainee_id:
            raise HTTPException(status_code=404, detail="classroom session was not found")


@router.put("/sessions/{session_id}/consent", response_model=ConsentResponse)
def set_consent(
    session_id: int,
    data: ConsentRequest,
    db: DbSession = Depends(get_session),
    service: RecordingService = Depends(get_recording_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> ConsentResponse:
    _protect_assessment_recording(db, session_id, credentials)
    try:
        consent = service.set_consent(db, session_id, data)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ConsentResponse(
        session_id=consent.session_id,
        video_recording=consent.video_recording == "granted",
        given_at=consent.given_at,
        purpose=consent.purpose,
        notice_version=consent.notice_version,
        withdrawn_at=consent.withdrawn_at,
    )


@router.delete("/sessions/{session_id}/consent", response_model=ConsentResponse)
def withdraw_consent(
    session_id: int,
    db: DbSession = Depends(get_session),
    service: RecordingService = Depends(get_recording_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> ConsentResponse:
    _protect_assessment_recording(db, session_id, credentials)
    try:
        consent = service.withdraw_consent(db, session_id)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ConsentRequiredError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ConsentResponse(
        session_id=consent.session_id,
        video_recording=False,
        given_at=consent.given_at,
        purpose=consent.purpose,
        notice_version=consent.notice_version,
        withdrawn_at=consent.withdrawn_at,
    )


def _recording_response(recording) -> RecordingResponse:
    return RecordingResponse(
        session_id=recording.session_id,
        status=recording.status,
        mime_type=recording.mime_type,
        storage_ref=recording.file_path,
        duration_s=recording.duration_s,
        size_bytes=recording.size_bytes,
        started_at=recording.started_at,
        completed_at=recording.completed_at,
    )


@router.post(
    "/sessions/{session_id}/recording/start",
    response_model=RecordingResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_recording(
    session_id: int,
    data: RecordingStartRequest,
    db: DbSession = Depends(get_session),
    service: RecordingService = Depends(get_recording_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> RecordingResponse:
    _protect_assessment_recording(db, session_id, credentials)
    try:
        return _recording_response(service.start_recording(db, session_id, data))
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ConsentRequiredError, RecordingStateError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/sessions/{session_id}/recording/complete",
    response_model=RecordingResponse,
)
def complete_recording(
    session_id: int,
    data: RecordingCompleteRequest,
    db: DbSession = Depends(get_session),
    service: RecordingService = Depends(get_recording_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> RecordingResponse:
    _protect_assessment_recording(db, session_id, credentials)
    try:
        return _recording_response(service.complete_recording(db, session_id, data))
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RecordingStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/sessions/{session_id}/recording/upload",
    response_model=RecordingResponse,
)
async def upload_recording(
    session_id: int,
    duration_s: float = Form(..., gt=0, le=86_400),
    file: UploadFile = File(...),
    db: DbSession = Depends(get_session),
    service: RecordingService = Depends(get_recording_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> RecordingResponse:
    _protect_assessment_recording(db, session_id, credentials)
    try:
        recording = await service.save_upload(db, session_id, file, duration_s)
        return _recording_response(recording)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ConsentRequiredError, RecordingStateError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
