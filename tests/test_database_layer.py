from sqlalchemy.orm import Session as OrmSession

from classsimvr.database import DATABASE_URL, SessionLocal, engine, get_session
from classsimvr.models.base import Base


def test_base_exposes_metadata_without_connecting():
    assert Base.metadata is not None


def test_database_defaults_to_postgresql_and_builds_session_factory():
    assert DATABASE_URL.startswith("postgresql+psycopg://")
    assert engine.url.get_backend_name() == "postgresql"
    assert isinstance(SessionLocal(), OrmSession)


def test_get_session_is_a_generator_dependency():
    dependency = get_session()
    assert hasattr(dependency, "__next__")
    dependency.close()
