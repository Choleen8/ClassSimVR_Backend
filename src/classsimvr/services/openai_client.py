from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI


class OpenAIConfigurationError(RuntimeError):
    """Raised when the OpenAI client cannot be configured safely."""


def create_openai_client(api_key: str | None = None) -> OpenAI:
    load_dotenv()
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise OpenAIConfigurationError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=key)
