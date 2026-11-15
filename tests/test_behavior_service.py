from types import SimpleNamespace
import json

from classsimvr.services.behavior import BehaviorService
from classsimvr.services.contracts import BehaviorRequest
from classsimvr.services.openai_client import OpenAIConfigurationError, create_openai_client
from classsimvr.services.token_budget import TokenBudget, estimate_tokens


def request(text: str = "Can you explain your answer?") -> BehaviorRequest:
    return BehaviorRequest(
        session_id="session-1",
        persona_id="persona-1",
        persona_name="Ana",
        persona_profile="curious learner",
        language="English",
        trainee_input=text,
        scene_state="The class is reviewing fractions.",
        session_summary="Ana asked one question earlier.",
        recent_turns=[{"speaker": "learner", "text": "I am unsure."}],
    )


class FakeModels:
    def __init__(self, outputs, failures=0):
        self.outputs = list(outputs)
        self.failures = failures
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise RuntimeError("temporary provider failure")
        return SimpleNamespace(
            text=self.outputs.pop(0),
            usage_metadata=SimpleNamespace(prompt_token_count=20, candidates_token_count=12, total_token_count=32),
        )


class FakeClient:
    def __init__(self, models):
        self.models = models


def test_question_response_is_speech_intent_without_physical_action():
    models = FakeModels(['{"persona_id":"persona-1","dialogue":"It is one half.","emotion":"confident","action":"TALK","speak":true}'])
    service = BehaviorService(client=FakeClient(models), budget=TokenBudget(max_input_tokens=120, max_output_tokens=30))

    result = service.generate_reply(request())

    assert result.action == "NONE"
    assert result.speak is True
    assert result.dialogue == "It is one half."
    assert models.calls[0]["config"]["response_schema"]["properties"]["speak"]["type"] == "BOOLEAN"


def test_empty_llm_dialogue_for_introduction_uses_spoken_fallback():
    models = FakeModels(['{"persona_id":"persona-1","dialogue":"","emotion":"smiling","action":"NONE","speak":false}'])
    service = BehaviorService(client=FakeClient(models))

    result = service.generate_reply(request("Maria, introduce yourself"))

    assert result.dialogue
    assert result.speak is True
    assert result.action == "NONE"
    assert result.fallback is True


def test_behavior_payload_uses_teacher_language_for_taglish_utterance():
    models = FakeModels(['{"persona_id":"persona-1","dialogue":"A computer is a machine that processes information.","emotion":"calm","action":"NONE","speak":true}'])
    service = BehaviorService(client=FakeClient(models))

    service.generate_reply(request("Robert, can you explain kung ano ang computer?"))

    assert json.loads(models.calls[0]["contents"])["language"] == "Taglish"


def test_clarification_request_is_spoken_and_has_no_action():
    service = BehaviorService(client=FakeClient(FakeModels([])))
    unclear = request("Maupukan na, Maria Sandoval").model_copy(
        update={"clarification_required": True, "language": "Filipino"}
    )

    result = service.generate_reply(unclear)

    assert result.dialogue
    assert result.action == "NONE"
    assert result.speak is True
    assert result.fallback is False


def test_name_only_address_gets_natural_acknowledgement():
    service = BehaviorService(client=FakeClient(FakeModels([])))
    result = service.generate_reply(
        request("Maria!").model_copy(update={"name_only_address": True})
    )

    assert result.dialogue in {"Yes, Ma'am?", "Ano po, Ma'am?"}
    assert result.action == "NONE"
    assert result.speak is True


def test_physical_only_command_skips_provider_and_returns_spoken_acknowledgement():
    models = FakeModels([])
    service = BehaviorService(client=FakeClient(models))

    result = service.generate_reply(request("Please stand up"))

    assert result.action == "STAND"
    assert result.speak is True
    assert result.dialogue == "Yes, Ma'am."
    assert models.calls == []


def test_filipino_physical_command_returns_filipino_acknowledgement():
    service = BehaviorService(client=FakeClient(FakeModels([])))

    result = service.generate_reply(request("Maria, maupo ka na").model_copy(
        update={"language": "English"}
    ))

    assert result.action == "SIT"
    assert result.speak is True
    assert result.dialogue == "Opo, Ma'am."


def test_classroom_wide_physical_command_can_remain_silent():
    service = BehaviorService(client=FakeClient(FakeModels([])))
    result = service.generate_reply(
        request("Everyone, please sit down").model_copy(
            update={"acknowledgment_required": False}
        )
    )

    assert result.action == "SIT"
    assert result.dialogue == ""
    assert result.speak is False


def test_combined_command_keeps_physical_action_and_speaks():
    models = FakeModels(['{"persona_id":"persona-1","dialogue":"Python is a programming language.","emotion":"calm","action":"TALK","speak":true}'])
    service = BehaviorService(client=FakeClient(models))

    result = service.generate_reply(request("Please stand up and tell me what Python is"))

    assert result.action == "STAND"
    assert result.speak is True
    assert result.dialogue == "Python is a programming language."


def test_behavior_service_bounds_request_payload():
    models = FakeModels(['{"persona_id":"persona-1","dialogue":"Short answer.","emotion":"calm","action":"NONE","speak":true}'])
    service = BehaviorService(client=FakeClient(models), budget=TokenBudget(max_input_tokens=120, max_output_tokens=30))
    oversized = request().model_copy(update={"persona_profile": "profile " * 1000, "scene_state": "scene " * 1000, "session_summary": "summary " * 1000, "trainee_input": "question " * 500})

    service.generate_reply(oversized)

    assert estimate_tokens(models.calls[0]["contents"]) <= 120


def test_behavior_service_retries_transient_failures_with_a_bound():
    models = FakeModels(['{"persona_id":"persona-1","dialogue":"Try again.","emotion":"calm","action":"NONE","speak":true}'], failures=2)
    service = BehaviorService(client=FakeClient(models), max_retries=2)

    result = service.generate_reply(request())

    assert result.fallback is False
    assert len(models.calls) == 3


def test_behavior_service_falls_back_when_model_returns_wrong_persona_id():
    models = FakeModels(['{"persona_id":"persona-2","dialogue":"Wrong target.","emotion":"neutral","action":"NONE","speak":true}'])
    service = BehaviorService(client=FakeClient(models))

    result = service.generate_reply(request())

    assert result.fallback is True
    assert result.persona_id == "persona-1"
    assert result.speak is True


def test_openai_client_requires_an_api_key(monkeypatch):
    monkeypatch.setattr("classsimvr.services.openai_client.load_dotenv", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    try:
        create_openai_client()
    except OpenAIConfigurationError:
        pass
    else:
        raise AssertionError("missing API key should fail client construction")
