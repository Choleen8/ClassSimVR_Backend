import pytest

from classsimvr.services.behavior import BehaviorService
from classsimvr.services.contracts import BehaviorResponse
from classsimvr.services.microphone_commands import MicrophoneCommandRouter
from classsimvr.services.student_registry import StudentProfile, StudentRegistry

@pytest.mark.parametrize("student_name,student_id", [
    ("Francis", "francis-reyes"),
    ("Ethan", "ethan-luna"),
    ("Judy", "judy-castillo"),
    ("Dave", "dave-santos"),
    ("Juan", "juan-cruz"),
])
def test_spoken_idk_has_one_silent_thinking_prelude(student_name, student_id):
    from unittest.mock import Mock

    behavior = Mock()
    behavior.generate_reply.return_value = BehaviorResponse(
        persona_id=student_id,
        dialogue="I'm sorry, Ma'am. I don't know the answer.",
        emotion="uncertain", action="IDK", speak=True,
    )
    behavior.latency_status.return_value = {}
    tts = Mock()
    tts.synthesize.return_value = "http://localhost/audio/uncertainty.mp3"
    router = MicrophoneCommandRouter(behavior=behavior, tts=tts)
    result = router.handle(
        f"{student_name}, derive Einstein's field equations.", session_id="sequence-idk",
    )
    assert result.target == student_id
    assert [(r.action, r.speak) for r in result.responses] == [
        ("THINKING", False), ("IDK", True),
    ]
    thinking, final = result.responses
    assert thinking.persona_id == final.persona_id == student_id
    assert thinking.dialogue == "" and thinking.audio_url is None
    assert final.dialogue == behavior.generate_reply.return_value.dialogue
    assert final.audio_url == tts.synthesize.return_value
    tts.synthesize.assert_called_once()
    assert tts.synthesize.call_args.kwargs["voice"] == router.registry.get(student_id).voice


@pytest.mark.parametrize("student_name,student_id", [
    ("Francis", "francis-reyes"), ("Ethan", "ethan-luna"),
    ("Dave", "dave-santos"), ("Juan", "juan-cruz"),
])
@pytest.mark.parametrize("selected,expected_actions", [
    ("answer", ["NONE"]),
    ("thinking", ["THINKING", "NONE"]),
    ("idk", ["THINKING", "IDK"]),
])
def test_real_ability_decision_keeps_answer_thinking_and_idk_distinct(
    student_name, student_id, selected, expected_actions,
):
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from classsimvr.services.academic_ability import AcademicAbilityConfig, AcademicAbilityController

    models = Mock()
    models.generate_content.return_value = SimpleNamespace(text=json.dumps({
        "persona_id": student_id, "dialogue": "I understand part of the topic, Ma'am.",
        "emotion": "respectful", "action": "NONE", "speak": True,
    }))
    config = AcademicAbilityConfig(**{
        f"hard_{behavior}": float(behavior == selected)
        for behavior in ("answer", "thinking", "idk")
    })
    router = MicrophoneCommandRouter(
        behavior=BehaviorService(
            client=SimpleNamespace(models=models),
            academic_controller=AcademicAbilityController(config),
        ),
        tts=FakeTTS(),
    )
    result = router.handle(
        f"{student_name}, explain quantum entanglement.", session_id="ability-sequence",
    )
    assert [r.action for r in result.responses] == expected_actions
    assert all(r.persona_id == student_id for r in result.responses)
    assert all(not r.speak and not r.dialogue and r.audio_url is None for r in result.responses[:-1])
    assert result.responses[-1].speak and result.responses[-1].audio_url
    assert models.generate_content.call_count == int(selected != "idk")
    memory = router.registry.get_conversation_memory("ability-sequence", student_id)
    assert len(memory["recent_turns"]) == 2


def test_idk_does_not_add_unsupported_thinking_for_maria_or_robert():
    from unittest.mock import Mock

    for name, student_id in (("Maria", "maria-sandoval"), ("Robert", "robert-eco")):
        behavior = Mock()
        behavior.latency_status.return_value = {}
        behavior.generate_reply.return_value = BehaviorResponse(
            persona_id=student_id, dialogue="I'm not sure, Ma'am.",
            emotion="uncertain", action="IDK", speak=True,
        )
        result = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS()).handle(
            f"{name}, explain quantum entanglement.", session_id="unsupported-thinking",
        )
        assert [r.action for r in result.responses] == ["NONE"]
class FakeBehavior:
    def __init__(self):
        self.inputs = []

    def generate_reply(self, request):
        self.inputs.append(request)
        return BehaviorResponse(
            persona_id=request.persona_id,
            dialogue="Ready.",
            emotion="neutral",
            action=(
                "STAND" if "stand" in request.trainee_input.lower()
                else "SIT" if "sit" in request.trainee_input.lower()
                else "NONE"
            ),
            speak=False,
        )


