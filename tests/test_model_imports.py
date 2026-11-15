from classsimvr.models import (
    Base,
    Bti,
    Consent,
    Evidence,
    IndicatorScore,
    NotObserved,
    ObservedScore,
    Persona,
    Recording,
    Report,
    Session,
    SessionBti,
    SessionPersona,
    Trainee,
    Turn,
)


def test_all_eerd_models_are_exported_and_registered():
    models = {
        Trainee,
        Session,
        Persona,
        Bti,
        Consent,
        Recording,
        Report,
        Turn,
        SessionPersona,
        SessionBti,
        IndicatorScore,
        ObservedScore,
        NotObserved,
        Evidence,
    }
    assert models <= {mapper.class_ for mapper in Base.registry.mappers}
    assert {model.__tablename__ for model in models} <= set(Base.metadata.tables)


def test_existing_model_module_paths_reexport_orm_classes():
    from classsimvr.models.Persona import Persona as LegacyPersona
    from classsimvr.models.Session import Session as LegacySession
    from classsimvr.models.Trainee import Trainee as LegacyTrainee
    from classsimvr.models.Turn import Turn as LegacyTurn

    assert (LegacyPersona, LegacySession, LegacyTrainee, LegacyTurn) == (
        Persona,
        Session,
        Trainee,
        Turn,
    )
