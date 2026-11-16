from __future__ import annotations

from collections import defaultdict, deque
from threading import RLock
from hashlib import sha256
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..services.behavior import BehaviorService
from ..services.contracts import BehaviorResponse
from ..services.microphone import MicrophoneBusyError, MicrophoneError, TranscriptionResult
from ..services.microphone_commands import MicrophoneCommandRouter
from ..services.voice_listening import VoiceListeningService
from ..services.tts import TextToSpeechService
from .students import get_student_registry

router = APIRouter()
logger = logging.getLogger(__name__)


class MicrophoneContextBody(BaseModel):
    session_id: str = Field(default="classroom", min_length=1, max_length=128)
    scene_state: str = Field(default="", max_length=12000)
    session_summary: str = Field(default="", max_length=12000)
    recent_turns: list[dict[str, str]] = Field(default_factory=list, max_length=30)
    default_persona_id: str = Field(default="maria-sandoval", max_length=128)


class MicrophoneStartBody(MicrophoneContextBody):
    device_name: str | None = Field(default=None, max_length=255)
    reset_memory: bool = False


class MicrophoneStopBody(MicrophoneContextBody):
    pass


class MicrophoneControlBody(BaseModel):
    session_id: str = Field(default="classroom", min_length=1, max_length=128)


class MicrophoneStopResponse(BaseModel):
    transcript: str
    target: str
    responses: list[BehaviorResponse]


class MicrophoneResponseQueue(BaseModel):
    items: list[MicrophoneStopResponse]


