import json

import pytest
from pydantic import ValidationError

from classsimvr.services.contracts import BehaviorRequest, BehaviorResponse
from classsimvr.services.token_budget import TokenBudget, estimate_tokens


def test_token_budget_trims_context_and_keeps_only_recent_turns():
    budget = TokenBudget(max_input_tokens=120, max_output_tokens=30, recent_turn_limit=2)
    context = budget.prepare_context(
        persona_profile="profile " * 500,
        scene_state="scene " * 500,
        session_summary="summary " * 500,
        recent_turns=[
            {"speaker": "trainee", "text": "old"},
            {"speaker": "learner", "text": "middle"},
            {"speaker": "trainee", "text": "latest"},
        ],
    )

    assert context["recent_turns"] == [
        {"speaker": "learner", "text": "middle"},
        {"speaker": "trainee", "text": "latest"},
    ]
    assert estimate_tokens(json.dumps(context)) <= budget.max_input_tokens


def test_behavior_request_rejects_blank_trainee_input():
    with pytest.raises(ValidationError):
        BehaviorRequest(
            session_id="session-1",
            persona_id="persona-1",
            persona_name="Ana",
            persona_profile="curious learner",
            trainee_input="   ",
        )


def test_behavior_response_has_structured_fields_and_fallback_flag():
    response = BehaviorResponse(
        persona_id="persona-1",
        dialogue="I need another example.",
        emotion="confused",
        action="raises_hand",
        fallback=True,
    )

    assert response.fallback is True
    assert response.dialogue


def test_behavior_response_can_include_audio_url():
    response = BehaviorResponse(
        persona_id="persona-1",
        dialogue="Hello, teacher.",
        emotion="calm",
        action="NONE",
        speak=True,
        audio_url="/audio/session-1.mp3",
    )

    assert response.audio_url == "/audio/session-1.mp3"


def test_behavior_response_rejects_unexpected_fields():
    with pytest.raises(ValidationError):
        BehaviorResponse(
            persona_id="persona-1",
            dialogue="Hello.",
            emotion="calm",
            action="waves",
            unexpected="should fail",
        )
