from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..models import Session
from ..services.transcript_assessment import lock_session
from ..services.auth import bearer_scheme, get_current_account
from ..services.persona_contracts import AssignedPersonaResponse
from ..services.session_contracts import (
    GeneratedClassResponse,
    SessionCreate,
    SessionLifecycleResponse,
    SessionResponse,
    StudentGenerationRequest,
    TraineeHistoryResponse,
)
from ..services.sessions import (
    ClassAlreadyGeneratedError,
    SessionLifecycleError,
    SessionService,
)
from ..services.personas import SessionNotFoundError, TraineeNotFoundError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


def get_session_service() -> SessionService:
    return SessionService()


def _session_response(session) -> SessionResponse:
    return SessionResponse(
        session_id=session.session_id,
        trainee_id=session.trainee_id,
        grade=session.grade,
        subject=session.subject,
        topic=session.topic,
        session_mode=session.session_mode,
        competency=session.competency,
        lesson_plan=session.lesson_plan,
        answer_key=session.answer_key,
        class_size=session.class_size,
        difficulty=session.difficulty,
        status=session.status,
        created_at=session.created_at,
    )


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    data: SessionCreate,
    db: DbSession = Depends(get_session),
    service: SessionService = Depends(get_session_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> SessionResponse:
    try:
        if data.start_immediately:
            account = get_current_account(credentials, db)
            if account.trainee_id != data.trainee_id:
                raise HTTPException(status_code=403, detail="classroom session must belong to the signed-in trainee")
        return _session_response(service.create_session(db, data))
    except SQLAlchemyError as error:
        if not data.start_immediately:
            raise
        logger.warning("Classroom session creation failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="classroom session is unavailable") from error
    except TraineeNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _history_response(session) -> TraineeHistoryResponse:
    report = session.report
    return TraineeHistoryResponse(
        session_id=session.session_id,
        trainee_id=session.trainee_id,
        grade=session.grade,
        subject=session.subject,
        topic=session.topic,
        session_mode=session.session_mode,
        competency=session.competency,
        class_size=session.class_size,
        difficulty=session.difficulty,
        status=session.status,
        assessment_status=session.assessment_status,
        assessment_message=session.assessment_message,
        created_at=session.created_at,
        started_at=session.started_at,
        completed_at=session.completed_at,
        report_available=report is not None,
        summary=report.summary if report is not None else None,
        next_focus=report.next_focus if report is not None else None,
    )


@router.get(
    "/trainees/{trainee_id}/history",
    response_model=list[TraineeHistoryResponse],
)
def get_trainee_history(
    trainee_id: int,
    db: DbSession = Depends(get_session),
    service: SessionService = Depends(get_session_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> list[TraineeHistoryResponse]:
    try:
        sessions = service.list_history(db, trainee_id)
    except TraineeNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    owns_history = credentials is not None and get_current_account(credentials, db).trainee_id == trainee_id
    return [_history_response(session) for session in sessions
            if session.assessment_started_at is None or (owns_history and session.assessment_status == "gradable")]


def _student_response(assignment) -> AssignedPersonaResponse:
    persona = assignment.persona
    return AssignedPersonaResponse(
        session_id=assignment.session_id,
        persona_id=assignment.persona_id,
        name=persona.name,
        language=persona.language,
        avatar_ref=persona.avatar_ref,
        personality_traits=persona.personality_traits,
        behavior_triggers=persona.behavior_triggers,
        response_style=persona.response_style,
        participation_level=persona.participation_level,
        initial_state=assignment.initial_state,
        current_state=assignment.current_state,
        current_emotion=assignment.current_emotion,
        state_summary=assignment.state_summary,
        state_data=assignment.state_data,
        is_active=assignment.is_active,
    )


@router.post(
    "/sessions/{session_id}/students/generate",
    response_model=GeneratedClassResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_students(
    session_id: int,
    data: StudentGenerationRequest,
    db: DbSession = Depends(get_session),
    service: SessionService = Depends(get_session_service),
) -> GeneratedClassResponse:
    try:
        generated = service.generate_students(
            db, session_id, data.class_size, data.seed
        )
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ClassAlreadyGeneratedError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return GeneratedClassResponse(
        session_id=generated.session_id,
        class_size=generated.class_size,
        seed=generated.seed,
        students=[_student_response(item) for item in generated.assignments],
    )


def _lifecycle_response(session) -> SessionLifecycleResponse:
    return SessionLifecycleResponse(
        session_id=session.session_id,
        trainee_id=session.trainee_id,
        status=session.status,
        class_size=session.class_size,
        started_at=session.started_at,
        paused_at=session.paused_at,
        completed_at=session.completed_at,
    )


def _transition(service_method, session_id: int, db: DbSession):
    try:
        session = lock_session(db, session_id)
        if session is not None and session.assessment_started_at is not None:
            raise HTTPException(status_code=409, detail="The timed assessment manages its own lifecycle.")
        return _lifecycle_response(service_method(db, session_id))
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SessionLifecycleError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/sessions/{session_id}/start", response_model=SessionLifecycleResponse)
def start_session(
    session_id: int,
    db: DbSession = Depends(get_session),
    service: SessionService = Depends(get_session_service),
) -> SessionLifecycleResponse:
    return _transition(service.start_session, session_id, db)


@router.post("/sessions/{session_id}/pause", response_model=SessionLifecycleResponse)
def pause_session(
    session_id: int,
    db: DbSession = Depends(get_session),
    service: SessionService = Depends(get_session_service),
) -> SessionLifecycleResponse:
    return _transition(service.pause_session, session_id, db)


@router.post("/sessions/{session_id}/resume", response_model=SessionLifecycleResponse)
def resume_session(
    session_id: int,
    db: DbSession = Depends(get_session),
    service: SessionService = Depends(get_session_service),
) -> SessionLifecycleResponse:
    return _transition(service.resume_session, session_id, db)


@router.post("/sessions/{session_id}/complete", response_model=SessionLifecycleResponse)
def complete_session(
    session_id: int,
    db: DbSession = Depends(get_session),
    service: SessionService = Depends(get_session_service),
) -> SessionLifecycleResponse:
    return _transition(service.complete_session, session_id, db)


@router.post("/sessions/{session_id}/reset", response_model=SessionLifecycleResponse)
def reset_session(
    session_id: int,
    db: DbSession = Depends(get_session),
    service: SessionService = Depends(get_session_service),
) -> SessionLifecycleResponse:
    try:
        session = lock_session(db, session_id)
        if session is not None and session.assessment_started_at is not None:
            raise HTTPException(status_code=409, detail="Start a new classroom instead of resetting a timed assessment.")
        return _lifecycle_response(service.reset_session(db, session_id))
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SessionLifecycleError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