class ListeningCoordinator:
    def __init__(self) -> None:
        self.router = MicrophoneCommandRouter(
            behavior=BehaviorService(),
            tts=TextToSpeechService(),
            registry=get_student_registry(),
        )
        self._lock = RLock()
        self._lifecycle_lock = RLock()
        self._context = MicrophoneContextBody()
        self._queues: dict[str, deque[MicrophoneStopResponse]] = defaultdict(
            lambda: deque(maxlen=100)
        )
        self._queue_enqueued_at: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=100)
        )
        self._last_error: str | None = None
        self._transcriptions_received = 0
        self._routes_completed = 0
        self._last_transcript: str | None = None
        self._last_route_error: str | None = None
        self._last_response_queue_ms: float | None = None
        self._generation = 0
        self._recent_command_fingerprints: dict[str, float] = {}
        self._executed_command_ids: set[str] = set()
        self._last_command_id: str | None = None
        self._duplicate_commands_dropped = 0
        self._stale_commands_dropped = 0
        self.listener = VoiceListeningService(on_transcription=self._on_transcription)

    def start(self, body: MicrophoneStartBody | None = None) -> dict:
        request = body or MicrophoneStartBody()
        with self._lifecycle_lock:
            if request.reset_memory:
                self.router.registry.reset_session(request.session_id)
            with self._lock:
                self._generation += 1
                self._context = MicrophoneContextBody.model_validate(request.model_dump())
                for response_queue in self._queues.values():
                    response_queue.clear()
                for queue_times in self._queue_enqueued_at.values():
                    queue_times.clear()
                self._recent_command_fingerprints.clear()
                self._executed_command_ids.clear()
                self._last_command_id = None
                self._last_error = None
                self._transcriptions_received = 0
                self._routes_completed = 0
                self._last_transcript = None
                self._last_route_error = None
                self._last_response_queue_ms = None
            status = self.listener.start(request.device_name)
        status["mode"] = "toggle_listen"
        return status

    def stop(self, body: MicrophoneStopBody | None = None) -> MicrophoneStopResponse:
        with self._lifecycle_lock:
            if body is not None:
                with self._lock:
                    self._context = MicrophoneContextBody.model_validate(body.model_dump())
            self.listener.stop()
            with self._lock:
                self._generation += 1
                session_id = self._context.session_id
            items = self.poll(session_id, limit=1, drain=True)
            if items:
                return items[0]
            return MicrophoneStopResponse(transcript="", target="none", responses=[])

    def pause(self, _session_id: str) -> dict:
        return self.listener.pause()

    def resume(self, _session_id: str) -> dict:
        return self.listener.resume()

    def cancel(self) -> dict:
        with self._lifecycle_lock:
            with self._lock:
                self._generation += 1
                for response_queue in self._queues.values():
                    response_queue.clear()
                for queue_times in self._queue_enqueued_at.values():
                    queue_times.clear()
            return self.listener.cancel()

    def reset_memory(self, session_id: str) -> dict:
        with self._lock:
            self.router.registry.reset_session(session_id)
        return {"state": "memory_reset", "session_id": session_id}

    def status(self) -> dict:
        status = self.listener.status()
        with self._lock:
            status["queued_responses"] = sum(len(queue) for queue in self._queues.values())
            status["last_error"] = self._last_error
            status["transcriptions_received"] = self._transcriptions_received
            status["routes_completed"] = self._routes_completed
            status["last_routed_transcript"] = self._last_transcript
            status["last_route_error"] = self._last_route_error
            status["command_generation"] = self._generation
            status["last_command_id"] = self._last_command_id
            status["duplicate_commands_dropped"] = self._duplicate_commands_dropped
            status["stale_commands_dropped"] = self._stale_commands_dropped
            status["tts"] = self.router.tts.diagnostics()
            listener_latency = status.get("latency_ms", {})
            router_latency = self.router.latency_status()
            status["latency_ms"] = {
                **listener_latency,
                **router_latency,
                "response_queue": self._last_response_queue_ms,
            }
        return status

    def poll(self, session_id: str, *, limit: int, drain: bool) -> list[MicrophoneStopResponse]:
        expire_hands = getattr(self.router, "expire_hand_raise_responses", None)
        expired_hand_responses = expire_hands(session_id) if expire_hands else []
        with self._lock:
            response_queue = self._queues[session_id]
            if expired_hand_responses:
                response_queue.append(MicrophoneStopResponse(
                    transcript="",
                    target="classroom",
                    responses=expired_hand_responses,
                ))
                self._queue_enqueued_at[session_id].append(time.perf_counter())
            if drain:
                items = []
                for _ in range(min(limit, len(response_queue))):
                    items.append(response_queue.popleft())
                    if self._queue_enqueued_at[session_id]:
                        enqueued_at = self._queue_enqueued_at[session_id].popleft()
                        self._set_router_latency("response_delivery", round(
                            (time.perf_counter() - enqueued_at) * 1000, 2
                        ))
                return items
            return list(response_queue)[:limit]

    def shutdown(self) -> None:
        self.listener.shutdown()

    def _on_transcription(self, transcript: str) -> None:
        with self._lock:
            context = self._context
            generation = self._generation
            self._transcriptions_received += 1
            self._last_transcript = transcript
            fingerprint = sha256(
                f"{generation}|{context.session_id}|{' '.join(transcript.casefold().split())}".encode()
            ).hexdigest()
            now = time.monotonic()
            previous = self._recent_command_fingerprints.get(fingerprint)
            if previous is not None and now - previous < 2.0:
                self._duplicate_commands_dropped += 1
                logger.warning(
                    "Dropped duplicate microphone command session=%s transcript=%r",
                    context.session_id,
                    transcript,
                )
                return
            self._recent_command_fingerprints[fingerprint] = now
            command_id = f"{generation}-{uuid.uuid4().hex}"
            self._last_command_id = command_id
        logger.info(
            "Routing transcription command_id=%s generation=%s session=%s transcript=%r",
            command_id,
            generation,
            context.session_id,
            transcript,
        )
        speech_end_at = getattr(self.listener, "_last_utterance_end_at", None)
        end_to_end_started = speech_end_at or time.perf_counter()
        try:
            result = self.router.handle(
                transcript,
                session_id=context.session_id,
                scene_state=context.scene_state,
                session_summary=context.session_summary,
                recent_turns=context.recent_turns,
                default_persona_id=context.default_persona_id,
            )
            item = MicrophoneStopResponse(
                transcript=transcript,
                target=result.target,
                responses=result.responses,
            )
            queue_started = time.perf_counter()
            with self._lock:
                if generation != self._generation or context.session_id != self._context.session_id:
                    self._stale_commands_dropped += 1
                    logger.warning(
                        "Dropped stale microphone response command_id=%s session=%s",
                        command_id,
                        context.session_id,
                    )
                    return
                if command_id in self._executed_command_ids:
                    self._duplicate_commands_dropped += 1
                    return
                self._executed_command_ids.add(command_id)
                self._queues[context.session_id].append(item)
                self._queue_enqueued_at[context.session_id].append(time.perf_counter())
                self._routes_completed += 1
                self._last_response_queue_ms = round(
                    (time.perf_counter() - queue_started) * 1000, 2
                )
                total_response_ms = round(
                    (time.perf_counter() - end_to_end_started) * 1000, 2
                )
                self._set_router_latency("response_queue", self._last_response_queue_ms)
                self._set_router_latency("total_response", total_response_ms)
            logger.info(
                "Response queued command_id=%s session=%s target=%s action=%s responses=%s",
                command_id,
                context.session_id,
                result.target,
                [response.action for response in result.responses],
                len(result.responses),
            )
            logger.info(
                "Response latency command_id=%s session=%s stages_ms=%s total_ms=%.2f",
                command_id,
                context.session_id,
                {
                    **self.listener.status().get("latency_ms", {}),
                    **self.router.latency_status(),
                },
                total_response_ms,
            )
        except Exception as error:
            with self._lock:
                self._last_error = type(error).__name__
                self._last_route_error = f"{type(error).__name__}: {error}"
            logger.exception("Microphone command routing failed")

    def _set_router_latency(self, stage: str, value: float | None) -> None:
        """Record optional timing without coupling the coordinator to test doubles."""
        latency = getattr(self.router, "_last_latency_ms", None)
        if isinstance(latency, dict):
            latency[stage] = value


