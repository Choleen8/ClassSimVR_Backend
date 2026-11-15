from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import random

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..models import Persona, Session, SessionPersona, Turn, Trainee
from .persona_contracts import SessionPersonaAssign
from .personas import PersonaService, SessionNotFoundError, TraineeNotFoundError
from .session_contracts import SessionCreate


class ClassAlreadyGeneratedError(ValueError):
    pass


class SessionLifecycleError(ValueError):
    pass


@dataclass(frozen=True)
class GeneratedClass:
    session_id: int
    class_size: int
    seed: int
    assignments: list[SessionPersona]


_NAMES = (
    "Althea", "Benedict", "Camille", "Dario", "Elise", "Franco", "Gabriela",
    "Hannah", "Ian", "Jasmine", "Kurt", "Leah", "Marco", "Nina", "Oliver",
    "Patricia", "Quinn", "Rafael", "Sofia", "Tomas", "Uriel", "Valerie",
    "Warren", "Xandra", "Yvette", "Zion", "Aira", "Bryan", "Chloe", "Denzel",
    "Erika", "Felix", "Grace", "Harvey", "Ivy", "Joshua", "Kiana", "Luis",
    "Mika", "Noah",
)
_LANGUAGES = ("English", "Filipino", "Taglish")
_BASELINES = (
    "needs concrete examples",
    "strong verbal recall",
    "developing reading confidence",
    "quick with visual patterns",
    "needs additional processing time",
)
_TRAITS = ("curious", "shy", "outspoken", "cooperative", "analytical", "playful")
_TRIGGERS = (
    "asks for examples",
    "responds when called on",
    "needs redirection",
    "helps a seatmate",
    "asks clarification questions",
)
_STATES = ("engaged", "confused", "disengaged", "disruptive")


class SessionService:
    def create_session(self, db: DbSession, data: SessionCreate) -> Session:
        if db.get(Trainee, data.trainee_id) is None:
            raise TraineeNotFoundError(f"trainee {data.trainee_id} was not found")
        session = Session(**data.model_dump())
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    def get_session(self, db: DbSession, session_id: int) -> Session:
        session = db.get(Session, session_id)
        if session is None:
            raise SessionNotFoundError(f"session {session_id} was not found")
        return session

    def _transition(
        self,
        db: DbSession,
        session_id: int,
        expected: set[str],
        target: str,
    ) -> Session:
        session = self.get_session(db, session_id)
        if session.status not in expected:
            raise SessionLifecycleError(
                f"cannot transition session from {session.status} to {target}"
            )
        now = datetime.now(timezone.utc)
        session.status = target
        if target == "running" and session.started_at is None:
            session.started_at = now
        if target == "paused":
            session.paused_at = now
        if target == "completed":
            session.completed_at = now
        db.commit()
        db.refresh(session)
        return session

    def start_session(self, db: DbSession, session_id: int) -> Session:
        return self._transition(db, session_id, {"created"}, "running")

    def pause_session(self, db: DbSession, session_id: int) -> Session:
        return self._transition(db, session_id, {"running"}, "paused")

    def resume_session(self, db: DbSession, session_id: int) -> Session:
        return self._transition(db, session_id, {"paused"}, "running")

    def complete_session(self, db: DbSession, session_id: int) -> Session:
        return self._transition(db, session_id, {"running", "paused"}, "completed")

    def reset_session(self, db: DbSession, session_id: int) -> Session:
        session = self.get_session(db, session_id)
        generated = list(
            db.scalars(
                select(Persona).where(
                    Persona.generated_for_session_id == session_id
                )
            ).all()
        )
        if generated and db.scalars(
            select(Turn).where(
                Turn.session_id == session_id,
                Turn.persona_id.in_([persona.persona_id for persona in generated]),
            )
        ).first() is not None:
            raise SessionLifecycleError(
                "cannot reset a session with recorded generated-student turns"
            )
        for assignment in list(
            db.scalars(
                select(SessionPersona).where(SessionPersona.session_id == session_id)
            ).all()
        ):
            if assignment.persona_id in {persona.persona_id for persona in generated}:
                db.delete(assignment)
        db.flush()
        for persona in generated:
            db.delete(persona)
        session.status = "created"
        session.class_size = None
        session.started_at = None
        session.paused_at = None
        session.completed_at = None
        db.commit()
        db.refresh(session)
        return session

    def generate_students(
        self, db: DbSession, session_id: int, class_size: int, seed: int | None = None
    ) -> GeneratedClass:
        session = db.get(Session, session_id)
        if session is None:
            raise SessionNotFoundError(f"session {session_id} was not found")
        if list(
            db.scalars(
                select(SessionPersona).where(SessionPersona.session_id == session_id)
            ).all()
        ):
            raise ClassAlreadyGeneratedError(
                "students are already assigned to this session"
            )

        actual_seed = seed if seed is not None else random.SystemRandom().randrange(2**31)
        generator = random.Random(actual_seed)
        names = list(_NAMES)
        generator.shuffle(names)
        assignments: list[SessionPersona] = []
        for index in range(class_size):
            persona = Persona(
                name=f"{names[index]} {index + 1:02d}",
                language=generator.choice(_LANGUAGES),
                academic_baseline=generator.choice(_BASELINES),
                personality_traits=generator.sample(_TRAITS, 2),
                behavior_triggers=[generator.choice(_TRIGGERS)],
                response_style=generator.choice(("brief", "thoughtful", "expressive")),
                participation_level=generator.choice(("low", "medium", "high")),
                generated_for_session_id=session_id,
                is_active=True,
            )
            db.add(persona)
            db.flush()
            state = generator.choice(_STATES)
            assignment = SessionPersona(
                session_id=session_id,
                **SessionPersonaAssign(
                    persona_id=persona.persona_id,
                    initial_state=state,
                ).model_dump(),
                current_state=state,
            )
            db.add(assignment)
            assignments.append(assignment)

        session.class_size = class_size
        db.commit()
        for assignment in assignments:
            db.refresh(assignment)
        return GeneratedClass(session_id, class_size, actual_seed, assignments)
