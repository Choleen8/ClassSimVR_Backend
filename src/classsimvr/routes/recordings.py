from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
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


@router.put("/sessions/{session_id}/consent", response_model=ConsentResponse)
def set_consent(
    session_id: int,
    data: ConsentRequest,
    db: DbSession = Depends(get_session),
    service: RecordingService = Depends(get_recording_service),
) -> ConsentResponse:
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
) -> ConsentResponse:
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
) -> RecordingResponse:
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
) -> RecordingResponse:
    try:
        return _recording_response(service.complete_recording(db, session_id, data))
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RecordingStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