class FakeTTS:
    def synthesize(self, *args, **kwargs):
        return "http://127.0.0.1:8000/audio/test.mp3"


@pytest.mark.parametrize("alias", [
    "Juan", "Juan Cruz", "Jun", "Jun Cruz", "Juwan", "Juwan Cruz", "Huan",
    "Huan Cruz", "One Cruz",
])
def test_juan_speech_variants_route_to_one_canonical_actor(alias):
    router = MicrophoneCommandRouter(behavior=BehaviorService(client=object()), tts=FakeTTS())
    called = router.handle(alias + "!", session_id="juan-alias")
    assert called.target == "juan-cruz"
    reply, = called.responses
    assert reply.persona_id == "juan-cruz"
    assert (reply.action, reply.dialogue, reply.speak) == ("NONE", "Yes, Ma'am!", True)
    assert reply.audio_url
    seated = router.handle(f"{alias}, please sit down.", session_id="juan-alias")
    assert [(r.persona_id, r.action) for r in seated.responses] == [("juan-cruz", "SIT")]


@pytest.mark.parametrize("text", ["One.", "One plus one.", "One, please sit down.", "I spoke to Jun yesterday."])
def test_juan_number_confusions_and_incidental_mentions_do_not_target_a_student(text):
    router = MicrophoneCommandRouter(behavior=BehaviorService(client=object()), tts=FakeTTS())
    result = router.handle(text, session_id="safe-juan")
    assert result.target == "none" and result.responses == []


@pytest.mark.parametrize("name,student_id", [
    ("Dave Santos", "dave-santos"), ("Juan Cruz", "juan-cruz"),
])
def test_new_students_acknowledge_names_and_follow_individual_commands(name, student_id):
    from unittest.mock import Mock

    tts = Mock(spec=FakeTTS)
    tts.synthesize.return_value = "http://localhost/audio/test.mp3"
    router = MicrophoneCommandRouter(behavior=BehaviorService(client=object()), tts=tts)
    router.registry.set_selected_grade_level("new-students", "Grade 8")
    for alias in (name, name.split()[0]):
        for text, action in (
            (f"{alias}!", "NONE"),
            (f"{alias}, please stand up.", "STAND"),
            (f"Umupo ka na, {alias}.", "SIT"),
        ):
            result = router.handle(text, session_id="new-students")
            assert result.target == student_id
            reply, = result.responses
            assert reply.persona_id == student_id and reply.action == action
            assert reply.speak and reply.audio_url == tts.synthesize.return_value
            if action == "NONE":
                assert reply.dialogue == "Yes, Ma'am!"
            assert tts.synthesize.call_args.kwargs["voice"] == router.registry.get(student_id).voice
    assert router.registry.get_selected_grade_level("new-students") == "Grade 8"
    result = router.handle(f"{name}, explain quantum field theory.", session_id="new-students")
    assert [(r.action, r.speak) for r in result.responses] == [("THINKING", False), ("IDK", True)]


def test_dave_and_juan_volunteer_and_lower_hands_through_shared_group_routing():
    from unittest.mock import Mock

    behavior = Mock()
    behavior.latency_status.return_value = {}
    behavior.decide_classroom_volunteers.return_value = {
        "dave-santos": "RAISE_HAND", "juan-cruz": "RAISE_HAND",
    }
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())
    result = router.handle("Who wants to answer?", session_id="new-volunteers")
    assert [(r.persona_id, r.action, r.speak) for r in result.responses] == [
        ("dave-santos", "RAISE_HAND", False), ("juan-cruz", "RAISE_HAND", False),
    ]
    behavior.decide_classroom_volunteers.assert_called_once()
    behavior.generate_reply.return_value = BehaviorResponse(
        persona_id="juan-cruz", dialogue="Yes, Ma'am!", action="NONE",
        emotion="respectful", speak=True,
    )
    result = router.handle("Juan", session_id="new-volunteers")
    assert [(r.persona_id, r.action) for r in result.responses] == [
        ("dave-santos", "LOWER_HAND"), ("juan-cruz", "LOWER_HAND"), ("juan-cruz", "NONE"),
    ]


def test_routes_named_student_to_maria():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    result = router.handle("Maria, please stand up.", session_id="classroom")

    assert [item.persona_id for item in result.responses] == ["maria-sandoval"]
    assert result.responses[0].action == "STAND"
    assert behavior.inputs[0].persona_name == "Maria Sandoval"


def test_andrea_name_acknowledgement_uses_distinct_female_voice():
    class VoiceTTS(FakeTTS):
        def __init__(self):
            self.calls = []

        def synthesize(self, *args, **kwargs):
            self.calls.append(kwargs)
            return super().synthesize(*args, **kwargs)

    tts = VoiceTTS()
    router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=object()), tts=tts
    )

    result = router.handle("Andreya!", session_id="andrea-name")

    assert result.target == "andrea-guinto"
    assert result.responses[0].persona_id == "andrea-guinto"
    assert result.responses[0].dialogue == "Yes, Ma'am!"
    assert result.responses[0].speak is True
    assert tts.calls[0]["voice"] != "fil-PH-BlessicaNeural"


