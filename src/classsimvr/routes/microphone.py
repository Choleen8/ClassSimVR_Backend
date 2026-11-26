from __future__ import annotations

from collections import defaultdict, deque
from threading import RLock
from hashlib import sha256
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..models import Session
from ..services.auth import bearer_scheme, get_current_account
from ..services.session_contracts import ClassroomContext
from ..services.transcript_assessment import record_microphone_events

from ..services.behavior import BehaviorService
from ..services.contracts import BehaviorResponse
from ..services.microphone import MicrophoneBusyError, MicrophoneError, TranscriptionResult
from ..services.microphone_commands import MicrophoneCommandRouter
from ..services.voice_listening import VoiceListeningService
from ..services.tts import TextToSpeechService
from .students import get_student_registry

router = APIRouter()
logger = logging.getLogger(__name__)


def _is_classroom_session(session_id: str) -> bool:
    return session_id.startswith("db:")


def _classroom_context(
    session_id: str, credentials: HTTPAuthorizationCredentials | None, db: DbSession,
    *, require_running: bool = False,
) -> ClassroomContext | None:
    if not _is_classroom_session(session_id):
        return None
    value = session_id[3:]
    if not value.isascii() or not value.isdecimal() or len(value) > 10:
        raise HTTPException(status_code=409, detail="classroom session is not initialized")
    key = int(value)
    if not 0 < key <= 2**31 - 1 or str(key) != value:
        raise HTTPException(status_code=409, detail="invalid classroom session identifier")
    try:
        account = get_current_account(credentials, db)
        session = db.get(Session, key)
        if session is None or session.trainee_id != account.trainee_id:
            raise HTTPException(status_code=404, detail="classroom session was not found")
        if require_running and session.status != "running":
            raise HTTPException(status_code=409, detail="classroom session must be running")
        try:
            return ClassroomContext.from_selection(session.grade, session.subject, session.session_mode)
        except ValueError as error:
            raise HTTPException(status_code=409, detail="classroom session has no valid subject context") from error
    except SQLAlchemyError as error:
        logger.warning("Classroom microphone session lookup failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="classroom session is unavailable") from error


class MicrophoneContextBody(BaseModel):
    session_id: str = Field(default="classroom", min_length=1, max_length=128)
    selected_grade_level: str | None = Field(default=None, max_length=20)
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
        self._classroom_context: ClassroomContext | None = None
        self._listening = False
        self._queues: dict[str, deque[MicrophoneStopResponse]] = defaultdict(
            lambda: deque(maxlen=100)
        )
        self._queue_enqueued_at: dict[str, deque[tuple[float, str | None, float | None]]] = defaultdict(
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
        self._assessment_evidence_failures: set[str] = set()
        self._duplicate_commands_dropped = 0
        self._stale_commands_dropped = 0
        self.listener = VoiceListeningService(
            on_transcription=self._on_transcription,
            student_names=tuple(student.name for student in self.router.registry.list_students()),
        )

    def start(
        self, body: MicrophoneStartBody | None = None, *, classroom_context: ClassroomContext | None = None,
    ) -> dict:
        request = body or MicrophoneStartBody()
        with self._lifecycle_lock:
            if self._listening:
                raise MicrophoneBusyError("microphone listening is already enabled")
            if _is_classroom_session(request.session_id) and classroom_context is None:
                raise MicrophoneBusyError("classroom session is not initialized")
            selected_grade_level = self.router.registry.set_selected_grade_level(
                request.session_id, classroom_context.grade if classroom_context else request.selected_grade_level
            )
            request = request.model_copy(update={"selected_grade_level": selected_grade_level})
            if request.reset_memory:
                self.router.registry.reset_session(request.session_id)
            self.listener.set_student_names(
                tuple(student.name for student in self.router.registry.list_students())
            )
            with self._lock:
                self._generation += 1
                self._context = MicrophoneContextBody.model_validate(request.model_dump())
                self._classroom_context = classroom_context
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
            self._listening = True
        status["mode"] = "toggle_listen"
        return status

    def stop(self, body: MicrophoneStopBody | None = None) -> MicrophoneStopResponse:
        with self._lifecycle_lock:
            session_id = body.session_id if body else self._context.session_id
            self._check_capture_owner(session_id)
            if body is not None and not _is_classroom_session(session_id):
                with self._lock:
                    self._context = MicrophoneContextBody.model_validate(body.model_dump())
                    self._classroom_context = None
            try:
                self.listener.stop()
            finally:
                self._listening = False
            with self._lock:
                self._generation += 1
            items = self.poll(session_id, limit=1, drain=True)
            if items:
                return items[0]
            return MicrophoneStopResponse(transcript="", target="none", responses=[])

    def _check_capture_owner(self, session_id: str) -> None:
        owner = self._context.session_id
        if self._listening and session_id != owner and (
            _is_classroom_session(owner) or _is_classroom_session(session_id)
        ):
            raise MicrophoneBusyError("microphone belongs to another classroom session")

    def pause(self, session_id: str) -> dict:
        with self._lifecycle_lock:
            self._check_capture_owner(session_id)
            return self.listener.pause()

    def resume(self, session_id: str) -> dict:
        with self._lifecycle_lock:
            self._check_capture_owner(session_id)
            return self.listener.resume()

    def cancel(self, session_id: str = "classroom") -> dict:
        with self._lifecycle_lock:
            self._check_capture_owner(session_id)
            if _is_classroom_session(session_id) and session_id != self._context.session_id:
                return {"state": "idle"}
            with self._lock:
                self._generation += 1
                for response_queue in self._queues.values():
                    response_queue.clear()
                for queue_times in self._queue_enqueued_at.values():
                    queue_times.clear()
            try:
                return self.listener.cancel()
            finally:
                self._listening = False

    def reset_memory(self, session_id: str) -> dict:
        with self._lock:
            self.router.registry.reset_session(session_id)
        return {"state": "memory_reset", "session_id": session_id}

    def status(self) -> dict:
        status = self.listener.status()
        with self._lock:
            status["queued_responses"] = sum(len(queue) for queue in self._queues.values())
            status["selected_grade_level"] = self.router.registry.get_selected_grade_level(
                self._context.session_id
            )
            status["last_error"] = self._last_error
            status["transcriptions_received"] = self._transcriptions_received
            status["routes_completed"] = self._routes_completed
            if self._classroom_context:
                # The legacy status probe is unauthenticated and only needs capture state.
                status["last_transcript"] = None
            status["last_routed_transcript"] = None if self._classroom_context else self._last_transcript
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
                self._queue_enqueued_at[session_id].append((time.perf_counter(), None, None))
            if drain:
                items = []
                for _ in range(min(limit, len(response_queue))):
                    items.append(response_queue.popleft())
                    if self._queue_enqueued_at[session_id]:
                        enqueued_at, command_id, last_speech_at = self._queue_enqueued_at[session_id].popleft()
                        now = time.perf_counter()
                        delivery_ms = round((now - enqueued_at) * 1000, 2)
                        self._set_router_latency("response_delivery", delivery_ms)
                        logger.info("Response drained command_id=%s session=%s queue_to_poll_ms=%s speech_to_poll_ms=%s",
                                    command_id, session_id, delivery_ms,
                                    round((now - last_speech_at) * 1000, 2) if last_speech_at is not None else None)
                return items
            return list(response_queue)[:limit]

    def shutdown(self) -> None:
        self.listener.shutdown()

    def _on_transcription(self, transcript: str) -> None:
        with self._lock:
            context = self._context
            classroom_context = self._classroom_context
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
                    "Dropped duplicate microphone command session=%s",
                    context.session_id,
                )
                return
            self._recent_command_fingerprints[fingerprint] = now
            command_id = f"{generation}-{uuid.uuid4().hex}"
            self._last_command_id = command_id
        logger.info(
            "Routing transcription command_id=%s generation=%s session=%s",
            command_id,
            generation,
            context.session_id,
        )
        speech_end_at, capture_stages = getattr(
            self.listener, "current_command_timing", lambda: (None, {})
        )()
        end_to_end_started = speech_end_at or time.perf_counter()
        assess = classroom_context is not None and classroom_context.session_mode == "assessment"
        first = last = None
        if assess:
            bounds = self.listener.current_speech_bounds()
            if isinstance(bounds, tuple):
                first, last = bounds
            if not record_microphone_events(context.session_id, first, last, transcript=transcript):
                with self._lock:
                    self._assessment_evidence_failures.add(context.session_id)
        try:
            result = self.router.handle(
                transcript,
                session_id=context.session_id,
                scene_state=context.scene_state,
                session_summary=context.session_summary,
                recent_turns=context.recent_turns,
                default_persona_id=context.default_persona_id,
                selected_grade_level=context.selected_grade_level,
                **({"classroom_context": classroom_context} if classroom_context else {}),
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
                self._queue_enqueued_at[context.session_id].append((time.perf_counter(), command_id, speech_end_at))
                self._routes_completed += 1
                self._last_response_queue_ms = round(
                    (time.perf_counter() - queue_started) * 1000, 2
                )
                total_response_ms = round(
                    (time.perf_counter() - end_to_end_started) * 1000, 2
                )
                self._set_router_latency("response_queue", self._last_response_queue_ms)
                self._set_router_latency("total_response", total_response_ms)
            if assess:
                if not record_microphone_events(context.session_id, first, last, queued_at=queue_started, responses=result.responses):
                    with self._lock:
                        self._assessment_evidence_failures.add(context.session_id)
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
                    **capture_stages,
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

    def drain_assessment_evidence(self, session_id: str, cutoff: float) -> bool:
        deadline = time.perf_counter() + 25.0
        while time.perf_counter() < deadline:
            with self._lock:
                if session_id in self._assessment_evidence_failures:
                    return False
                if self._context.session_id != session_id:
                    return True
            if not self.listener.has_pending_assessment_audio(cutoff):
                return True
            time.sleep(0.05)
        return False


microphone_manager = ListeningCoordinator()


def get_microphone_manager() -> ListeningCoordinator:
    return microphone_manager


def get_microphone_router() -> MicrophoneCommandRouter:
    return microphone_manager.router


@router.post("/microphone/start")
def start_microphone(
    body: MicrophoneStartBody | None = None,
    manager: ListeningCoordinator = Depends(get_microphone_manager),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: DbSession = Depends(get_session),
) -> dict:
    classroom = _classroom_context((body or MicrophoneStartBody()).session_id, credentials, db, require_running=True)
    try:
        if isinstance(manager, ListeningCoordinator):
            return manager.start(body, classroom_context=classroom)
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
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: DbSession = Depends(get_session),
) -> MicrophoneStopResponse:
    resolved_body = body or MicrophoneStopBody()
    _classroom_context(resolved_body.session_id, credentials, db)
    try:
        if isinstance(manager, ListeningCoordinator):
            return manager.stop(resolved_body)
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
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: DbSession = Depends(get_session),
) -> MicrophoneResponseQueue:
    _classroom_context(session_id, credentials, db)
    return MicrophoneResponseQueue(items=manager.poll(session_id, limit=limit, drain=drain))


