from sqlalchemy import inspect

from classsimvr.models import Bti, Persona, Session, Trainee


def test_core_tables_have_eerd_names_and_primary_keys():
    assert {Trainee.__tablename__, Session.__tablename__, Persona.__tablename__, Bti.__tablename__} == {
        "trainee",
        "session",
        "persona",
        "bti",
    }
    assert inspect(Trainee).primary_key[0].name == "trainee_id"
    assert inspect(Session).primary_key[0].name == "session_id"
    assert inspect(Persona).primary_key[0].name == "persona_id"
    assert inspect(Bti).primary_key[0].name == "bti_id"


def test_session_references_trainee_and_exposes_relationship_collections():
    session_columns = {column.name for column in inspect(Session).columns}
    assert "trainee_id" in session_columns
    foreign_keys = {foreign_key.target_fullname for foreign_key in inspect(Session).columns.trainee_id.foreign_keys}
    assert foreign_keys == {"trainee.trainee_id"}
    assert {relationship.key for relationship in inspect(Trainee).relationships} == {
        "sessions", "auth_account"
    }
    assert {relationship.key for relationship in inspect(Session).relationships} >= {"trainee"}


def test_reference_tables_expose_their_declared_columns():
    assert {
        "persona_id",
        "name",
        "language",
        "academic_baseline",
    } <= {column.name for column in inspect(Persona).columns}
    assert {column.name for column in inspect(Bti).columns} == {
        "bti_id",
        "strand",
        "descriptor",
    }
