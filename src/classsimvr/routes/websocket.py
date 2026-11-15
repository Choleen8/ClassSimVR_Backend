from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.orm import Session as DbSession
from starlette.concurrency import run_in_threadpool

from ..database import get_session
from ..services.behavior import BehaviorService
from ..services.contracts import BehaviorRequest
from ..services.personas import (
    PersonaService,
    PersonaUnavailableError,
    SessionPersonaNotFoundError,
)
from ..services.tts import TextToSpeechService
from ..services.websocket_contracts import (
    WebSocketMessage,
    connected_event,
    error_event,
    pong_event,
    response_event,
)
from .behavior import _attach_audio

router = APIRouter()


def get_websocket_behavior_service() -> BehaviorService:
    return BehaviorService()


def get_websocket_persona_service() -> PersonaService:
    return PersonaService()


def get_websocket_tts_service() -> TextToSpeechService:
    return TextToSpeechService()


def _profile_for_assignment(assignment) -> str:
    persona = assignment.persona
    return "; ".join(
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


def _generate_response(
    message: WebSocketMessage,
    *,
    session_id: int,
    assignment,
    behavior: BehaviorService,
    tts: TextToSpeechService,
):
    persona = assignment.persona
    request = BehaviorRequest(
        session_id=str(session_id),
        persona_id=str(persona.persona_id),
        persona_name=persona.name,
        persona_profile=_profile_for_assignment(assignment),
        language=persona.language or "English",
        trainee_input=message.trainee_input or "",
        scene_state=message.scene_state,
        session_summary=assignment.state_summary or "",
        recent_turns=message.recent_turns,
    )
    reply = behavior.generate_reply(request)
    return _attach_audio(
        reply,
        tts=tts,
        session_id=str(session_id),
        persona_id=str(persona.persona_id),
        persona_name=persona.name,
    )


@router.websocket("/api/v1/ws/sessions/{session_id}")
async def session_websocket(
    websocket: WebSocket,
    session_id: int,
    db: DbSession = Depends(get_session),
    persona_service: PersonaService = Depends(get_websocket_persona_service),
    behavior: BehaviorService = Depends(get_websocket_behavior_service),
    tts: TextToSpeechService = Depends(get_websocket_tts_service),
) -> None:
    await websocket.accept()
    await websocket.send_json(connected_event(session_id))

    try:
        while True:
            raw_message = await websocket.receive_json()
            try:
                message = WebSocketMessage.model_validate(raw_message)
            except ValidationError as error:
                await websocket.send_json(
                    error_event("invalid_message", error.errors()[0]["msg"])
                )
                continue

            if message.type == "ping":
                await websocket.send_json(pong_event())
                continue

            try:
                assignment = persona_service.get_assigned_persona(
                    db, session_id, message.persona_id or 0
                )
            except (SessionPersonaNotFoundError, PersonaUnavailableError) as error:
                await websocket.send_json(error_event("persona_not_assigned", str(error)))
                continue

            reply = await run_in_threadpool(
                _generate_response,
                message,
                session_id=session_id,
                assignment=assignment,
                behavior=behavior,
                tts=tts,
            )
            await websocket.send_json(response_event(reply.model_dump()))
    except WebSocketDisconnect:
        return
