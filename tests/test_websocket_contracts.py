import pytest
from pydantic import ValidationError

from classsimvr.services.websocket_contracts import WebSocketMessage


def test_websocket_turn_message_accepts_bounded_turn_payload():
    message = WebSocketMessage.model_validate(
        {
            "type": "turn",
            "persona_id": 7,
            "trainee_input": "What is one half?",
            "scene_state": "The class is reviewing fractions.",
            "recent_turns": [],
        }
    )

    assert message.type == "turn"
    assert message.persona_id == 7


def test_websocket_ping_message_requires_no_turn_fields():
    message = WebSocketMessage.model_validate({"type": "ping"})

    assert message.type == "ping"


def test_websocket_message_rejects_oversized_trainee_input():
    with pytest.raises(ValidationError):
        WebSocketMessage.model_validate(
            {"type": "turn", "persona_id": 7, "trainee_input": "x" * 4001}
        )


def test_websocket_message_rejects_unknown_type():
    with pytest.raises(ValidationError):
        WebSocketMessage.model_validate({"type": "unknown"})
