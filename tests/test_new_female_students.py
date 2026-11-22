"""Registration and shared behavior regressions for Eva, Ara, and Kate."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from classsimvr.routes.students import StudentRegistrationBody, register_student
from classsimvr.services.academic_ability import (
    AcademicAbilityController, AcademicDecision, Difficulty, SelectedBehavior,
)
from classsimvr.services.behavior import BehaviorService
from classsimvr.services.contracts import BehaviorResponse
from classsimvr.services.microphone_commands import MicrophoneCommandRouter
from classsimvr.services.speech_to_text import SpeechToTextService
from classsimvr.services.student_registry import StudentRegistry
from classsimvr.services.tts import TextToSpeechService


STUDENTS = [
    ("eva-mendoza", "Eva Mendoza", "ADVANCED", "en-US-EmmaNeural"),
    ("ara-villanueva", "Ara Villanueva", "INTERMEDIATE", "en-US-AvaNeural"),
    ("kate-navarro", "Kate Navarro", "BEGINNER", "en-US-MichelleNeural"),
]


class RecordingTTS:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, stem, **kwargs):
        self.calls.append((text, kwargs))
        return f"http://localhost/audio/{kwargs['persona_id']}.mp3"


@pytest.mark.parametrize("student_id,name,ability,voice", STUDENTS)
def test_profile_identity_aliases_abilities_and_independent_memory(student_id, name, ability, voice):
    registry = StudentRegistry.with_defaults()
    student = registry.get(student_id)
    assert (student.name, student.gender, student.knowledge_level, student.voice) == (
        name, "female", ability, voice,
    )
    assert student.academic_behavior_enabled
    assert {"NONE", "STAND", "SIT", "RAISE_HAND", "LOWER_HAND", "TALK", "THINKING", "IDK"} <= set(student.supported_actions)
    assert not hasattr(student, "grade_level")
    assert all(other.voice != voice for other in registry.list_students() if other.student_id != student_id)
    for alias in (name, name.split()[0], name.upper(), f" {name.split()[0]}! "):
        assert registry.resolve(alias).student_id == student_id
    registry.set_selected_grade_level("new-class", "Grade 8")
    assert registry.get_selected_grade_level("new-class") == "Grade 8"
    registry.record_conversation_turn("new-class", student_id, "What is a computer?", "An electronic device.")
    assert registry.get_conversation_memory("new-class", student_id)["first_teacher_question"] == "What is a computer?"
    assert not registry.get_conversation_memory("other-class", student_id)["recent_turns"]
    for other in registry.list_students():
        if other.student_id != student_id:
            assert not registry.get_conversation_memory("new-class", other.student_id)["recent_turns"]


@pytest.mark.parametrize("student_id,name,ability,voice", STUDENTS)
def test_first_and_full_name_acknowledgments_stand_and_sit_use_own_voice(student_id, name, ability, voice):
    tts = RecordingTTS()
    router = MicrophoneCommandRouter(behavior=BehaviorService(client=object()), tts=tts)
    for alias in (name.split()[0], name):
        for transcript, action in ((alias, "NONE"), (f"{alias}, please stand up.", "STAND"), (f"{alias}, umupo ka na.", "SIT")):
            result = router.handle(transcript, session_id="female-commands")
            assert result.target == student_id
            reply, = result.responses
            assert reply.persona_id == student_id and reply.action == action
            assert reply.speak and reply.audio_url
            assert tts.calls[-1][1]["voice"] == voice
            if action == "NONE":
                assert reply.dialogue == "Yes, Ma'am!"


@pytest.mark.parametrize("student_id,name,ability,voice", STUDENTS)
def test_private_unknown_question_uses_one_silent_thinking_then_spoken_idk(student_id, name, ability, voice):
    tts = RecordingTTS()
    router = MicrophoneCommandRouter(behavior=BehaviorService(client=object()), tts=tts)
    result = router.handle(f"{name}, what number am I thinking of right now?", session_id="female-idk")
    assert result.target == student_id
    assert [(r.action, r.speak) for r in result.responses] == [("THINKING", False), ("IDK", True)]
    thinking, final = result.responses
    assert thinking.dialogue == "" and thinking.audio_url is None
    assert final.dialogue and final.audio_url
    assert all(r.persona_id == student_id for r in result.responses)
    assert len(tts.calls) == 1 and tts.calls[0][1]["voice"] == voice


@pytest.mark.parametrize("student_id,name,ability,voice", STUDENTS)
@pytest.mark.parametrize("selection,actions", [(SelectedBehavior.ANSWER, ["NONE"]), (SelectedBehavior.THINKING, ["THINKING", "NONE"])])
def test_academic_selection_uses_own_level_and_shared_grade_without_forcing_idk(
    monkeypatch, student_id, name, ability, voice, selection, actions,
):
    models = Mock()
    models.generate_content.return_value = SimpleNamespace(text=json.dumps({
        "persona_id": student_id, "dialogue": "A fraction represents part of a whole, Ma'am.",
        "emotion": "thoughtful", "action": "NONE", "speak": True,
    }))
    controller = AcademicAbilityController()
    calls = []

    def choose(question, **kwargs):
        calls.append(kwargs)
        return AcademicDecision(Difficulty.MODERATE, selection, False, 0.5, (0.4, 0.4, 0.2))

    monkeypatch.setattr(controller, "select_behavior", choose)
    router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=SimpleNamespace(models=models), academic_controller=controller),
        tts=RecordingTTS(),
    )
    router.registry.set_selected_grade_level("female-grade", "Grade 8")
    result = router.handle(f"{name}, what is a fraction?", session_id="female-grade")
    assert len(calls) == 1
    assert calls[0]["knowledge_level"] == ability and calls[0]["grade_level"] == "Grade 8"
    assert [r.action for r in result.responses] == actions
    assert result.responses[-1].speak and result.responses[-1].audio_url
    models.generate_content.assert_called_once()


def test_new_students_raise_and_lower_hands_through_shared_group_flow():
    behavior = Mock()
    behavior.latency_status.return_value = {}
    behavior.decide_classroom_volunteers.return_value = {sid: "RAISE_HAND" for sid, *_ in STUDENTS}
    router = MicrophoneCommandRouter(behavior=behavior, tts=RecordingTTS())
    raised = router.handle("Who wants to answer?", session_id="female-volunteers")
    assert [(r.persona_id, r.action, r.speak) for r in raised.responses] == [(sid, "RAISE_HAND", False) for sid, *_ in STUDENTS]
    behavior.generate_reply.return_value = BehaviorResponse(
        persona_id="ara-villanueva", dialogue="Yes, Ma'am!", emotion="respectful", action="NONE", speak=True,
    )
    selected = router.handle("Ara", session_id="female-volunteers")
    assert [(r.persona_id, r.action) for r in selected.responses] == [
        *((sid, "LOWER_HAND") for sid, *_ in STUDENTS), ("ara-villanueva", "NONE"),
    ]


def test_existing_profiles_do_not_opt_into_changed_academic_policy():
    registry = StudentRegistry.with_defaults()
    new_ids = {row[0] for row in STUDENTS}
    assert {s.student_id for s in registry.list_students() if s.academic_behavior_enabled} == new_ids
    assert registry.resolve("Maria").student_id == "maria-sandoval"
    assert registry.resolve("Andrea").student_id == "andrea-guinto"
    prompt = SpeechToTextService()._initial_prompt()
    assert all(name in prompt for _, name, *_ in STUDENTS)


def test_default_voice_mapping_renders_three_distinct_ready_files(tmp_path, monkeypatch):
    calls = []
    tts = TextToSpeechService(output_dir=tmp_path)

    def render(coroutine):
        params = coroutine.cr_frame.f_locals
        calls.append(params["voice"])
        params["path"].write_bytes(b"fake-mp3")
        coroutine.close()

    monkeypatch.setattr(tts, "_run_async", render)
    urls = [tts.synthesize("Yes, Ma'am!", "female-voice-test", persona_id=sid) for sid, *_ in STUDENTS]
    assert calls == [row[3] for row in STUDENTS]
    assert len(set(urls)) == 3
    assert all((tmp_path / url.rsplit("/", 1)[-1]).read_bytes() for url in urls)


def test_dynamic_registration_preserves_explicit_academic_controller_opt_in():
    registry = StudentRegistry()
    register_student(StudentRegistrationBody(
        student_id="future-advanced", name="Future Student", knowledge_level="ADVANCED",
        academic_behavior_enabled=True,
    ), registry)
    assert registry.get("future-advanced").academic_behavior_enabled


def test_profile_json_preserves_academic_opt_in_and_grade_is_shared():
    registry = StudentRegistry()
    registry.load_json(json.dumps([{
        "student_id": "future-beginner", "name": "Future Learner",
        "knowledge_level": "BEGINNER", "academic_behavior_enabled": True,
    }]))
    assert registry.get("future-beginner").academic_behavior_enabled
    assert registry.get_selected_grade_level("new-session") is None


def test_multi_student_spoken_responses_keep_generation_and_poll_order():
    from classsimvr.routes.microphone import ListeningCoordinator

    defaults = StudentRegistry.with_defaults()
    registry = StudentRegistry(tuple(defaults.get(sid) for sid, *_ in STUDENTS))
    behavior = Mock()
    behavior.latency_status.return_value = {}
    behavior.generate_reply.side_effect = lambda request: BehaviorResponse(
        persona_id=request.persona_id, dialogue=f"I am {request.persona_name}, Ma'am.",
        emotion="respectful", action="NONE", speak=True,
    )
    tts = RecordingTTS()
    coordinator = ListeningCoordinator()
    coordinator.router = MicrophoneCommandRouter(behavior=behavior, tts=tts, registry=registry)
    try:
        coordinator._on_transcription("Everyone, please introduce yourselves.")
        item, = coordinator.poll("classroom", limit=10, drain=True)
        assert [r.persona_id for r in item.responses] == [row[0] for row in STUDENTS]
        assert [kwargs["persona_id"] for _, kwargs in tts.calls] == [row[0] for row in STUDENTS]
        assert all(r.speak and r.audio_url for r in item.responses)
        assert coordinator.poll("classroom", limit=10, drain=True) == []
    finally:
        coordinator.shutdown()


@pytest.mark.parametrize("student_id,name,ability,voice", STUDENTS)
def test_real_ability_weights_allow_answers_and_uncertainty_without_forcing_idk(student_id, name, ability, voice):
    controller = AcademicAbilityController()

    def decisions(question):
        return {controller.select_behavior(
            question, session_id=f"sample-{index}", persona_id=student_id,
            knowledge_level=ability, grade_level=None,
        ).behavior for index in range(200)}

    assert SelectedBehavior.ANSWER in decisions("What is five plus five?")
    assert {SelectedBehavior.THINKING, SelectedBehavior.IDK} <= decisions("Explain quantum entanglement.")
    args = dict(session_id="consistent-class", persona_id=student_id, knowledge_level=ability, grade_level=None)
    assert controller.select_behavior("What is a computer?", **args) == controller.select_behavior("What is a computer?", **args)
