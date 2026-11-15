from sqlalchemy import inspect

from classsimvr.models import Evidence, IndicatorScore, NotObserved, ObservedScore, Report


def test_indicator_score_references_report_and_bti():
    foreign_keys = {
        fk.target_fullname
        for column in inspect(IndicatorScore).columns
        for fk in column.foreign_keys
    }
    assert foreign_keys == {"report.session_id", "bti.bti_id"}
    assert {relationship.key for relationship in inspect(Report).relationships} >= {
        "indicator_scores"
    }


def test_score_subtypes_use_joined_table_inheritance_and_discriminator():
    mapper = inspect(IndicatorScore)
    assert mapper.polymorphic_on.name == "score_type"
    assert inspect(ObservedScore).inherits is mapper
    assert inspect(NotObserved).inherits is mapper
    assert inspect(ObservedScore).polymorphic_identity == "observed"
    assert inspect(NotObserved).polymorphic_identity == "not_observed"


def test_evidence_references_observed_score():
    foreign_keys = {
        fk.target_fullname
        for column in inspect(Evidence).columns
        for fk in column.foreign_keys
    }
    assert foreign_keys == {"observed_score.score_id"}
