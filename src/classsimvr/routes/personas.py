from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..services.persona_contracts import (
    AssignedPersonaResponse,
    PersonaCreate,
    PersonaResponse,
    SessionPersonaAssign,
    SessionPersonaStateUpdate,
)
from ..services.personas import (
    AssignmentExistsError,
    PersonaNotFoundError,
    PersonaService,
    SessionNotFoundError,
    SessionPersonaNotFoundError,
    TraineeNotFoundError,
)

router = APIRouter(prefix="/api/v1")


def get_persona_service() -> PersonaService:
    return PersonaService()


def _assigned_response(assignment) -> AssignedPersonaResponse:
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


@router.post("/personas", response_model=PersonaResponse, status_code=status.HTTP_201_CREATED)
def create_persona(
    data: PersonaCreate,
    db: DbSession = Depends(get_session),
    service: PersonaService = Depends(get_persona_service),
) -> PersonaResponse:
    return PersonaResponse.model_validate(service.create_persona(db, data))


@router.get("/personas", response_model=list[PersonaResponse])
def list_personas(
    db: DbSession = Depends(get_session),
    service: PersonaService = Depends(get_persona_service),
) -> list[PersonaResponse]:
    return [PersonaResponse.model_validate(persona) for persona in service.list_personas(db)]


@router.get("/trainees/{trainee_id}/personas", response_model=list[PersonaResponse])
def list_trainee_personas(
    trainee_id: int,
    db: DbSession = Depends(get_session),
    service: PersonaService = Depends(get_persona_service),
) -> list[PersonaResponse]:
    try:
        personas = service.list_personas_for_trainee(db, trainee_id)
    except TraineeNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return [PersonaResponse.model_validate(persona) for persona in personas]


@router.post(
    "/sessions/{session_id}/personas",
    response_model=AssignedPersonaResponse,
    status_code=status.HTTP_201_CREATED,
)
def assign_persona(
    session_id: int,
    data: SessionPersonaAssign,
    db: DbSession = Depends(get_session),
    service: PersonaService = Depends(get_persona_service),
) -> AssignedPersonaResponse:
    try:
        return _assigned_response(service.assign_persona(db, session_id, data))
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PersonaNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except AssignmentExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/sessions/{session_id}/personas", response_model=list[AssignedPersonaResponse])
def list_assigned_personas(
    session_id: int,
    db: DbSession = Depends(get_session),
    service: PersonaService = Depends(get_persona_service),
) -> list[AssignedPersonaResponse]:
    try:
        return [_assigned_response(item) for item in service.list_assigned_personas(db, session_id)]
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch(
    "/sessions/{session_id}/personas/{persona_id}",
    response_model=AssignedPersonaResponse,
)
def update_session_persona(
    session_id: int,
    persona_id: int,
    data: SessionPersonaStateUpdate,
    db: DbSession = Depends(get_session),
    service: PersonaService = Depends(get_persona_service),
) -> AssignedPersonaResponse:
    try:
        return _assigned_response(
            service.update_session_state(db, session_id, persona_id, data)
        )
    except SessionPersonaNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
