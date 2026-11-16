from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .persona_contracts import AssignedPersonaResponse


MIN_CLASS_SIZE = 15
MAX_CLASS_SIZE = 30


class StudentGenerationRequest(BaseModel):
    class_size: int = Field(ge=MIN_CLASS_SIZE, le=MAX_CLASS_SIZE)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)


class SessionCreate(BaseModel):
    trainee_id: int = Field(gt=0)
    grade: str | None = Field(default=None, max_length=50)
    competency: str | None = Field(default=None, max_length=4_000)
    lesson_plan: str | None = Field(default=None, max_length=12_000)
    answer_key: str | None = Field(default=None, max_length=12_000)
    class_size: int | None = Field(default=None, ge=MIN_CLASS_SIZE, le=MAX_CLASS_SIZE)
    difficulty: str | None = Field(default=None, max_length=50)


class SessionResponse(BaseModel):
    session_id: int
    trainee_id: int
    grade: str | None
    competency: str | None
    lesson_plan: str | None
    answer_key: str | None
    class_size: int | None
    difficulty: str | None
    status: str
    created_at: datetime


class TraineeHistoryResponse(BaseModel):
    session_id: int
    trainee_id: int
    grade: str | None
    competency: str | None
    class_size: int | None
    difficulty: str | None
    status: str
    assessment_status: str
    assessment_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    report_available: bool
    summary: str | None
    next_focus: str | None


class GeneratedClassResponse(BaseModel):
    session_id: int
    class_size: int
    seed: int
    students: list[AssignedPersonaResponse]


class SessionLifecycleResponse(BaseModel):
    session_id: int
    trainee_id: int
    status: str
    class_size: int | None
    started_at: object | None
    paused_at: object | None
    completed_at: object | None
