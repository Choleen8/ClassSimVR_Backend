from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class BehaviorRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    persona_id: str = Field(min_length=1, max_length=128)
    persona_name: str = Field(min_length=1, max_length=120)
    persona_profile: str = Field(default="", max_length=12000)
    language: str = Field(default="English", max_length=40)
    trainee_input: str = Field(min_length=1, max_length=4000)
    scene_state: str = Field(default="", max_length=12000)
    session_summary: str = Field(default="", max_length=12000)
    recent_turns: list[dict[str, str]] = Field(default_factory=list, max_length=30)
    clarification_required: bool = False
    name_only_address: bool = False
    acknowledgment_required: bool = True
    knowledge_level: str = Field(default="INTERMEDIATE", max_length=20)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    willingness: float = Field(default=0.5, ge=0.0, le=1.0)
    supported_actions: list[str] = Field(default_factory=lambda: [
        "NONE", "STAND", "SIT", "TALK", "STOP_TALKING", "RAISE_HAND", "LOWER_HAND",
    ], max_length=20)

    @field_validator("trainee_input")
    @classmethod
    def input_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trainee_input must not be blank")
        return value.strip()


class BehaviorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_id: str = Field(min_length=1, max_length=128)
    dialogue: str = Field(default="", max_length=400)
    emotion: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=120)
    speak: bool = False
    audio_url: str | None = None
    fallback: bool = False
    usage: TokenUsage | None = None
