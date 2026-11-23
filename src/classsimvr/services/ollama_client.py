from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv


class OllamaConfigurationError(RuntimeError):
    """Raised when the local Ollama endpoint is misconfigured."""


class OllamaAPIError(RuntimeError):
    """Safe provider error wrapper with an HTTP status for retry decisions."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OllamaUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class OllamaResponse:
    text: str
    usage: OllamaUsage | None


def _ollama_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (item.lower() if key == "type" and isinstance(item, str) else _ollama_schema(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_ollama_schema(item) for item in value]
    return value


class _OllamaModels:
    def __init__(self, base_url: str, timeout_seconds: float, keep_alive: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive

    def generate_content(
        self, *, model: str, contents: str, config: dict[str, Any]
    ) -> OllamaResponse:
        system_instruction = config.get("system_instruction", "")
        schema = config.get("response_schema")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": contents},
            ],
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "num_predict": int(config.get("max_output_tokens", 512)),
                "num_ctx": int(os.getenv("OLLAMA_CONTEXT_LENGTH", "4096")),
                "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0.5")),
            },
        }
        if schema:
            payload["format"] = _ollama_schema(schema)
        elif config.get("response_mime_type") == "application/json":
            payload["format"] = "json"

        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.RequestError as error:
            raise OllamaAPIError(
                f"Could not reach the local Ollama service ({type(error).__name__})."
            ) from error

        if response.is_error:
            raise OllamaAPIError(
                f"Ollama returned HTTP {response.status_code}.", response.status_code
            )

        body = response.json()
        if not isinstance(body, dict):
            raise OllamaAPIError("Ollama returned an invalid response body.")
        message = body.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise OllamaAPIError("Ollama returned an empty response.")

        prompt_tokens = int(body.get("prompt_eval_count", 0) or 0)
        output_tokens = int(body.get("eval_count", 0) or 0)
        usage = None
        if prompt_tokens or output_tokens:
            usage = OllamaUsage(
                input_tokens=prompt_tokens,
                output_tokens=output_tokens,
                total_tokens=prompt_tokens + output_tokens,
            )
        return OllamaResponse(text=text, usage=usage)


class OllamaClient:
    """Small Ollama adapter that keeps the behavior service's injectable client boundary."""

    def __init__(self, base_url: str, timeout_seconds: float, keep_alive: str) -> None:
        self.models = _OllamaModels(base_url, timeout_seconds, keep_alive)


def create_ollama_client() -> OllamaClient:
    load_dotenv()
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()
    if not base_url.startswith(("http://", "https://")):
        raise OllamaConfigurationError("OLLAMA_BASE_URL must be an HTTP(S) URL.")
    try:
        timeout_seconds = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "90"))
    except ValueError as error:
        raise OllamaConfigurationError("OLLAMA_TIMEOUT_SECONDS must be numeric.") from error
    if timeout_seconds <= 0:
        raise OllamaConfigurationError("OLLAMA_TIMEOUT_SECONDS must be positive.")
    keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "30m").strip() or "30m"
    return OllamaClient(base_url, timeout_seconds, keep_alive)
