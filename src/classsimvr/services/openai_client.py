from __future__ import annotations

import os
import logging
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, OpenAI

logger = logging.getLogger(__name__)


class OpenAIConfigurationError(RuntimeError):
    """Raised when the OpenAI client cannot be configured safely."""

    code = 401


class OpenAIAPIError(RuntimeError):
    """Provider error with a safe message and status code for retry decisions."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def _openai_schema(value: Any) -> Any:
    if isinstance(value, dict):
        result = {
            key: (item.lower() if key == "type" and isinstance(item, str) else _openai_schema(item))
            for key, item in value.items()
        }
        if result.get("type") == "object":
            result.setdefault("additionalProperties", False)
            if "properties" in result:
                result.setdefault("required", list(result["properties"].keys()))
        return result
    if isinstance(value, list):
        return [_openai_schema(item) for item in value]
    return value


class _OpenAIModels:
    def __init__(self, client: OpenAI) -> None:
        self._client = client

    def generate_content(
        self, *, model: str, contents: str, config: dict[str, Any]
    ) -> Any:
        schema = config.get("response_schema")
        response_format: dict[str, Any] | None = None
        if schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "classsimvr_response",
                    "strict": True,
                    "schema": _openai_schema(schema),
                },
            }
        elif config.get("response_mime_type") == "application/json":
            response_format = {"type": "json_object"}

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": config.get("system_instruction", "")},
                {"role": "user", "content": contents},
            ],
            "max_completion_tokens": int(config.get("max_output_tokens", 512)),
        }
        if model.casefold().startswith("gpt-6-luna"):
            kwargs["reasoning_effort"] = "none"
        else:
            kwargs["temperature"] = float(os.getenv("OPENAI_BEHAVIOR_TEMPERATURE", "0.5"))
        if response_format:
            kwargs["response_format"] = response_format

        try:
            response = self._client.chat.completions.create(**kwargs)
        except ModuleNotFoundError as error:
            # The SDK lazily imports native dependencies when loading this endpoint.
            logger.warning("OpenAI SDK dependency could not be imported: %s", error.name or "unknown")
            raise OpenAIConfigurationError("OpenAI SDK dependency is unavailable") from error
        except APIStatusError as error:
            raise OpenAIAPIError(
                f"OpenAI returned HTTP {error.status_code}.", error.status_code
            ) from error
        except APIConnectionError as error:
            raise OpenAIAPIError("Could not connect to the OpenAI API.") from error

        if not response.choices:
            raise OpenAIAPIError("OpenAI returned no response choices.")
        message = response.choices[0].message
        text = message.content
        if not isinstance(text, str) or not text.strip():
            if getattr(message, "refusal", None):
                raise OpenAIAPIError("OpenAI declined the behavior request.")
            raise OpenAIAPIError("OpenAI returned an empty response.")

        usage = response.usage
        if usage is not None:
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
            logger.info(
                "OpenAI token usage model=%s operation=%s input_tokens=%d output_tokens=%d total_tokens=%d",
                model,
                config.get("usage_category", "student_behavior"),
                input_tokens,
                output_tokens,
                total_tokens,
            )

        return type("OpenAIBehaviorResponse", (), {
            "text": text,
            "usage": response.usage,
        })()


class OpenAIBehaviorClient:
    """Adapt the OpenAI Chat Completions API to the behavior service interface."""

    def __init__(self, client: OpenAI) -> None:
        self.models = _OpenAIModels(client)


def create_openai_client(api_key: str | None = None) -> OpenAIBehaviorClient:
    load_dotenv()
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise OpenAIConfigurationError("OPENAI_API_KEY is not configured")
    # BehaviorService owns the single bounded retry policy. Disable the SDK's
    # automatic retries so a transient error cannot multiply billable requests.
    return OpenAIBehaviorClient(OpenAI(api_key=key, max_retries=0))