def test_andrea_supports_named_posture_commands():
    router = MicrophoneCommandRouter(behavior=FakeBehavior(), tts=FakeTTS())

    standing = router.handle("Andria, stand up.", session_id="andria-stand")
    seated = router.handle("Andreah, sit down.", session_id="andreah-sit")

    assert standing.target == seated.target == "andrea-guinto"
    assert standing.responses[0].action == "STAND"
    assert seated.responses[0].action == "SIT"


def test_judy_name_only_acknowledges_without_standing_and_uses_her_voice():
    class VoiceTTS(FakeTTS):
        def __init__(self):
            self.calls = []

        def synthesize(self, *args, **kwargs):
            self.calls.append(kwargs)
            return super().synthesize(*args, **kwargs)

    tts = VoiceTTS()
    router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=object()), tts=tts
    )

    result = router.handle("Judi!", session_id="judy-name")

    assert result.target == "judy-castillo"
    assert [response.action for response in result.responses] == ["NONE"]
    assert result.responses[0].dialogue == "Yes, Ma'am!"
    assert result.responses[0].speak is True
    assert tts.calls[0]["voice"] == "en-US-AriaNeural"


def test_arabic_script_judy_transcripts_acknowledge_as_judy():
    router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=object()), tts=FakeTTS()
    )

    for transcript in ("جودي", "جو دی."):
        result = router.handle(transcript, session_id=f"judy-arabic-{transcript}")
        assert result.target == "judy-castillo"
        assert result.responses[0].persona_id == "judy-castillo"
        assert result.responses[0].dialogue == "Yes, Ma'am!"
        assert result.responses[0].action == "NONE"
        assert result.responses[0].speak is True


def test_ethan_thinking_is_sequenced_before_spoken_answer_and_uses_robert_voice():
    registry = StudentRegistry.with_defaults()

    class ThinkingBehavior(FakeBehavior):
        def generate_reply(self, request):
            self.inputs.append(request)
            return BehaviorResponse(
                persona_id=request.persona_id,
                dialogue="I think the answer is related to energy.",
                emotion="thinking",
                action="THINKING",
                speak=True,
            )

    class VoiceTTS(FakeTTS):
        def __init__(self):
            self.calls = []

        def synthesize(self, *args, **kwargs):
            self.calls.append(kwargs)
            return super().synthesize(*args, **kwargs)

    tts = VoiceTTS()
    router = MicrophoneCommandRouter(
        behavior=ThinkingBehavior(), tts=tts, registry=registry
    )
    result = router.handle("Ethan, what is quantum physics?", session_id="classroom")

    assert [response.action for response in result.responses] == ["THINKING", "NONE"]
    assert result.responses[0].speak is False
    assert result.responses[1].speak is True
    assert result.responses[1].audio_url is not None
    assert tts.calls[0]["voice"] == registry.get("robert-eco").voice


def test_ethan_supports_named_stand_and_sit_routing():
    router = MicrophoneCommandRouter(behavior=FakeBehavior(), tts=FakeTTS())

    standing = router.handle("Ethan, please stand up.", session_id="classroom")
    seated = router.handle("Ethan Luna, please sit down.", session_id="classroom")

    assert standing.target == "ethan-luna"
    assert standing.responses[0].persona_id == "ethan-luna"
    assert standing.responses[0].action == "STAND"
    assert seated.target == "ethan-luna"
    assert seated.responses[0].persona_id == "ethan-luna"
    assert seated.responses[0].action == "SIT"


def test_ethan_beyond_grade_question_sequences_thinking_then_spoken_idk():
    from types import SimpleNamespace
    import json

    class BeyondGradeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(
                text=json.dumps({
                    "persona_id": "ethan-luna",
                    "dialogue": "Quantum field theory explains particles as fields.",
                    "emotion": "confident",
                    "action": "NONE",
                    "speak": True,
                })
            )

    class BeyondGradeClient:
        models = BeyondGradeModels()

    router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=BeyondGradeClient()),
        tts=FakeTTS(),
    )
    router.registry.set_selected_grade_level("classroom-grade-8", "Grade 8")

    result = router.handle(
        "Ethan, explain quantum field theory.", session_id="classroom-grade-8"
    )

    assert [response.action for response in result.responses] == ["THINKING", "IDK"]
    assert result.responses[0].speak is False
    assert result.responses[1].speak is True
    assert result.responses[1].action == "IDK"
    assert "don't know" in result.responses[1].dialogue.casefold() or "do not know" in result.responses[1].dialogue.casefold()


