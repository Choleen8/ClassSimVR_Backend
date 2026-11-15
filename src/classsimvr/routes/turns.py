from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..services.personas import SessionNotFoundError
from ..services.turn_contracts import TurnCreate, TurnResponse
from ..services.turns import TurnPersonaNotFoundError, TurnService

router = APIRouter(prefix="/api/v1")


def get_turn_service() -> TurnService:
    return TurnService()


@router.post(
    "/sessions/{session_id}/turns",
    response_model=TurnResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_turn(
    session_id: int,
    data: TurnCreate,
    db: DbSession = Depends(get_session),
    service: TurnService = Depends(get_turn_service),
) -> TurnResponse:
    try:
        turn = service.create_turn(db, session_id, data)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TurnPersonaNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return TurnResponse(
        turn_id=turn.turn_id,
        session_id=turn.session_id,
        persona_id=turn.persona_id,
        t_offset_s=turn.t_offset_s,
        teacher_text=turn.teacher_text,
        learner_text=turn.learner_text,
        emotion=turn.emotion,
        action=turn.action,
        source=turn.source,
    )
