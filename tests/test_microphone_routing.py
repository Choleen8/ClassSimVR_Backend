from classsimvr.services.behavior import BehaviorService
from classsimvr.services.contracts import BehaviorResponse
from classsimvr.services.microphone_commands import MicrophoneCommandRouter
from classsimvr.services.student_registry import StudentProfile, StudentRegistry


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


def test_routes_named_student_to_maria():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    result = router.handle("Maria, please stand up.", session_id="classroom")

    assert [item.persona_id for item in result.responses] == ["maria-sandoval"]
    assert result.responses[0].action == "STAND"
    assert behavior.inputs[0].persona_name == "Maria Sandoval"


def test_routes_everyone_to_both_students():
    behavior = FakeBehavior()
    router = MicrophoneCommandRouter(behavior=behavior, tts=FakeTTS())

    result = router.handle("Everyone, please sit down.", session_id="classroom")

    assert [item.persona_id for item in result.responses] == ["maria-sandoval", "robert-eco"]
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
    assert result.responses[0].speak is False
