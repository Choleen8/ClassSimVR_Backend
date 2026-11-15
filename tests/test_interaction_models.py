from sqlalchemy import inspect

from classsimvr.models import (
    Consent,
    Recording,
    Report,
    Session,
    SessionBti,
    SessionPersona,
    Turn,
)


def test_session_owned_weak_entities_use_session_id_as_pk_and_fk():
    for model in (Consent, Recording, Report):
        mapper = inspect(model)
        assert [column.name for column in mapper.primary_key] == ["session_id"]
        assert {fk.target_fullname for fk in mapper.columns.session_id.foreign_keys} == {
            "session.session_id"
        }


def test_junction_entities_have_composite_primary_keys():
    assert [column.name for column in inspect(SessionPersona).primary_key] == [
        "session_id",
        "persona_id",
    ]
    assert [column.name for column in inspect(SessionBti).primary_key] == [
        "session_id",
        "bti_id",
    ]


def test_turn_references_session_and_persona():
    foreign_keys = {
        fk.target_fullname
        for column in inspect(Turn).columns
        for fk in column.foreign_keys
    }
    assert foreign_keys == {"session.session_id", "persona.persona_id"}
    assert {relationship.key for relationship in inspect(Session).relationships} >= {
        "trainee",
        "turns",
        "personas",
        "btis",
    }
