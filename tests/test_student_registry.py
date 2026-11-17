from __future__ import annotations

import json

import pytest

from classsimvr.services.student_registry import (
    DuplicateStudentError,
    StudentProfile,
    StudentRegistry,
)


def make_student(index: int) -> StudentProfile:
    return StudentProfile(
        student_id=f"student-{index}",
        name=f"Student {index}",
        aliases=(f"learner {index}", f"student {index}"),
        personality=f"Student {index} is curious and cooperative.",
        voice="fil-PH-BlessicaNeural" if index % 2 else "fil-PH-AngeloNeural",
    )


def test_registry_registers_unique_profiles_and_resolves_aliases():
    registry = StudentRegistry()
    profile = make_student(1)
    registry.register(profile)

    assert registry.resolve("Please ask learner 1 to answer").student_id == profile.student_id
    with pytest.raises(DuplicateStudentError):
        registry.register(profile)


def test_default_registry_includes_ethan_luna_with_shared_male_voice_and_capabilities():
    registry = StudentRegistry.with_defaults()

    ethan = registry.get("ethan-luna")

    assert ethan.name == "Ethan Luna"
    assert {registry.resolve(alias).student_id for alias in ("Ethan", "Ethan Luna")} == {"ethan-luna"}
    assert ethan.voice == registry.get("robert-eco").voice
    assert ethan.knowledge_level == "INTERMEDIATE"
    assert ethan.confidence == 0.6
    assert ethan.willingness == 0.6
    assert "THINKING" in ethan.supported_actions
    assert "IDK" in ethan.supported_actions
    assert "THINKING" not in registry.get("maria-sandoval").supported_actions
    assert registry.get("maria-sandoval").knowledge_level == "ADVANCED"
    assert registry.get("robert-eco").knowledge_level == "ADVANCED"


def test_registry_normalizes_configured_knowledge_levels():
    registry = StudentRegistry()
    registry.register(StudentProfile(
        student_id="student-1",
        name="Student One",
        knowledge_level="beginner",
    ))
    registry.register(StudentProfile(
        student_id="student-2",
        name="Student Two",
        knowledge_level="not-a-level",
    ))

    assert registry.get("student-1").knowledge_level == "BEGINNER"
    assert registry.get("student-2").knowledge_level == "INTERMEDIATE"


def test_registry_loads_configuration_and_keeps_per_session_state():
    registry = StudentRegistry()
    registry.load_json(
        json.dumps(
            [
                {
                    "student_id": "configured-1",
                    "name": "Configured One",
                    "aliases": ["one"],
                    "personality": "patient",
                    "voice": "fil-PH-AngeloNeural",
                }
            ]
        )
    )

    registry.update_session_state("session-1", "configured-1", emotion="happy", summary="answered")
    state = registry.get_session_state("session-1", "configured-1")
    assert state.emotion == "happy"
    assert state.summary == "answered"


def test_registry_rejects_duplicate_aliases():
    registry = StudentRegistry()
    registry.register(make_student(1))
    with pytest.raises(DuplicateStudentError):
        registry.register(
            StudentProfile(
                student_id="student-2",
                name="Other",
                aliases=("learner 1",),
                personality="quiet",
            )
        )


def test_registry_resolves_registered_name_anywhere_with_natural_punctuation():
    registry = StudentRegistry()
    profile = make_student(3)
    registry.register(profile)

    for text in (
        "Student 3, please stand",
        "Please stand up, student 3.",
        "Pwede bang tumayo ka, learner 3?",
        "Itaas ang kamay ni Student 3!",
    ):
        assert registry.resolve(text).student_id == profile.student_id


def test_default_robert_profile_uses_canonical_actor_persona_id_and_all_aliases():
    registry = StudentRegistry.with_defaults()
    profile = registry.get("robert-eco")

    assert profile.student_id == "robert-eco"
    assert profile.name == "Robert Eco"
    assert profile.voice == "fil-PH-AngeloNeural"
    assert {registry.resolve(alias).student_id for alias in ("Robert", "Berto", "Robert Eco", "Berto Eco")} == {"robert-eco"}


def test_legacy_berto_id_is_migrated_without_creating_duplicate_student():
    registry = StudentRegistry.with_defaults()
    loaded = registry.load_json(
        json.dumps(
            [
                {
                    "student_id": "berto-eco",
                    "name": "Robert Eco",
                    "aliases": ["robert", "berto"],
                    "personality": "Robert is a thoughtful Filipino VR student.",
                    "voice": "fil-PH-AngeloNeural",
                }
            ]
        ),
        replace=False,
    )

    assert [profile.student_id for profile in loaded] == ["robert-eco"]
    assert [profile.student_id for profile in registry.list_students()].count("robert-eco") == 1
    assert len(registry.list_students()) == 3


def test_registry_keeps_chronological_conversation_memory_per_student_and_session():
    registry = StudentRegistry()
    registry.register(make_student(4))

    registry.record_conversation_turn(
        "classroom", "student-4", "Introduce yourself", "I am Student 4."
    )
    registry.record_conversation_turn(
        "classroom", "student-4", "What is a computer?", "It processes information."
    )
    registry.record_conversation_turn(
        "other-session", "student-4", "What is 2 plus 2?", "Four."
    )

    memory = registry.get_conversation_memory("classroom", "student-4")
    assert memory["first_teacher_question"] == "Introduce yourself"
    assert memory["recent_turns"] == [
        {"speaker": "teacher", "text": "Introduce yourself"},
        {"speaker": "student", "text": "I am Student 4."},
        {"speaker": "teacher", "text": "What is a computer?"},
        {"speaker": "student", "text": "It processes information."},
    ]
    assert registry.get_conversation_memory("other-session", "student-4")["first_teacher_question"] == "What is 2 plus 2?"


def test_registry_reset_clears_only_requested_session_memory():
    registry = StudentRegistry()
    registry.register(make_student(5))
    registry.record_conversation_turn("classroom", "student-5", "First", "Answer")
    registry.record_conversation_turn("other", "student-5", "Other", "Answer")

    registry.reset_session("classroom")

    assert registry.get_conversation_memory("classroom", "student-5")["recent_turns"] == []
    assert registry.get_conversation_memory("other", "student-5")["recent_turns"]


def test_registry_matches_speech_transcription_punctuation_variants():
    registry = StudentRegistry()
    profile = StudentProfile(
        student_id="jean-pierre",
        name="Jean-Pierre O'Neil",
        aliases=("jp",),
    )
    registry.register(profile)

    assert registry.resolve("Please stand, Jean-Pierre O'Neil").student_id == profile.student_id
    assert registry.resolve("Please stand Jean Pierre O Neil").student_id == profile.student_id


def test_registry_matches_accented_spoken_name_to_unaccented_profile():
    registry = StudentRegistry()
    profile = StudentProfile(student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",))
    registry.register(profile)

    assert registry.resolve("María, introduce yourself").student_id == profile.student_id
