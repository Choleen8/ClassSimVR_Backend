from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..services.behavior import BehaviorService
from ..services.contracts import BehaviorRequest, BehaviorResponse
from ..services.personas import (
    PersonaService,
    PersonaUnavailableError,
    SessionPersonaNotFoundError,
)
from ..services.tts import TextToSpeechService

from ..services.session_contracts import ClassroomContext, ELEMENTARY_GRADES

router = APIRouter()


class BehaviorTurnBody(BaseModel):
    persona_id: str = Field(min_length=1, max_length=128)
    persona_name: str = Field(min_length=1, max_length=120)
    persona_profile: str = Field(default="", max_length=12000)
    language: str = Field(default="English", max_length=40)
    trainee_input: str = Field(min_length=1, max_length=4000)
    scene_state: str = Field(default="", max_length=12000)
    session_summary: str = Field(default="", max_length=12000)
    recent_turns: list[dict[str, str]] = Field(default_factory=list, max_length=30)
    grade_level: str | None = Field(default=None, max_length=20)

    @field_validator("trainee_input")
    @classmethod
    def input_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trainee_input must not be blank")
        return value.strip()

    @field_validator("persona_id", "persona_name")
    @classmethod
    def identifiers_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("persona identifiers must not be blank")
        return value.strip()

    @field_validator("recent_turns")
    @classmethod
    def recent_turns_must_be_bounded(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        for turn in value:
            if len(turn.get("speaker", "")) > 80 or len(turn.get("text", "")) > 2_000:
                raise ValueError("recent turn fields exceed limits")
        return value


class VersionedBehaviorTurnBody(BaseModel):
    trainee_input: str = Field(min_length=1, max_length=4000)
    scene_state: str = Field(default="", max_length=12000)
    recent_turns: list[dict[str, str]] = Field(default_factory=list, max_length=30)

    @field_validator("trainee_input")
    @classmethod
    def input_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trainee_input must not be blank")
        return value.strip()

    @field_validator("recent_turns")
    @classmethod
    def recent_turns_must_be_bounded(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        for turn in value:
            if len(turn.get("speaker", "")) > 80 or len(turn.get("text", "")) > 2_000:
                raise ValueError("recent turn fields exceed limits")
        return value


def get_behavior_service() -> BehaviorService:
    return BehaviorService()


def get_persona_service() -> PersonaService:
    return PersonaService()


def get_tts_service() -> TextToSpeechService:
    return TextToSpeechService()


def _attach_audio(
    reply: BehaviorResponse | dict,
    *,
    tts: TextToSpeechService,
    session_id: str,
    persona_id: str,
    persona_name: str | None = None,
) -> BehaviorResponse:
    if not isinstance(reply, BehaviorResponse):
        reply = BehaviorResponse.model_validate(reply)
    if not reply.speak or not reply.dialogue:
        return reply.model_copy(update={"audio_url": None})
    try:
        audio_url = tts.synthesize(
            reply.dialogue,
            f"{session_id}-{persona_id}",
            persona_id=persona_id,
            persona_name=persona_name,
        )
        return reply.model_copy(update={"audio_url": audio_url})
    except Exception as error:
        # Keep dialogue, subtitles, and animation fields usable if TTS is
        # unavailable. Do not turn a voice-provider failure into a turn failure.
        print(f"Edge TTS request failed: {type(error).__name__}: {error}")
        return reply.model_copy(update={"audio_url": None})


@router.post("/sessions/{session_id}/turn", response_model=BehaviorResponse)
def create_behavior_turn(
    session_id: str,
    body: BehaviorTurnBody,
    service: BehaviorService = Depends(get_behavior_service),
    tts: TextToSpeechService = Depends(get_tts_service),
) -> BehaviorResponse:
    request = BehaviorRequest(session_id=session_id, **body.model_dump())
    reply = service.generate_reply(request)
    return _attach_audio(
        reply,
        tts=tts,
        session_id=session_id,
        persona_id=request.persona_id,
        persona_name=request.persona_name,
    )


@router.post(
    "/api/v1/sessions/{session_id}/personas/{persona_id}/turn",
    response_model=BehaviorResponse,
)
def create_versioned_behavior_turn(
    session_id: int,
    persona_id: int,
    body: VersionedBehaviorTurnBody,
    db: DbSession = Depends(get_session),
    persona_service: PersonaService = Depends(get_persona_service),
    service: BehaviorService = Depends(get_behavior_service),
    tts: TextToSpeechService = Depends(get_tts_service),
) -> BehaviorResponse:
    try:
        assignment = persona_service.get_assigned_persona(db, session_id, persona_id)
    except (SessionPersonaNotFoundError, PersonaUnavailableError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    persona = assignment.persona
    profile = "; ".join(
        value
        for value in (
            f"traits={', '.join(persona.personality_traits)}" if persona.personality_traits else "",
            f"triggers={', '.join(persona.behavior_triggers)}" if persona.behavior_triggers else "",
            f"baseline={persona.academic_baseline}" if persona.academic_baseline else "",
            f"response_style={persona.response_style}" if persona.response_style else "",
            f"participation={persona.participation_level}",
            f"state={assignment.current_state}",
            f"emotion={assignment.current_emotion}",
        )
        if value
    )
    session = assignment.session
    context = None
    if session.subject and session.grade in ELEMENTARY_GRADES:
        try:
            context = ClassroomContext.from_selection(session.grade, session.subject, session.session_mode)
        except ValueError as error:
            raise HTTPException(status_code=409, detail="classroom session has no valid subject context") from error
    request = BehaviorRequest(
        classroom_context=context,
        session_id=str(session_id),
        persona_id=str(persona_id),
        persona_name=persona.name,
        persona_profile=profile,
        language=persona.language,
        trainee_input=body.trainee_input,
        scene_state=body.scene_state,
        session_summary=assignment.state_summary or "",
        recent_turns=body.recent_turns,
    )
    reply = service.generate_reply(request)
    return _attach_audio(
        reply,
        tts=tts,
        session_id=str(session_id),
        persona_id=str(persona_id),
        persona_name=persona.name,
    )
