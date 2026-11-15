from __future__ import annotations

import os

from dotenv import load_dotenv
from google import genai


class GeminiConfigurationError(RuntimeError):
    """Raised when the Gemini client cannot be configured safely."""


def create_gemini_client(api_key: str | None = None) -> genai.Client:
    load_dotenv()
    key = (
        api_key
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_GENAI_API_KEY")
    )
    if not key:
        raise GeminiConfigurationError(
            "GEMINI_API_KEY or GOOGLE_API_KEY is not configured"
        )
    return genai.Client(api_key=key)
