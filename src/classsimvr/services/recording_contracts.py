from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ConsentRequest(BaseModel):
    video_recording: bool
    purpose: str = Field(min_length=1, max_length=2_000)
    notice_version: str = Field(min_length=1, max_length=50)


class ConsentResponse(BaseModel):
    session_id: int
    video_recording: bool
    given_at: datetime | None
    purpose: str | None
    notice_version: str | None
    withdrawn_at: datetime | None


class RecordingStartRequest(BaseModel):
    mime_type: str = Field(min_length=1, max_length=100)
    storage_ref: str = Field(min_length=1, max_length=2_000)


class RecordingCompleteRequest(BaseModel):
    duration_s: float = Field(gt=0, le=86_400)
    storage_ref: str = Field(min_length=1, max_length=2_000)
    size_bytes: int | None = Field(default=None, ge=0, le=10_000_000_000)


class RecordingResponse(BaseModel):
    session_id: int
    status: str
    mime_type: str | None
    storage_ref: str | None
    duration_s: float | None
    size_bytes: int | None
    started_at: datetime | None
    completed_at: datetime | None
