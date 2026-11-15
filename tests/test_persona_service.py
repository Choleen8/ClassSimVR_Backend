import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession

from classsimvr.models import Base, Session, Trainee
from classsimvr.services.persona_contracts import (
    PersonaCreate,
    SessionPersonaAssign,
    SessionPersonaStateUpdate,
)


def test_runtime_state_rejects_non_json_values_and_excessive_nesting():
    with pytest.raises(ValidationError):
        SessionPersonaAssign(persona_id=1, state_data={"value": object()})
    with pytest.raises(ValidationError):
        SessionPersonaAssign(persona_id=1, state_data={"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}})
from classsimvr.services.personas import (
    AssignmentExistsError,
    PersonaNotFoundError,
    PersonaService,
    SessionNotFoundError,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with DbSession(engine) as session:
        yield session


def persona_data() -> PersonaCreate:
    return PersonaCreate(
        name="Ana",
        language="English",
        academic_baseline="strong in mathematics",
        avatar_ref="avatar-ana",
        personality_traits=["curious", "shy"],
        behavior_triggers=["asks for examples"],
        response_style="short and careful",
        participation_level="medium",
    )


def create_session(db: DbSession) -> Session:
    trainee = Trainee(full_name="Teacher One", email="teacher@example.com")
    db.add(trainee)
    db.flush()
    teaching_session = Session(trainee_id=trainee.trainee_id)
    db.add(teaching_session)
    db.commit()
    db.refresh(teaching_session)
    return teaching_session


def test_persona_service_creates_and_lists_reusable_personas(db):
    service = PersonaService()

    created = service.create_persona(db, persona_data())
    personas = service.list_personas(db)

    assert created.name == "Ana"
    assert created.personality_traits == ["curious", "shy"]
    assert [persona.persona_id for persona in personas] == [created.persona_id]


def test_persona_service_lists_distinct_personas_for_a_trainee(db):
    service = PersonaService()
    persona = service.create_persona(db, persona_data())
    second_persona = service.create_persona(
        db, PersonaCreate(name="Ben", language="English")
    )
    teaching_session = create_session(db)
    second_session = Session(trainee_id=teaching_session.trainee_id)
    db.add(second_session)
    db.commit()
    service.assign_persona(
        db,
        teaching_session.session_id,
        SessionPersonaAssign(persona_id=persona.persona_id),
    )
    service.assign_persona(
        db,
        second_session.session_id,
        SessionPersonaAssign(persona_id=persona.persona_id),
    )
    service.assign_persona(
        db,
        second_session.session_id,
        SessionPersonaAssign(persona_id=second_persona.persona_id),
    )

    personas = service.list_personas_for_trainee(db, teaching_session.trainee_id)

    assert [item.name for item in personas] == ["Ana", "Ben"]


def test_persona_service_assigns_and_updates_session_state_without_mutating_profile(db):
    service = PersonaService()
    persona = service.create_persona(db, persona_data())
    teaching_session = create_session(db)

    assigned = service.assign_persona(
        db,
        teaching_session.session_id,
        SessionPersonaAssign(persona_id=persona.persona_id, initial_state="confused"),
    )
    updated = service.update_session_state(
        db,
        teaching_session.session_id,
        persona.persona_id,
        SessionPersonaStateUpdate(
            current_state="engaged",
            current_emotion="confident",
            state_summary="Understood the fraction example.",
        ),
    )

    assert assigned.initial_state == "confused"
    assert updated.current_state == "engaged"
    assert db.get(type(persona), persona.persona_id).personality_traits == ["curious", "shy"]


def test_persona_service_rejects_duplicate_and_missing_assignments(db):
    service = PersonaService()
    persona = service.create_persona(db, persona_data())
    teaching_session = create_session(db)
    assignment = SessionPersonaAssign(persona_id=persona.persona_id)
    service.assign_persona(db, teaching_session.session_id, assignment)

    with pytest.raises(AssignmentExistsError):
        service.assign_persona(db, teaching_session.session_id, assignment)
    with pytest.raises(PersonaNotFoundError):
        service.assign_persona(db, teaching_session.session_id, SessionPersonaAssign(persona_id=999))
    with pytest.raises(SessionNotFoundError):
        service.assign_persona(db, 999, assignment)
