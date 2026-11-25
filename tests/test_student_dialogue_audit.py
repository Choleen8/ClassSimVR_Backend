"""Exercise the live transcript-to-voice contract for every default student."""

import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from classsimvr.services.academic_ability import (
    AcademicAbilityController,
    AcademicDecision,
    SelectedBehavior,
)
from classsimvr.services.behavior import BehaviorService
from classsimvr.services.microphone_commands import MicrophoneCommandRouter
from classsimvr.services.speech_to_text import SpeechToTextService
from classsimvr.services.student_registry import DEFAULT_STUDENT_PROFILES, StudentRegistry
from classsimvr.services.tts import TextToSpeechService


QUESTIONS = (
    ("What is photosynthesis?", "Plants use light to make their own food."),
    ("What is five plus three?", "Five plus three is eight."),
    ("Why do plants need sunlight?", "Sunlight supplies energy for photosynthesis."),
    ("Can you explain the water cycle?", "Water evaporates, condenses, and falls as rain."),
    ("What did you understand from our lesson?", "I learned how plants use light and how water circulates."),
)
GRADE = "Grade 8"


@pytest.mark.parametrize("student_id,name,level,gender,voice", [
    ("amira-salazar", "Amira Salazar", "BEGINNER", "female", "en-US-AnaNeural"),
    ("justin-mercado", "Justin Mercado", "INTERMEDIATE", "male", "en-PH-JamesNeural"),
])
def test_final_student_profiles_route_with_canonical_id_and_own_audio(
    student_id, name, level, gender, voice, tmp_path, monkeypatch,
):
    registry = StudentRegistry.with_defaults()
    profile = registry.get(student_id)
    assert (profile.name, profile.knowledge_level, profile.gender, profile.voice) == (name, level, gender, voice)
    assert profile.personality and profile.academic_behavior_enabled
    assert {"STAND", "SIT", "TALK", "THINKING", "IDK"} <= set(profile.supported_actions)
    assert [other.student_id for other in registry.list_students() if other.voice == voice] == [student_id]
    assert name in SpeechToTextService()._initial_prompt()

    tts = TextToSpeechService(output_dir=tmp_path, public_base_url="http://127.0.0.1:8000")
    rendered_voices = []

    async def render(text, render_voice, path):
        rendered_voices.append(render_voice)
        path.write_bytes(b"fake mp3 bytes")

    monkeypatch.setattr(tts, "_save_audio", render)
    # The legacy turn route resolves a voice by ID without a registry voice override.
    assert tts._voice_for_persona(student_id) == voice
    router = MicrophoneCommandRouter(registry=registry, tts=tts, behavior=BehaviorService(client=object()))
    first_name = name.split()[0]
    for transcript, action in [(first_name, "NONE"), (f"{name}, please stand up.", "STAND")]:
        result = router.handle(transcript, session_id="final-student-test")
        assert result.target == student_id
        reply, = result.responses
        assert (reply.persona_id, reply.action, reply.speak, reply.fallback) == (student_id, action, True, False)
        url = urlsplit(reply.audio_url)
        assert url.netloc == "127.0.0.1:8000" and url.path.startswith("/audio/")
        filename = url.path.rsplit("/", 1)[-1]
        assert filename.endswith(".mp3") and (tmp_path / filename).read_bytes() == b"fake mp3 bytes"
    assert rendered_voices == [voice, voice]


@pytest.mark.parametrize("profile", DEFAULT_STUDENT_PROFILES, ids=lambda p: p.student_id)
def test_registered_student_answers_distinct_questions_and_keeps_their_context(profile, monkeypatch):
    registry = StudentRegistry.with_defaults()
    controller = AcademicAbilityController()
    academic_inputs = []
    provider_inputs = []
    audio_calls = []
    session = "mock-dialogue-audit"

    def select(question, **fields):
        academic_inputs.append(fields)
        difficulty = controller.classify(question, fields["grade_level"])
        return AcademicDecision(
            difficulty, SelectedBehavior.ANSWER, False, 0.0,
            controller.config.probabilities(difficulty, fields["knowledge_level"]),
        )

    monkeypatch.setattr(controller, "select_behavior", select)

    def generate(**kwargs):
        payload = json.loads(kwargs["contents"])
        provider_inputs.append(payload)
        answer = next(answer for question, answer in QUESTIONS if payload["trainee_input"].endswith(question))
        return SimpleNamespace(text=json.dumps({
            "persona_id": payload["persona_id"], "dialogue": answer,
            "emotion": "calm", "action": "NONE", "speak": True,
        }), usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120))

    def synthesize(text, file_stem, **kwargs):
        audio_calls.append((text, kwargs))
        return "http://localhost/audio/audit.mp3"

    router = MicrophoneCommandRouter(
        registry=registry,
        behavior=BehaviorService(
            client=SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
            academic_controller=controller,
        ),
        tts=SimpleNamespace(synthesize=synthesize),
    )
    first_name = profile.name.split()[0]
    assert registry.resolve(profile.name).student_id == profile.student_id
    assert registry.resolve(first_name).student_id == profile.student_id
    registered = registry.get(profile.student_id)
    assert registered.knowledge_level == profile.knowledge_level
    registry.set_selected_grade_level(session, GRADE)

    for question, _ in QUESTIONS:
        unaddressed = router.handle(question, session_id=session)
        assert unaddressed.target == "none" and unaddressed.responses == []
    assert not provider_inputs

    replies = []
    for i, (question, answer) in enumerate(QUESTIONS):
        address = profile.name if i % 2 == 0 else first_name
        result = router.handle(f"{address}, {question}", session_id=session)
        assert result.target == profile.student_id
        reply, = result.responses
        assert reply.model_dump() == {
            "persona_id": profile.student_id, "dialogue": answer, "emotion": "calm",
            "action": "NONE", "speak": True, "audio_url": "http://localhost/audio/audit.mp3",
            "fallback": False, "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        }
        assert audio_calls[-1][1]["voice"] == profile.voice
        payload = provider_inputs[-1]
        assert payload["persona_id"] == profile.student_id
        assert payload["knowledge_level"] == profile.knowledge_level and payload["grade_level"] == GRADE
        assert payload["context"]["persona_profile"] == profile.personality
        if replies:
            assert {"speaker": "student", "text": replies[-1]} in payload["context"]["recent_turns"]
        replies.append(reply.dialogue)
    assert len(set(replies)) == len(QUESTIONS)
    if profile.academic_behavior_enabled or profile.knowledge_level == "INTERMEDIATE":
        assert len(academic_inputs) == len(QUESTIONS)
        assert all(row["knowledge_level"] == profile.knowledge_level and row["grade_level"] == GRADE
                   for row in academic_inputs)

    unclear = router.handle(f"{first_name}, blorple frandle.", session_id=session)
    clarification, = unclear.responses
    assert (clarification.persona_id, clarification.action, clarification.speak) == (profile.student_id, "NONE", True)
    assert "understand" in clarification.dialogue.lower() and clarification.audio_url
    assert not clarification.fallback
    assert len(provider_inputs) == len(QUESTIONS)
