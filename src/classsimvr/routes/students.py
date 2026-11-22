from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..services.student_registry import (
    DEFAULT_STUDENT_ACTIONS,
    DuplicateStudentError,
    StudentNotFoundError,
    StudentProfile,
    StudentRegistry,
    StudentSessionState,
)

router = APIRouter()
student_registry = StudentRegistry.with_defaults()


class StudentRegistrationBody(BaseModel):
    student_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    personality: str = Field(default="a respectful Filipino VR student", max_length=2_000)
    language: str = Field(default="English", max_length=40)
    voice: str | None = Field(default=None, max_length=120)
    initial_state: str = Field(default="engaged", max_length=50)
    knowledge_level: str = Field(default="intermediate", max_length=40)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    willingness: float = Field(default=0.5, ge=0.0, le=1.0)
    gender: str = Field(default="unspecified", max_length=30)
    academic_behavior_enabled: bool = False
    supported_actions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_STUDENT_ACTIONS), max_length=20
    )


class StudentStateBody(BaseModel):
    current_state: str | None = Field(default=None, max_length=50)
    emotion: str | None = Field(default=None, max_length=80)
    summary: str | None = Field(default=None, max_length=4_000)
    data: dict[str, object] | None = None


class StudentResponse(BaseModel):
    student_id: str
    name: str
    aliases: list[str]
    personality: str
    language: str
    voice: str | None
    initial_state: str
    knowledge_level: str
    confidence: float
    willingness: float


class StudentStateResponse(BaseModel):
    student_id: str
    session_id: str
    current_state: str
    emotion: str
    summary: str
    data: dict[str, object]


def get_student_registry() -> StudentRegistry:
    return student_registry


def _student_response(profile: StudentProfile) -> StudentResponse:
    return StudentResponse(
        student_id=profile.student_id,
        name=profile.name,
        aliases=list(profile.aliases),
        personality=profile.personality,
        language=profile.language,
        voice=profile.voice,
        initial_state=profile.initial_state,
        knowledge_level=profile.knowledge_level,
        confidence=profile.confidence,
        willingness=profile.willingness,
    )


def _state_response(session_id: str, profile: StudentProfile, state: StudentSessionState) -> StudentStateResponse:
    return StudentStateResponse(
        student_id=profile.student_id,
        session_id=session_id,
        current_state=state.current_state,
        emotion=state.emotion,
        summary=state.summary,
        data=state.data,
    )


@router.post("/students/register", response_model=StudentResponse, status_code=status.HTTP_201_CREATED)
def register_student(
    body: StudentRegistrationBody,
    registry: StudentRegistry = Depends(get_student_registry),
) -> StudentResponse:
    try:
        profile = registry.register(
            StudentProfile(
                student_id=body.student_id,
                name=body.name,
                aliases=tuple(body.aliases),
                personality=body.personality,
                language=body.language,
                voice=body.voice,
                initial_state=body.initial_state,
                knowledge_level=body.knowledge_level,
                confidence=body.confidence,
                willingness=body.willingness,
                gender=body.gender,
                supported_actions=tuple(body.supported_actions),
                academic_behavior_enabled=body.academic_behavior_enabled,
            )
        )
    except DuplicateStudentError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _student_response(profile)


@router.get("/students", response_model=list[StudentResponse])
def list_students(registry: StudentRegistry = Depends(get_student_registry)) -> list[StudentResponse]:
    return [_student_response(profile) for profile in registry.list_students()]


@router.get("/students/{student_id}/sessions/{session_id}/state", response_model=StudentStateResponse)
def get_student_state(
    student_id: str,
    session_id: str,
    registry: StudentRegistry = Depends(get_student_registry),
) -> StudentStateResponse:
    try:
        profile = registry.get(student_id)
        state = registry.get_session_state(session_id, student_id)
    except StudentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _state_response(session_id, profile, state)


@router.patch("/students/{student_id}/sessions/{session_id}/state", response_model=StudentStateResponse)
def update_student_state(
    student_id: str,
    session_id: str,
    body: StudentStateBody,
    registry: StudentRegistry = Depends(get_student_registry),
) -> StudentStateResponse:
    try:
        profile = registry.get(student_id)
        state = registry.update_session_state(
            session_id,
            student_id,
            **body.model_dump(exclude_unset=True),
        )
    except StudentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _state_response(session_id, profile, state)