microphone_manager = ListeningCoordinator()


def get_microphone_manager() -> ListeningCoordinator:
    return microphone_manager


def get_microphone_router() -> MicrophoneCommandRouter:
    return microphone_manager.router


@router.post("/microphone/start")
def start_microphone(
    body: MicrophoneStartBody | None = None,
    manager: ListeningCoordinator = Depends(get_microphone_manager),
) -> dict:
    try:
        if isinstance(manager, ListeningCoordinator):
            return manager.start(body)
        return manager.start(body.device_name if body else None)
    except MicrophoneBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except MicrophoneError as error:
        logger.exception("Microphone start failed")
        raise HTTPException(status_code=503, detail="microphone is unavailable") from error


@router.post("/microphone/stop", response_model=MicrophoneStopResponse)
def stop_microphone(
    body: MicrophoneStopBody | None = None,
    manager: ListeningCoordinator = Depends(get_microphone_manager),
    command_router: MicrophoneCommandRouter = Depends(get_microphone_router),
) -> MicrophoneStopResponse:
    try:
        if isinstance(manager, ListeningCoordinator):
            return manager.stop(body)
        transcription: TranscriptionResult = manager.stop()
        result = command_router.handle(
            transcription.text,
            session_id=(body or MicrophoneStopBody()).session_id,
            scene_state=(body or MicrophoneStopBody()).scene_state,
            session_summary=(body or MicrophoneStopBody()).session_summary,
            recent_turns=(body or MicrophoneStopBody()).recent_turns,
            default_persona_id=(body or MicrophoneStopBody()).default_persona_id,
        )
        return MicrophoneStopResponse(
            transcript=transcription.text,
            target=result.target,
            responses=result.responses,
        )
    except MicrophoneBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except MicrophoneError as error:
        logger.exception("Microphone stop failed")
        raise HTTPException(status_code=503, detail="microphone listening failed") from error


@router.get("/microphone/status")
def microphone_status(manager: ListeningCoordinator = Depends(get_microphone_manager)) -> dict:
    return manager.status()


@router.get("/microphone/responses", response_model=MicrophoneResponseQueue)
def microphone_responses(
    session_id: str = Query(default="classroom", min_length=1, max_length=128),
    limit: int = Query(default=10, ge=1, le=50),
    drain: bool = True,
    manager: ListeningCoordinator = Depends(get_microphone_manager),
) -> MicrophoneResponseQueue:
    return MicrophoneResponseQueue(items=manager.poll(session_id, limit=limit, drain=drain))


@router.post("/microphone/pause")
def pause_microphone(
    body: MicrophoneControlBody | None = None,
    manager: ListeningCoordinator = Depends(get_microphone_manager),
) -> dict:
    try:
        return manager.pause((body or MicrophoneControlBody()).session_id)
    except MicrophoneError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/microphone/resume")
def resume_microphone(
    body: MicrophoneControlBody | None = None,
    manager: ListeningCoordinator = Depends(get_microphone_manager),
) -> dict:
    try:
        return manager.resume((body or MicrophoneControlBody()).session_id)
    except MicrophoneError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/microphone/cancel")
def cancel_microphone(manager: ListeningCoordinator = Depends(get_microphone_manager)) -> dict:
    try:
        return manager.cancel()
    except MicrophoneError as error:
        raise HTTPException(status_code=503, detail="microphone could not be released") from error


@router.post("/microphone/reset")
def reset_microphone_memory(
    body: MicrophoneControlBody | None = None,
    manager: ListeningCoordinator = Depends(get_microphone_manager),
) -> dict:
    return manager.reset_memory((body or MicrophoneControlBody()).session_id)