def test_whisper_eitan_transcript_routes_to_ethan_and_preserves_question():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    transcript = "Eitan, what number I'm thinking right now?"
    result = router.handle(transcript, session_id="classroom")

    assert result.target == "ethan-luna"
    assert [item.persona_id for item in result.responses] == ["ethan-luna"]
    assert behavior.inputs[0].trainee_input == transcript


def test_all_failed_ethan_name_transcripts_get_name_only_acknowledgement():
    router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=object()), tts=FakeTTS()
    )

    for transcript in ("Ethan!", "Etan", "Itan.", "Eitan"):
        result = router.handle(transcript, session_id=f"name-{transcript}")

        assert result.target == "ethan-luna"
        assert len(result.responses) == 1
        assert result.responses[0].persona_id == "ethan-luna"
        assert result.responses[0].dialogue == "Yes, Ma'am!"
        assert result.responses[0].speak is True
        assert result.responses[0].audio_url is not None


def test_ethan_aliases_route_physical_and_conversational_commands():
    class ConversationBehavior(BehaviorService):
        pass

    class FakeModels:
        def generate_content(self, **kwargs):
            from types import SimpleNamespace
            return SimpleNamespace(
                text='{"persona_id":"ethan-luna","dialogue":"Two plus two is four.","emotion":"confident","action":"NONE","speak":true}'
            )

    class FakeClient:
        models = FakeModels()

    router = MicrophoneCommandRouter(
        behavior=ConversationBehavior(client=FakeClient()), tts=FakeTTS()
    )

    standing = router.handle("Etan, stand up.", session_id="etan-stand")
    seated = router.handle("Itan, sit down.", session_id="itan-sit")
    answer = router.handle("Eitan, what is two plus two?", session_id="eitan-answer")

    assert standing.target == seated.target == answer.target == "ethan-luna"
    assert standing.responses[0].action == "STAND"
    assert seated.responses[0].action == "SIT"
    assert answer.responses[0].persona_id == "ethan-luna"
    assert any(response.speak for response in answer.responses)


def test_ethan_exact_questions_do_not_fall_into_clarification_for_unselected_or_grade_session():
    import json
    from types import SimpleNamespace

    class QuestionModels:
        def __init__(self):
            self.calls = []

        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            outputs = [
                {"dialogue": "The planets are Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus, and Neptune.", "emotion": "confident", "action": "NONE", "speak": True},
                {"dialogue": "Five plus five is ten.", "emotion": "confident", "action": "NONE", "speak": True},
                {"dialogue": "I'm sorry, Ma'am. I don't know the answer.", "emotion": "uncertain", "action": "IDK", "speak": True},
                {"dialogue": "I'm not sure, Ma'am.", "emotion": "uncertain", "action": "IDK", "speak": True},
            ]
            output = outputs[(len(self.calls) - 1) % len(outputs)]
            return SimpleNamespace(text=json.dumps({
                "persona_id": "ethan-luna",
                **output,
            }))

    class QuestionClient:
        def __init__(self, models):
            self.models = models

    for grade in (None, "Grade 8"):
        models = QuestionModels()
        router = MicrophoneCommandRouter(
            behavior=BehaviorService(client=QuestionClient(models)), tts=FakeTTS()
        )
        session_id = f"ethan-questions-{grade or 'unselected'}"
        results = [
            router.handle("Ethan, name all of the planet in our solar system.", session_id=session_id, selected_grade_level=grade),
            router.handle("Ethan, what is five plus five?", session_id=session_id, selected_grade_level=grade),
            router.handle("Ethan, explain quantum entanglement.", session_id=session_id, selected_grade_level=grade),
            router.handle("Ethan, what number am I thinking of right now?", session_id=session_id, selected_grade_level=grade),
        ]

        assert all(result.target == "ethan-luna" for result in results)
        assert results[0].responses[0].dialogue.startswith("The planets")
        assert results[1].responses[0].dialogue == "Five plus five is ten."
        assert "don't know" in results[2].responses[-1].dialogue.casefold()
        assert (
            "not sure" in results[3].responses[-1].dialogue.casefold()
            or "don't know" in results[3].responses[-1].dialogue.casefold()
        )
        assert all(response.speak for result in results for response in result.responses if response.dialogue)


def test_unknowable_personal_questions_are_deterministic_idk_for_intermediate_students():
    router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=object()), tts=FakeTTS()
    )
    questions = (
        "What did I eat yesterday?",
        "What did I eat for breakfast yesterday?",
        "What was I wearing yesterday?",
        "What number am I thinking of?",
        "What is inside my bag?",
    )

    for student_name, student_id in (
        ("Ethan", "ethan-luna"),
        ("Andrea", "andrea-guinto"),
        ("Francis", "francis-reyes"),
    ):
        for index, question in enumerate(questions):
            result = router.handle(
                f"{student_name}, {question}",
                session_id=f"unknowable-{student_id}-{index}",
            )
            assert result.target == student_id
            assert [response.action for response in result.responses] == ["THINKING", "IDK"]
            assert result.responses[0].speak is False
            assert result.responses[-1].speak is True
            assert result.responses[-1].dialogue
            assert result.responses[-1].audio_url is not None


