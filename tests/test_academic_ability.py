from classsimvr.services.academic_ability import (
    AcademicAbilityController,
    AcademicAbilityConfig,
    Difficulty,
    SelectedBehavior,
)
import pytest


def test_classifier_handles_structural_difficulty_and_private_information():
    controller = AcademicAbilityController()

    assert controller.classify("What is 5 plus 5?") == Difficulty.EASY
    assert controller.classify("Why do seasons change and how does sunlight reach Earth?") == Difficulty.MODERATE
    assert controller.classify("Compare the causes and consequences, then evaluate which factor mattered most.") == Difficulty.HARD
    assert controller.classify("What number am I thinking of right now?") == Difficulty.VERY_HARD


def test_intermediate_behavior_is_deterministic_for_a_session_and_question():
    controller = AcademicAbilityController()
    first = controller.select_behavior(
        "Ethan, explain why seasons change.",
        session_id="classroom-1",
        persona_id="ethan-luna",
        knowledge_level="INTERMEDIATE",
        grade_level=None,
    )
    second = controller.select_behavior(
        "Ethan, explain why seasons change.",
        session_id="classroom-1",
        persona_id="ethan-luna",
        knowledge_level="INTERMEDIATE",
        grade_level=None,
    )

    assert first == second
    assert first.difficulty == Difficulty.MODERATE


def test_private_information_always_selects_idk():
    controller = AcademicAbilityController()

    decision = controller.select_behavior(
        "Ethan, what is my password?",
        session_id="classroom-1",
        persona_id="ethan-luna",
        knowledge_level="INTERMEDIATE",
        grade_level=None,
    )

    assert decision.behavior == SelectedBehavior.IDK
    assert decision.is_unknowable is True


def test_probability_configuration_can_force_each_behavior(monkeypatch):
    monkeypatch.setenv("CLASSSIMVR_ACADEMIC_EASY_ANSWER", "0")
    monkeypatch.setenv("CLASSSIMVR_ACADEMIC_EASY_THINKING", "0")
    monkeypatch.setenv("CLASSSIMVR_ACADEMIC_EASY_IDK", "1")
    controller = AcademicAbilityController()

    decision = controller.select_behavior(
        "Ethan, what is 2 plus 2?",
        session_id="classroom-1",
        persona_id="ethan-luna",
        knowledge_level="INTERMEDIATE",
        grade_level=None,
    )

    assert decision.behavior == SelectedBehavior.IDK


@pytest.mark.parametrize("question, expected", [
    ("What color is the sky?", Difficulty.EASY),
    ("How does a bicycle stay balanced while moving?", Difficulty.MODERATE),
    ("Compare two possible explanations and evaluate which one is stronger.", Difficulty.HARD),
    ("Derive the integral and prove why the result must converge.", Difficulty.VERY_HARD),
])
def test_difficulty_bands_cover_questions_outside_existing_topic_lists(question, expected):
    assert AcademicAbilityController().classify(question) == expected
