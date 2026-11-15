from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ..models import Persona, Session, SessionPersona, Trainee
from .persona_contracts import PersonaCreate, SessionPersonaAssign, SessionPersonaStateUpdate


class PersonaNotFoundError(LookupError):
    pass


class SessionNotFoundError(LookupError):
    pass


class TraineeNotFoundError(LookupError):
    pass


class SessionPersonaNotFoundError(LookupError):
    pass


class AssignmentExistsError(ValueError):
    pass


class PersonaUnavailableError(LookupError):
    pass


class PersonaService:
    def create_persona(self, db: DbSession, data: PersonaCreate) -> Persona:
        persona = Persona(**data.model_dump())
        db.add(persona)
        db.commit()
        db.refresh(persona)
        return persona

    def list_personas(self, db: DbSession) -> list[Persona]:
        return list(db.scalars(select(Persona).order_by(Persona.persona_id)).all())

    def list_personas_for_trainee(
        self, db: DbSession, trainee_id: int
    ) -> list[Persona]:
        if db.get(Trainee, trainee_id) is None:
            raise TraineeNotFoundError(f"trainee {trainee_id} was not found")
        statement = (
            select(Persona)
            .join(SessionPersona, SessionPersona.persona_id == Persona.persona_id)
            .join(Session, Session.session_id == SessionPersona.session_id)
            .where(Session.trainee_id == trainee_id)
            .distinct()
            .order_by(Persona.persona_id)
        )
        return list(db.scalars(statement).all())

    def assign_persona(
        self, db: DbSession, session_id: int, data: SessionPersonaAssign
    ) -> SessionPersona:
        if db.get(Session, session_id) is None:
            raise SessionNotFoundError(f"session {session_id} was not found")
        if db.get(Persona, data.persona_id) is None:
            raise PersonaNotFoundError(f"persona {data.persona_id} was not found")
        if db.get(SessionPersona, (session_id, data.persona_id)) is not None:
            raise AssignmentExistsError("persona is already assigned to this session")

        assignment = SessionPersona(
            session_id=session_id,
            current_state=data.initial_state,
            **data.model_dump(),
        )
        db.add(assignment)
        try:
            db.commit()
        except IntegrityError as error:
            db.rollback()
            message = str(error.orig).lower()
            if "session_persona" in message and (
                "unique" in message or "duplicate" in message or "primary key" in message
            ):
                raise AssignmentExistsError(
                    "persona is already assigned to this session"
                ) from error
            raise
        db.refresh(assignment)
        return assignment

    def list_assigned_personas(
        self, db: DbSession, session_id: int
    ) -> list[SessionPersona]:
        if db.get(Session, session_id) is None:
            raise SessionNotFoundError(f"session {session_id} was not found")
        return list(
            db.scalars(
                select(SessionPersona)
                .where(SessionPersona.session_id == session_id)
                .order_by(SessionPersona.persona_id)
            ).all()
        )

    def get_assigned_persona(
        self, db: DbSession, session_id: int, persona_id: int
    ) -> SessionPersona:
        assignment = db.get(SessionPersona, (session_id, persona_id))
        if assignment is None:
            raise SessionPersonaNotFoundError(
                "persona is not assigned to this session"
            )
        if not assignment.is_active or not assignment.persona.is_active:
            raise PersonaUnavailableError("persona is not active for this session")
        return assignment

    def update_session_state(
        self,
        db: DbSession,
        session_id: int,
        persona_id: int,
        data: SessionPersonaStateUpdate,
    ) -> SessionPersona:
        assignment = db.get(SessionPersona, (session_id, persona_id))
        if assignment is None:
            raise SessionPersonaNotFoundError("persona is not assigned to this session")
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(assignment, key, value)
        db.commit()
        db.refresh(assignment)
        return assignment
