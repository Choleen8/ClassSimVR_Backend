import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession

from classsimvr.models import Base, Session, Trainee
from classsimvr.services.personas import SessionNotFoundError
from classsimvr.services.sessions import SessionLifecycleError, SessionService


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with DbSession(engine) as session:
        yield session


def create_session(db: DbSession) -> Session:
    trainee = Trainee(full_name="Teacher One", email="teacher@example.com")
    db.add(trainee)
    db.flush()
    session = Session(trainee_id=trainee.trainee_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_session_lifecycle_transitions_are_explicit(db):
    session = create_session(db)
    service = SessionService()

    assert service.get_session(db, session.session_id).status == "created"
    assert service.start_session(db, session.session_id).status == "running"
    assert service.pause_session(db, session.session_id).status == "paused"
    assert service.resume_session(db, session.session_id).status == "running"
    completed = service.complete_session(db, session.session_id)

    assert completed.status == "completed"
    assert completed.started_at is not None
    assert completed.completed_at is not None


def test_session_lifecycle_rejects_invalid_transition(db):
    session = create_session(db)
    service = SessionService()

    with pytest.raises(SessionLifecycleError):
        service.pause_session(db, session.session_id)
    with pytest.raises(SessionLifecycleError):
        service.complete_session(db, session.session_id)


def test_session_lifecycle_rejects_unknown_session(db):
    with pytest.raises(SessionNotFoundError):
        SessionService().start_session(db, 999)


def test_reset_removes_session_only_students_and_returns_session_to_created(db):
    session = create_session(db)
    service = SessionService()
    service.generate_students(db, session.session_id, 15, seed=4)
    service.start_session(db, session.session_id)

    reset = service.reset_session(db, session.session_id)

    assert reset.status == "created"
    assert reset.class_size is None
    assert reset.session_personas == []
    assert reset.generated_personas == []