def test_derive_question_is_not_misclassified_as_clarification():
    class DeriveModels:
        def generate_content(self, **kwargs):
            import json
            from types import SimpleNamespace

            return SimpleNamespace(text=json.dumps({
                "persona_id": "ethan-luna",
                "dialogue": "I am not sure how to derive that, Ma'am.",
                "emotion": "thoughtful",
                "action": "NONE",
                "speak": True,
            }))

    class DeriveClient:
        models = DeriveModels()

    router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=DeriveClient()), tts=FakeTTS()
    )
    result = router.handle(
        "Ethan, derive Einstein's field equations.",
        session_id="derive-regression",
    )

    assert result.target == "ethan-luna"
    assert result.responses[-1].dialogue != "Sorry, I did not understand. Could you please repeat that?"
    assert result.responses[-1].speak is True


def test_francis_aliases_posture_and_session_grade_sequence():
    from unittest.mock import Mock

    tts = Mock(spec=FakeTTS)
    tts.synthesize.return_value = "http://localhost/audio/test.mp3"
    router = MicrophoneCommandRouter(behavior=BehaviorService(client=object()), tts=tts)
    session = "francis-test"
    student = router.registry.get("francis-reyes")
    assert student.knowledge_level == "INTERMEDIATE" and student.gender == "male"
    assert student.voice == "en-US-GuyNeural"
    assert all(other.voice != student.voice for other in router.registry.list_students() if other != student)
    assert {"NONE", "TALK", "STAND", "SIT", "RAISE_HAND", "LOWER_HAND", "THINKING", "IDK"} <= set(student.supported_actions)
    for alias in ("Francis", "Francis Reyes", "Fransis"):
        result = router.handle(alias, session_id=session)
        assert result.target == student.student_id
        reply, = result.responses
        assert (reply.persona_id, reply.action, reply.dialogue, reply.speak) == (
            student.student_id, "NONE", "Yes, Ma'am!", True
        )
        assert reply.audio_url == tts.synthesize.return_value
        assert tts.synthesize.call_args.kwargs["voice"] == student.voice

    assert router.handle("Fransis, stand up.", session_id=session).responses[0].action == "STAND"
    assert router.handle("Sit down, Francis Reyes.", session_id=session).responses[0].action == "SIT"
    assert router.registry.get_selected_grade_level(session) is None
    router.registry.set_selected_grade_level(session, "Grade 8")
    result = router.handle("Francis, explain quantum field theory.", session_id=session)
    assert [(reply.action, reply.speak) for reply in result.responses] == [("THINKING", False), ("IDK", True)]
    assert router.registry.get_conversation_memory(session, student.student_id)["first_teacher_question"] == "Francis"
    assert not router.registry.get_conversation_memory(session, "ethan-luna")["recent_turns"]
    assert not router.registry.get_conversation_memory("another-class", student.student_id)["recent_turns"]


def test_francis_volunteers_silently_and_lowers_hand_before_selection_reply():
    class VolunteerBehavior(BehaviorService):
        def decide_classroom_volunteers(self, question, students, *, round_number=0):
            assert any(student.student_id == "francis-reyes" for student in students)
            return {"francis-reyes": "RAISE_HAND"}

    router = MicrophoneCommandRouter(behavior=VolunteerBehavior(client=object()), tts=FakeTTS())
    invitation = router.handle("Who wants to answer?", session_id="francis-hands")
    assert [(r.persona_id, r.action, r.speak) for r in invitation.responses] == [
        ("francis-reyes", "RAISE_HAND", False)
    ]
    selected = router.handle("Francis", session_id="francis-hands")
    assert [(r.persona_id, r.action, r.speak) for r in selected.responses] == [
        ("francis-reyes", "LOWER_HAND", False), ("francis-reyes", "NONE", True)
    ]


def test_routes_everyone_to_all_default_students():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    result = router.handle("Everyone, please sit down.", session_id="classroom")

    assert [item.persona_id for item in result.responses] == [
        "maria-sandoval", "robert-eco", "ethan-luna", "andrea-guinto", "judy-castillo", "francis-reyes",
        "dave-santos", "juan-cruz",
    ]
    assert all(item.action == "SIT" for item in result.responses)


def test_all_robert_aliases_route_to_one_canonical_persona_id():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    for alias in ("Robert", "Berto", "Robert Eco", "berto eco"):
        result = router.handle(f"{alias}, please stand up.", session_id="classroom")
        assert result.target == "robert-eco"
        assert [item.persona_id for item in result.responses] == ["robert-eco"]


