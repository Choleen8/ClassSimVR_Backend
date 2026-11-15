from sqlalchemy import inspect

from classsimvr.models import Persona, SessionPersona


def test_persona_has_reusable_learner_configuration_fields():
    columns = {column.name for column in inspect(Persona).columns}

    assert {
        "persona_id",
        "name",
        "language",
        "academic_baseline",
        "avatar_ref",
        "personality_traits",
        "behavior_triggers",
        "response_style",
        "participation_level",
        "is_active",
    } <= columns


def test_session_persona_has_mutable_runtime_state_fields():
    columns = {column.name for column in inspect(SessionPersona).columns}

    assert {
        "session_id",
        "persona_id",
        "initial_state",
        "current_state",
        "current_emotion",
        "state_summary",
        "state_data",
        "is_active",
    } <= columns