@router.post("/microphone/pause")
def pause_microphone(
    body: MicrophoneControlBody | None = None,
    manager: ListeningCoordinator = Depends(get_microphone_manager),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: DbSession = Depends(get_session),
) -> dict:
    session_id = (body or MicrophoneControlBody()).session_id
    _classroom_context(session_id, credentials, db)
    try:
        return manager.pause(session_id)
    except MicrophoneError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/microphone/resume")
def resume_microphone(
    body: MicrophoneControlBody | None = None,
    manager: ListeningCoordinator = Depends(get_microphone_manager),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: DbSession = Depends(get_session),
) -> dict:
    session_id = (body or MicrophoneControlBody()).session_id
    _classroom_context(session_id, credentials, db)
    try:
        return manager.resume(session_id)
    except MicrophoneError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/microphone/cancel")
def cancel_microphone(
    manager: ListeningCoordinator = Depends(get_microphone_manager),
    body: MicrophoneControlBody | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: DbSession = Depends(get_session),
) -> dict:
    session_id = (body or MicrophoneControlBody()).session_id
    _classroom_context(session_id, credentials, db)
    try:
        if isinstance(manager, ListeningCoordinator):
            return manager.cancel(session_id)
        return manager.cancel()
    except MicrophoneBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except MicrophoneError as error:
        raise HTTPException(status_code=503, detail="microphone could not be released") from error


@router.post("/microphone/reset")
def reset_microphone_memory(
    body: MicrophoneControlBody | None = None,
    manager: ListeningCoordinator = Depends(get_microphone_manager),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: DbSession = Depends(get_session),
) -> dict:
    session_id = (body or MicrophoneControlBody()).session_id
    _classroom_context(session_id, credentials, db)
    return manager.reset_memory(session_id)
