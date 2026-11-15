from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from ..models import Session, SessionPersona, Turn
from .personas import SessionNotFoundError
from .turn_contracts import TurnCreate


class TurnPersonaNotFoundError(LookupError):
    pass


class TurnService:
    def create_turn(self, db: DbSession, session_id: int, data: TurnCreate) -> Turn:
        if db.get(Session, session_id) is None:
            raise SessionNotFoundError(f"session {session_id} was not found")
        if db.get(SessionPersona, (session_id, data.persona_id)) is None:
            raise TurnPersonaNotFoundError("persona is not assigned to this session")
        turn = Turn(session_id=session_id, **data.model_dump())
        db.add(turn)
        db.commit()
        db.refresh(turn)
        return turn
