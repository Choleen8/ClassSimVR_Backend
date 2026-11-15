from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value.strip()


def _bounded_json_state(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None

    def validate(item: Any, depth: int = 0) -> None:
        if depth > 5:
            raise ValueError("state_data nesting is too deep")
        if item is None or isinstance(item, (bool, int, float, str)):
            return
        if isinstance(item, list):
            if len(item) > 50:
                raise ValueError("state_data lists are too large")
            for child in item:
                validate(child, depth + 1)
            return
        if isinstance(item, dict):
            if len(item) > 50 or any(not isinstance(key, str) for key in item):
                raise ValueError("state_data must contain bounded string-keyed objects")
            for key, child in item.items():
                if len(key) > 100:
                    raise ValueError("state_data keys are too long")
                validate(child, depth + 1)
            return
        raise ValueError("state_data must contain JSON-compatible values")

    validate(value)
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False)) > 8_000:
            raise ValueError("state_data is too large")
    except (TypeError, ValueError) as error:
        raise ValueError("state_data must contain JSON-compatible values") from error
    return value


class PersonaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    language: str = Field(default="English", max_length=100)
    academic_baseline: str | None = Field(default=None, max_length=2_000)
    avatar_ref: str | None = Field(default=None, max_length=255)
    personality_traits: list[str] = Field(default_factory=list, max_length=20)
    behavior_triggers: list[str] = Field(default_factory=list, max_length=20)
    response_style: str | None = Field(default=None, max_length=100)
    participation_level: str = Field(default="medium", max_length=50)
    is_active: bool = True

    _name_not_blank = field_validator("name", "language", "participation_level")(_non_blank)

    @field_validator("personality_traits", "behavior_triggers")
    @classmethod
    def profile_items_must_be_bounded(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item.strip() or len(item) > 200:
                raise ValueError("persona profile items must be non-blank and <= 200 characters")
        return [item.strip() for item in value]


class PersonaResponse(PersonaCreate):
    model_config = ConfigDict(from_attributes=True)

    persona_id: int


class SessionPersonaAssign(BaseModel):
    persona_id: int = Field(gt=0)
    initial_state: str = Field(default="engaged", min_length=1, max_length=50)
    current_emotion: str = Field(default="neutral", min_length=1, max_length=80)
    state_data: dict[str, object] = Field(default_factory=dict)
    is_active: bool = True

    _state_not_blank = field_validator("initial_state", "current_emotion")(_non_blank)
    _state_data_is_bounded = field_validator("state_data")(_bounded_json_state)


class SessionPersonaStateUpdate(BaseModel):
    current_state: str | None = Field(default=None, min_length=1, max_length=50)
    current_emotion: str | None = Field(default=None, min_length=1, max_length=80)
    state_summary: str | None = Field(default=None, max_length=4_000)
    state_data: dict[str, object] | None = None
    is_active: bool | None = None

    _state_not_blank = field_validator("current_state", "current_emotion")(_non_blank)
    _state_data_is_bounded = field_validator("state_data")(_bounded_json_state)


class AssignedPersonaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: int
    persona_id: int
    name: str
    language: str
    avatar_ref: str | None
    personality_traits: list[str]
    behavior_triggers: list[str]
    response_style: str | None
    participation_level: str
    initial_state: str
    current_state: str
    current_emotion: str
    state_summary: str | None
    state_data: dict[str, object]
    is_active: bool
