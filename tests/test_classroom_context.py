import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from classsimvr.app import app
from classsimvr.database import get_session
from classsimvr.models import AuthAccount, Base, Session, Trainee
from classsimvr.routes.microphone import ListeningCoordinator, get_microphone_manager
from classsimvr.services.academic_ability import Difficulty, SelectedBehavior
from classsimvr.services.auth import create_access_token
from classsimvr.services.behavior import BehaviorService
from classsimvr.services.microphone_commands import MicrophoneCommandRouter
from classsimvr.services.session_contracts import ClassroomContext, SessionCreate


def test_unreal_numbered_session_uses_existing_create_contract():
    data = SessionCreate(
        trainee_id=1, grade="Grade 4", subject="Filipino",
        session_mode="assessment", start_immediately=True,
    )
    assert data.grade == "Grade 4" and data.subject == "Filipino"
    assert data.session_mode == "assessment" and data.start_immediately


@pytest.mark.parametrize(("grade", "subject"), [
    ("Grade 0", "Mathematics"), ("Grade 7", "Mathematics"),
    ("Grade 1", "Science"), ("Grade 2", "AralingPanlipunan"),
    ("Grade 4", "ReadingAndLiteracy"), ("Grade 4", "Unknown"),
])
def test_invalid_unreal_subject_or_grade(grade, subject):
    with pytest.raises(ValueError):
        SessionCreate(trainee_id=1, grade=grade, subject=subject, start_immediately=True)


def make_router(relation="RELATED", dialogue="Ten po, Ma'am."):
    client = Mock()

    def generate(*, contents, config, **kwargs):
        payload = json.loads(contents)
        assert "subject_relation" in config["response_schema"]["required"]
        assert "independent of the spoken language" in config["system_instruction"]
        return SimpleNamespace(text=json.dumps({
            "persona_id": payload["persona_id"], "dialogue": dialogue,
            "emotion": "respectful", "action": "NONE", "speak": True,
            "subject_relation": relation,
        }))

    client.models.generate_content.side_effect = generate
    ability = Mock()
    ability.select_behavior.return_value = SimpleNamespace(
        difficulty=Difficulty.EASY, behavior=SelectedBehavior.ANSWER,
    )
    tts = Mock()
    tts.synthesize.return_value = "http://localhost/audio/answer.mp3"
    return MicrophoneCommandRouter(
        behavior=BehaviorService(client=client, academic_controller=ability), tts=tts,
    ), client, tts


def test_all_registered_students_receive_session_context_personality_and_voice():
    router, client, tts = make_router()
    context = ClassroomContext.from_selection("Grade 4", "Mathematics", "assessment")
    students = router.registry.list_students()
    assert len(students) == 15
    assert {student.knowledge_level for student in students} == {"ADVANCED", "INTERMEDIATE", "BEGINNER"}
    for student in students:
        result = router.handle(
            f"{student.name}, what is five plus five?", session_id="db:40", classroom_context=context,
        )
        assert result.target == student.student_id
        reply, = result.responses
        assert reply.dialogue == "Ten po, Ma'am." and reply.audio_url == tts.synthesize.return_value
        assert reply.action == "NONE" and reply.speak
        assert "subject_relation" not in reply.model_dump()
        payload = json.loads(client.models.generate_content.call_args.kwargs["contents"])
        assert payload["classroom_session"] == context.prompt_context()
        assert payload["grade_level"] == "Grade 4"
        assert payload["knowledge_level"] == student.knowledge_level
        assert payload["context"]["persona_profile"] == student.personality
        assert tts.synthesize.call_args.kwargs["voice"] == student.voice


@pytest.mark.parametrize("subject", ["Filipino", "Science", "Mathematics"])
@pytest.mark.parametrize(("utterance", "language"), [
    ("Maria, what does the example show?", "English"),
    ("Maria, ano ang ipinapakita sa halimbawa?", "Filipino"),
    ("Maria, can you explain kung ano ang nakita mo?", "Taglish"),
])
def test_language_stays_independent_of_subject(subject, utterance, language):
    router, client, _ = make_router("RELATED", "A short classroom reply.")
    context = ClassroomContext.from_selection("Grade 5", subject, "practice")
    assert router.handle(utterance, session_id="db:41", classroom_context=context).responses[0].speak
    payload = json.loads(client.models.generate_content.call_args.kwargs["contents"])
    assert payload["language"] == language
    assert payload["classroom_session"]["subject"] == subject


def test_off_subject_answer_is_removed_but_directions_greetings_and_clarification_work():
    context = ClassroomContext.from_selection("Grade 4", "Mathematics", "practice")
    router, client, tts = make_router("UNRELATED", "Detailed unrelated lesson that must never be spoken.")
    redirects = set()
    for text in ("ano ang pang-uri?", "what is photosynthesis?", "can you explain kung ano ang pandiwa?"):
        reply, = router.handle(f"Maria, {text}", session_id="db:42", classroom_context=context).responses
        assert "Mathematics" in reply.dialogue and "Detailed" not in reply.dialogue
        assert reply.speak and reply.action == "NONE" and reply.audio_url
        assert tts.synthesize.call_args.args[0] == reply.dialogue
        redirects.add(reply.dialogue)
    assert len(redirects) > 1
    call_count = client.models.generate_content.call_count
    stand, = router.handle("Maria, tumayo ka.", session_id="db:42", classroom_context=context).responses
    assert stand.action == "STAND" and stand.audio_url
    clarification, = router.handle("Maria, blargh plip.", session_id="db:42", classroom_context=context).responses
    assert clarification.speak and clarification.action == "NONE"
    assert client.models.generate_content.call_count == call_count

    router, _, _ = make_router("OTHER", "Good morning, Ma'am!")
    greeting, = router.handle("Maria, good morning!", session_id="db:43", classroom_context=context).responses
    assert greeting.dialogue == "Good morning, Ma'am!"


