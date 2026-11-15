import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession

from classsimvr.models import Base, Session, Trainee
from classsimvr.services.session_contracts import StudentGenerationRequest
from classsimvr.services.sessions import (
    ClassAlreadyGeneratedError,
    SessionService,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with DbSession(engine) as session:
        yield session


def create_session(db: DbSession, email: str = "teacher@example.com") -> Session:
    trainee = Trainee(full_name="Teacher One", email=email)
    db.add(trainee)
    db.flush()
    session = Session(trainee_id=trainee.trainee_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_student_generation_requires_15_to_30_students():
    assert StudentGenerationRequest(class_size=15).class_size == 15
    assert StudentGenerationRequest(class_size=30).class_size == 30
    with pytest.raises(ValidationError):
        StudentGenerationRequest(class_size=14)
    with pytest.raises(ValidationError):
        StudentGenerationRequest(class_size=31)


def test_session_service_generates_and_assigns_session_only_students(db):
    session = create_session(db)

    generated = SessionService().generate_students(db, session.session_id, 15, seed=7)

    assert generated.class_size == 15
    assert generated.seed == 7
    assert len(generated.assignments) == 15
    assert len({assignment.persona_id for assignment in generated.assignments}) == 15
    assert all(
        assignment.persona.generated_for_session_id == session.session_id
        for assignment in generated.assignments
    )
    assert db.get(Session, session.session_id).class_size == 15


def test_session_generation_is_deterministic_and_isolated(db):
    first = create_session(db, "teacher-one@example.com")
    second = create_session(db, "teacher-two@example.com")
    service = SessionService()

    first_roster = service.generate_students(db, first.session_id, 15, seed=99)
    second_roster = service.generate_students(db, second.session_id, 15, seed=100)

    assert [item.persona.name for item in first_roster.assignments] != [
        item.persona.name for item in second_roster.assignments
    ]
    assert all(
        item.persona.generated_for_session_id == second.session_id
        for item in second_roster.assignments
    )


def test_session_generation_rejects_duplicate_generation(db):
    session = create_session(db)
    service = SessionService()
    service.generate_students(db, session.session_id, 15, seed=1)

    with pytest.raises(ClassAlreadyGeneratedError):
        service.generate_students(db, session.session_id, 15, seed=2)