def test_routes_classroom_command_to_30_registered_students_without_provider_calls():
    registry = StudentRegistry()
    for index in range(30):
        registry.register(
            StudentProfile(
                student_id=f"student-{index}",
                name=f"Student {index}",
                aliases=(f"student {index}",),
                personality="curious and cooperative",
                voice="fil-PH-BlessicaNeural",
            )
        )

    class NeverCalledModels:
        def generate_content(self, **kwargs):
            raise AssertionError("classroom posture command must not call the AI provider")

    class NeverCalledClient:
        models = NeverCalledModels()

    behavior = BehaviorService(client=NeverCalledClient())
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS(), registry=registry)
    result = router.handle("Everyone, please sit down.", session_id="classroom")

    assert len(result.responses) == 30
    assert all(item.action == "SIT" for item in result.responses)


def test_routes_future_registered_student_when_name_is_at_sentence_end():
    registry = StudentRegistry()
    registry.register(
        StudentProfile(
            student_id="ana-delacruz",
            name="Ana Dela Cruz",
            aliases=("ana", "teacher ana"),
            personality="a patient student",
            voice="fil-PH-BlessicaNeural",
        )
    )

    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS(), registry=registry)
    result = router.handle("Please stand up, Ana.", session_id="classroom")

    assert result.target == "ana-delacruz"
    assert result.responses[0].persona_id == "ana-delacruz"
    assert result.responses[0].action == "STAND"
    assert result.responses[0].speak is False
    assert result.responses[0].audio_url is None


def test_unrecognized_explicit_student_does_not_fallback_to_maria():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    result = router.handle("Rober, please stand up.", session_id="classroom")

    assert result.target == "none"
    assert result.responses == []
    assert behavior.inputs == []


def test_multiple_explicit_student_matches_do_not_execute_physical_action():
    registry = StudentRegistry(
        (
            StudentProfile(student_id="alex-one", name="Alex One", aliases=("alex one",)),
            StudentProfile(student_id="alex-two", name="Alex Two", aliases=("alex two",)),
        )
    )
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS(), registry=registry)

    result = router.handle("Alex One and Alex Two, please stand.", session_id="classroom")

    assert result.target == "none"
    assert result.responses == []
    assert behavior.inputs == []


def test_router_passes_student_memory_and_records_teacher_and_student_turns():
    registry = StudentRegistry()
    registry.register(
        StudentProfile(student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",))
    )

    class MemoryBehavior(FakeBehavior):
        def generate_reply(self, request):
            self.inputs.append(request)
            return BehaviorResponse(
                persona_id=request.persona_id,
                dialogue="I was first asked to introduce myself.",
                emotion="calm",
                action="NONE",
                speak=True,
            )

    behavior = MemoryBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS(), registry=registry)
    router.handle("Maria, introduce yourself", session_id="classroom")
    router.handle("Maria, what was my first question?", session_id="classroom")

    assert behavior.inputs[1].recent_turns[0] == {
        "speaker": "teacher",
        "text": "Maria, introduce yourself",
    }
    assert behavior.inputs[1].recent_turns[1]["text"] == "I was first asked to introduce myself."
    assert "first_teacher_question=Maria, introduce yourself" in behavior.inputs[1].session_summary


def test_unclear_direct_request_gets_spoken_clarification_and_tts_url():
    registry = StudentRegistry()
    registry.register(
        StudentProfile(student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",))
    )

    class ClarifyingBehavior:
        def generate_reply(self, request):
            assert request.clarification_required is True
            return BehaviorResponse(
                persona_id=request.persona_id,
                dialogue="Pasensya na po, maaari mo bang ulitin?",
                emotion="respectful",
                action="NONE",
                speak=True,
            )

    router = MicrophoneCommandRouter(
        behavior=ClarifyingBehavior(), tts=FakeTTS(), registry=registry
    )
    result = router.handle("Maupukan na, Maria Sandoval", session_id="classroom")

    response = result.responses[0]
    assert response.persona_id == "maria-sandoval"
    assert response.action == "NONE"
    assert response.speak is True
    assert response.dialogue
    assert response.audio_url == "http://127.0.0.1:8000/audio/test.mp3"


def test_actual_behavior_service_clarification_reaches_tts_pipeline():
    registry = StudentRegistry()
    registry.register(
        StudentProfile(student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",))
    )

    class NeverCalledModels:
        def generate_content(self, **kwargs):
            raise AssertionError("clarification should not require an LLM call")

    class NeverCalledClient:
        models = NeverCalledModels()

    router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=NeverCalledClient()),
        tts=FakeTTS(),
        registry=registry,
    )
    response = router.handle("Maupukan na, Maria Sandoval", session_id="classroom").responses[0]

    assert response.dialogue
    assert response.action == "NONE"
    assert response.speak is True
    assert response.audio_url == "http://127.0.0.1:8000/audio/test.mp3"


