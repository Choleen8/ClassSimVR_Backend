from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WebSocketMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["turn", "ping"]
    persona_id: int | None = Field(default=None, gt=0)
    trainee_input: str | None = Field(default=None, min_length=1, max_length=4_000)
    scene_state: str = Field(default="", max_length=12_000)
    recent_turns: list[dict[str, str]] = Field(default_factory=list, max_length=30)

    @field_validator("trainee_input")
    @classmethod
    def trainee_input_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("trainee_input must not be blank")
        return value.strip() if value is not None else value

    @model_validator(mode="after")
    def validate_message(self) -> "WebSocketMessage":
        if self.type == "turn" and (self.persona_id is None or self.trainee_input is None):
            raise ValueError("turn messages require persona_id and trainee_input")
        if self.type == "ping" and (self.persona_id is not None or self.trainee_input is not None):
            raise ValueError("ping messages cannot contain turn fields")
        for turn in self.recent_turns:
            if len(turn.get("speaker", "")) > 80 or len(turn.get("text", "")) > 2_000:
                raise ValueError("recent turn fields exceed limits")
        return self


def connected_event(session_id: int) -> dict[str, object]:
    return {"type": "connected", "session_id": session_id}


def response_event(data: dict[str, object]) -> dict[str, object]:
    return {"type": "turn_response", "data": data}


def error_event(code: str, message: str) -> dict[str, str]:
    return {"type": "error", "code": code, "message": message}


def pong_event() -> dict[str, str]:
    return {"type": "pong"}
