from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class TurnCreate(BaseModel):
    persona_id: int = Field(gt=0)
    t_offset_s: float | None = Field(default=None, ge=0)
    teacher_text: str | None = Field(default=None, max_length=8_000)
    learner_text: str | None = Field(default=None, max_length=8_000)
    emotion: str | None = Field(default=None, max_length=100)
    action: str | None = Field(default=None, max_length=255)
    source: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def require_text(self) -> "TurnCreate":
        if not (self.teacher_text and self.teacher_text.strip()) and not (
            self.learner_text and self.learner_text.strip()
        ):
            raise ValueError("teacher_text or learner_text is required")
        return self


class TurnResponse(TurnCreate):
    turn_id: int
    session_id: int
    created_at: datetime | None = None
