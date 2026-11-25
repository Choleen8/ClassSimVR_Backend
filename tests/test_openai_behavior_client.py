import json
import logging
from unittest.mock import Mock, PropertyMock

import httpx
from openai import OpenAI

from classsimvr.services.behavior import BehaviorService
from classsimvr.services.contracts import BehaviorRequest
from classsimvr.services.openai_client import OpenAIBehaviorClient


def request():
    return BehaviorRequest(
        session_id="sdk-check",
        persona_id="ayesha-ramirez",
        persona_name="Ayesha Ramirez",
        trainee_input="What is five plus three?",
    )


def test_installed_sdk_can_send_and_parse_a_structured_behavior_reply():
    def reply(http_request):
        assert http_request.url.path == "/v1/chat/completions"
        body = json.loads(http_request.content)
        assert body["response_format"]["type"] == "json_schema"
        payload = json.loads(body["messages"][-1]["content"])
        return httpx.Response(200, json={
            "id": "mock-completion", "object": "chat.completion",
            "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant",
                "content": json.dumps({
                    "persona_id": payload["persona_id"], "dialogue": "Five plus three is eight.",
                    "emotion": "confident", "action": "NONE", "speak": True,
                }),
            }}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })

    with OpenAI(
        api_key="test-only",
        base_url="https://mock.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(reply)),
        max_retries=0,
    ) as sdk:
        result = BehaviorService(client=OpenAIBehaviorClient(sdk)).generate_reply(request())

    assert result.dialogue == "Five plus three is eight."
    assert result.persona_id == "ayesha-ramirez" and result.speak and not result.fallback
    assert result.usage and result.usage.total_tokens == 120


def test_missing_sdk_binary_does_not_retry_or_expose_exception_text(caplog):
    sdk = Mock()
    missing = PropertyMock(side_effect=ModuleNotFoundError(
        "private exception detail", name="jiter.jiter",
    ))
    type(sdk).chat = missing
    service = BehaviorService(client=OpenAIBehaviorClient(sdk), max_retries=1)

    with caplog.at_level(logging.WARNING):
        result = service.generate_reply(request())

    assert result.fallback and result.speak and result.usage is None
    assert result.persona_id == "ayesha-ramirez" and result.action == "NONE"
    assert missing.call_count == 1
    assert "jiter.jiter" in caplog.text
    assert "private exception detail" not in caplog.text
    assert "private exception detail" not in result.dialogue