def test_name_only_student_call_gets_spoken_acknowledgement():
    registry = StudentRegistry()
    registry.register(
        StudentProfile(student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",))
    )

    class AcknowledgingBehavior:
        def generate_reply(self, request):
            assert request.name_only_address is True
            return BehaviorResponse(
                persona_id=request.persona_id,
                dialogue="Yes, Ma'am?",
                emotion="respectful",
                action="NONE",
                speak=True,
            )

    router = MicrophoneCommandRouter(
        behavior=AcknowledgingBehavior(), tts=FakeTTS(), registry=registry
    )
    response = router.handle("Maria!", session_id="classroom").responses[0]

    assert response.speak is True
    assert response.dialogue == "Yes, Ma'am?"
    assert response.audio_url is not None


def test_incidental_student_name_is_not_routed_as_a_direct_address():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    result = router.handle("I saw Maria yesterday", session_id="classroom")

    assert result.target == "none"
    assert result.responses == []
    assert behavior.inputs == []


def test_incidental_full_name_at_sentence_end_is_not_routed():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    result = router.handle("I saw Maria Sandoval", session_id="classroom")

    assert result.target == "none"
    assert result.responses == []


def test_direct_physical_command_uses_real_behavior_ack_and_tts():
    registry = StudentRegistry()
    registry.register(
        StudentProfile(student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",))
    )

    class NeverCalledModels:
        def generate_content(self, **kwargs):
            raise AssertionError("physical acknowledgment should not require an LLM call")

    class NeverCalledClient:
        models = NeverCalledModels()

    router = MicrophoneCommandRouter(
        behavior=BehaviorService(client=NeverCalledClient()),
        tts=FakeTTS(),
        registry=registry,
    )
    response = router.handle("Maria, please stand up", session_id="classroom").responses[0]

    assert response.action == "STAND"
    assert response.dialogue == "Yes, Ma'am."
    assert response.speak is True
    assert response.audio_url == "http://127.0.0.1:8000/audio/test.mp3"


def test_unaddressed_individual_commands_questions_and_greetings_return_no_responses():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    for transcript in ("Please sit down", "What is a computer?", "Hello"):
        result = router.handle(transcript, session_id="classroom")
        assert result.target == "none", transcript
        assert result.responses == [], transcript

    assert behavior.inputs == []


def test_context_does_not_implicitly_target_previous_student():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    addressed = router.handle("Maria, introduce yourself", session_id="classroom")
    unaddressed = router.handle("Please stand up", session_id="classroom")

    assert addressed.target == "maria-sandoval"
    assert unaddressed.target == "none"
    assert len(behavior.inputs) == 1


def test_classroom_wide_posture_command_is_silent_for_all_students():
    registry = StudentRegistry()
    registry.register(StudentProfile(student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",)))
    registry.register(StudentProfile(student_id="robert-eco", name="Robert Eco", aliases=("robert",)))

    class ClassroomBehavior(FakeBehavior):
        def generate_reply(self, request):
            self.inputs.append(request)
            return BehaviorResponse(
                persona_id=request.persona_id,
                dialogue="",
                emotion="neutral",
                action="SIT",
                speak=False,
            )

    behavior = ClassroomBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS(), registry=registry)
    result = router.handle("Everyone, please sit down", session_id="classroom")

    assert result.target == "classroom"
    assert len(result.responses) == 2
    assert all(response.speak is False for response in result.responses)


def test_classroom_invitation_returns_only_independent_volunteers_without_speech():
    registry = StudentRegistry()
    registry.register(StudentProfile(student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",)))
    registry.register(StudentProfile(student_id="robert-eco", name="Robert Eco", aliases=("robert",)))

    class VolunteerBehavior(FakeBehavior):
        def decide_classroom_volunteers(self, question, students, *, round_number=0):
            assert question == "What is a computer?"
            return {"maria-sandoval": "RAISE_HAND", "robert-eco": "NONE"}

    router = MicrophoneCommandRouter(
        behavior=VolunteerBehavior(), tts=FakeTTS(), registry=registry
    )
    router.handle("What is a computer?", session_id="classroom")
    result = router.handle("Who wants to answer?", session_id="classroom")

    assert result.target == "classroom"
    assert [response.persona_id for response in result.responses] == ["maria-sandoval"]
    assert result.responses[0].action == "RAISE_HAND"
    assert result.responses[0].dialogue == ""
    assert result.responses[0].speak is False
    assert result.responses[0].audio_url is None
    assert registry.get_session_state("classroom", "maria-sandoval").current_state == "engaged"


def test_tagalog_classroom_invitation_uses_latest_question_and_supports_zero_volunteers():
    registry = StudentRegistry()
    registry.register(StudentProfile(student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",)))

    class NoVolunteerBehavior(FakeBehavior):
        def decide_classroom_volunteers(self, question, students, *, round_number=0):
            assert question == "Ano ang computer?"
            return {"maria-sandoval": "NONE"}

    router = MicrophoneCommandRouter(
        behavior=NoVolunteerBehavior(), tts=FakeTTS(), registry=registry
    )
    router.handle("Ano ang computer?", session_id="classroom")
    result = router.handle("Sino ang gustong sumagot?", session_id="classroom")

    assert result.target == "classroom"
    assert result.responses == []


def test_semantic_taglish_invitation_routes_latest_question_to_batch_decider():
    registry = StudentRegistry()
    registry.register(StudentProfile(student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",)))

    class VolunteerBehavior(FakeBehavior):
        def decide_classroom_volunteers(self, question, students, *, round_number=0):
            assert question == "What is a computer?"
            return {"maria-sandoval": "RAISE_HAND"}

    router = MicrophoneCommandRouter(
        behavior=VolunteerBehavior(), tts=FakeTTS(), registry=registry
    )
    router.handle("What is a computer?", session_id="classroom")
    result = router.handle("Anyone gustong sumagot?", session_id="classroom")

    assert result.target == "classroom"
    assert [response.persona_id for response in result.responses] == ["maria-sandoval"]
    assert result.responses[0].action == "RAISE_HAND"


def test_invitation_without_prior_question_still_runs_autonomous_decision():
    registry = StudentRegistry()
    registry.register(StudentProfile(
        student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",),
    ))

    class VolunteerBehavior(FakeBehavior):
        def decide_classroom_volunteers(self, question, students, *, round_number=0):
            assert question == "Who wants to answer?"
            return {"maria-sandoval": "RAISE_HAND"}

    router = MicrophoneCommandRouter(
        behavior=VolunteerBehavior(), tts=FakeTTS(), registry=registry
    )
    result = router.handle("Who wants to answer?", session_id="classroom")

    assert result.target == "classroom"
    assert [(item.persona_id, item.action) for item in result.responses] == [
        ("maria-sandoval", "RAISE_HAND")
    ]
    assert result.responses[0].speak is False
    assert registry.get_session_state("classroom", "maria-sandoval").data["hand_raised"] is True


def test_direct_raise_hand_command_is_ignored():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    result = router.handle("Raise your hand", session_id="classroom")

    assert result.target == "none"
    assert result.responses == []
    assert behavior.inputs == []


def test_new_question_lowers_waiting_hands_without_speech():
    registry = StudentRegistry()
    registry.register(StudentProfile(
        student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",),
    ))
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS(), registry=registry)
    registry.mark_hand_raised("classroom", "maria-sandoval", round_number=1)

    result = router.handle("What is a computer?", session_id="classroom")

    assert [(item.persona_id, item.action) for item in result.responses] == [
        ("maria-sandoval", "LOWER_HAND")
    ]
    assert result.responses[0].speak is False
    assert registry.get_session_state("classroom", "maria-sandoval").data["hand_raised"] is False


def test_selecting_student_lowers_all_waiting_hands_before_response():
    registry = StudentRegistry()
    registry.register(StudentProfile(
        student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",),
    ))
    registry.register(StudentProfile(
        student_id="robert-eco", name="Robert Eco", aliases=("robert",),
    ))
    registry.mark_hand_raised("classroom", "maria-sandoval", round_number=1)
    registry.mark_hand_raised("classroom", "robert-eco", round_number=1)
    router = MicrophoneCommandRouter(behavior=FakeBehavior(), tts=FakeTTS(), registry=registry)

    result = router.handle("Maria, what is a computer?", session_id="classroom")

    assert [(item.persona_id, item.action) for item in result.responses[:2]] == [
        ("maria-sandoval", "LOWER_HAND"),
        ("robert-eco", "LOWER_HAND"),
    ]
    assert result.responses[-1].persona_id == "maria-sandoval"
    assert registry.get_session_state("classroom", "maria-sandoval").data["hand_raised"] is False
    assert registry.get_session_state("classroom", "robert-eco").data["hand_raised"] is False


def test_expired_waiting_hand_emits_lower_hand_on_next_event():
    registry = StudentRegistry()
    registry.register(StudentProfile(
        student_id="maria-sandoval", name="Maria Sandoval", aliases=("maria",),
    ))
    registry.mark_hand_raised("classroom", "maria-sandoval", round_number=1)
    state = registry.get_session_state("classroom", "maria-sandoval")
    state.data["hand_raised_at"] -= 11.0
    router = MicrophoneCommandRouter(behavior=FakeBehavior(), tts=FakeTTS(), registry=registry)

    result = router.handle("Hello", session_id="classroom")

    assert [(item.persona_id, item.action) for item in result.responses] == [
        ("maria-sandoval", "LOWER_HAND")
    ]
    assert result.responses[0].speak is False