def test_cross_subject_example_classified_as_supporting_lesson_is_allowed():
    router, _, _ = make_router("RELATED", "We used a fraction to compare the lengths of the leaves.")
    context = ClassroomContext.from_selection("Grade 4", "Mathematics", "practice")
    reply, = router.handle(
        "Maria, what fraction of these leaves is green?", session_id="db:42", classroom_context=context,
    ).responses
    assert "compare the lengths" in reply.dialogue


def test_provider_failure_in_scoped_session_keeps_existing_fallback():
    router, client, _ = make_router()
    client.models.generate_content.side_effect = RuntimeError("provider temporarily unavailable")
    context = ClassroomContext.from_selection("Grade 4", "Mathematics", "practice")
    reply, = router.handle(
        "Maria, what is five plus five?", session_id="db:1", classroom_context=context,
    ).responses
    assert reply.fallback and reply.speak and reply.audio_url


def test_session_creation_and_voice_route_use_owned_database_context(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "classroom-session-fixture-test-key-long-enough")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with DbSession(engine) as db:
        headers = []
        for number in range(1, 3):
            trainee = Trainee(full_name=f"Fixture {number}", email=f"fixture{number}@example.invalid")
            db.add(trainee)
            db.flush()
            account = AuthAccount(
                trainee_id=trainee.trainee_id, username=f"fixture{number}", email=trainee.email,
                password_hash="unused-by-bearer-fixture", is_active=True,
            )
            db.add(account)
            db.flush()
            token, _ = create_access_token(account)
            headers.append({"Authorization": f"Bearer {token}"})
        db.commit()

    def database():
        with DbSession(engine) as db:
            yield db

    coordinator = ListeningCoordinator()
    coordinator.router, provider, _ = make_router()
    coordinator.listener = Mock()
    coordinator.listener.current_command_timing.return_value = (None, {})
    for action in ("start", "stop", "pause", "resume", "cancel"):
        getattr(coordinator.listener, action).return_value = {"state": action}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            setup = {"trainee_id": 1, "grade": "Grade 4", "subject": "Mathematics",
                     "session_mode": "assessment", "start_immediately": True}
            assert (await http.post("/api/v1/sessions", json=setup)).status_code == 401
            assert (await http.post("/api/v1/sessions", headers=headers[1], json=setup)).status_code == 403
            created = await http.post("/api/v1/sessions", headers=headers[0], json=setup)
            assert created.status_code == 201, created.text
            session = created.json()
            assert session["grade"] == "Grade 4" and session["subject"] == "Mathematics"
            assert session["session_mode"] == "assessment" and session["status"] == "running"
            first = f"db:{session['session_id']}"
            with DbSession(engine) as db:
                assert db.get(Session, session["session_id"]).session_mode == "assessment"
            assert (await http.post("/microphone/start", json={"session_id": first})).status_code == 401
            assert (await http.post("/microphone/start", headers=headers[1], json={"session_id": first})).status_code == 404
            start = await http.post("/microphone/start", headers=headers[0],
                                    json={"session_id": first, "selected_grade_level": "Grade 1"})
            assert start.status_code == 200
            coordinator._on_transcription("Maria, what is five plus five?")
            data = json.loads(provider.models.generate_content.call_args.kwargs["contents"])
            assert data["classroom_session"] == {"grade": "Grade 4", "subject": "Mathematics", "session_mode": "assessment"}
            assert (await http.get("/microphone/responses", params={"session_id": first}, headers=headers[1])).status_code == 404
            assert (await http.post("/microphone/pause", json={"session_id": "classroom"})).status_code == 409
            reply = await http.get("/microphone/responses", params={"session_id": first}, headers=headers[0])
            assert reply.json()["items"][0]["responses"][0]["audio_url"].endswith("answer.mp3")
            assert (await http.post("/microphone/pause", headers=headers[0], json={"session_id": first})).status_code == 200
            assert (await http.post("/microphone/resume", headers=headers[0], json={"session_id": first})).status_code == 200

            new = await http.post("/api/v1/sessions", headers=headers[1], json={
                **setup, "trainee_id": 2, "grade": "Grade 5", "subject": "Science", "session_mode": "practice",
            })
            assert new.status_code == 201
            second = f"db:{new.json()['session_id']}"
            assert second != first
            assert (await http.post("/microphone/start", headers=headers[1], json={"session_id": second})).status_code == 409
            assert (await http.post("/microphone/cancel", headers=headers[0], json={"session_id": first})).status_code == 200
            assert (await http.post("/microphone/start", headers=headers[1], json={"session_id": second})).status_code == 200
            assert (await http.post("/microphone/cancel", headers=headers[0], json={"session_id": first})).status_code == 409
            coordinator._on_transcription("Maria, what does water turn into when it freezes?")
            data = json.loads(provider.models.generate_content.call_args.kwargs["contents"])
            assert data["classroom_session"] == {"grade": "Grade 5", "subject": "Science", "session_mode": "practice"}
            assert (await http.post("/microphone/stop", headers=headers[1], json={"session_id": second})).status_code == 200
            assert (await http.post("/microphone/start", json={"session_id": "db:uninitialized"})).status_code == 409

    monkeypatch.setitem(app.dependency_overrides, get_session, database)
    monkeypatch.setitem(app.dependency_overrides, get_microphone_manager, lambda: coordinator)
    try:
        asyncio.run(exercise())
    finally:
        coordinator.shutdown()
        engine.dispose()

