from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session as DbSession

from ..models import Consent, Recording, Session
from .recording_contracts import (
    ConsentRequest,
    RecordingCompleteRequest,
    RecordingStartRequest,
)
from .personas import SessionNotFoundError


class ConsentRequiredError(ValueError):
    pass


class RecordingStateError(ValueError):
    pass


class RecordingService:
    def __init__(self, storage_dir: str | Path | None = None) -> None:
        configured_dir = storage_dir or os.getenv("CLASSSIMVR_RECORDING_DIR", "storage/recordings")
        self.storage_dir = Path(configured_dir).resolve()

    def _session(self, db: DbSession, session_id: int) -> Session:
        session = db.get(Session, session_id)
        if session is None:
            raise SessionNotFoundError(f"session {session_id} was not found")
        return session

    def set_consent(
        self, db: DbSession, session_id: int, data: ConsentRequest
    ) -> Consent:
        self._session(db, session_id)
        consent = db.get(Consent, session_id)
        if consent is None:
            consent = Consent(session_id=session_id)
            db.add(consent)
        consent.video_recording = "granted" if data.video_recording else "denied"
        consent.purpose = data.purpose
        consent.notice_version = data.notice_version
        consent.given_at = datetime.now(timezone.utc)
        consent.withdrawn_at = None if data.video_recording else datetime.now(timezone.utc)
        db.commit()
        db.refresh(consent)
        return consent

    def start_recording(
        self, db: DbSession, session_id: int, data: RecordingStartRequest
    ) -> Recording:
        self._session(db, session_id)
        consent = db.get(Consent, session_id)
        if (
            consent is None
            or consent.video_recording != "granted"
            or consent.withdrawn_at is not None
        ):
            raise ConsentRequiredError("video recording consent is required")
        recording = db.get(Recording, session_id)
        if recording is not None and recording.status == "recording":
            raise RecordingStateError("recording is already active")
        if recording is None:
            recording = Recording(session_id=session_id)
            db.add(recording)
        recording.status = "recording"
        recording.mime_type = data.mime_type
        recording.file_path = data.storage_ref
        recording.started_at = datetime.now(timezone.utc)
        recording.completed_at = None
        db.commit()
        db.refresh(recording)
        return recording

    def withdraw_consent(self, db: DbSession, session_id: int) -> Consent:
        self._session(db, session_id)
        consent = db.get(Consent, session_id)
        if consent is None:
            raise ConsentRequiredError("no recording consent exists")
        consent.video_recording = "denied"
        consent.withdrawn_at = datetime.now(timezone.utc)
        session = self._session(db, session_id)
        session.assessment_status = "not_gradable"
        session.assessment_message = (
            "Recording consent was withdrawn; this session cannot be graded."
        )
        db.commit()
        db.refresh(consent)
        return consent

    def complete_recording(
        self, db: DbSession, session_id: int, data: RecordingCompleteRequest
    ) -> Recording:
        self._session(db, session_id)
        recording = db.get(Recording, session_id)
        if recording is None or recording.status != "recording":
            raise RecordingStateError("no active recording exists")
        recording.status = "completed"
        recording.file_path = data.storage_ref
        recording.duration_s = data.duration_s
        recording.size_bytes = data.size_bytes
        recording.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(recording)
        return recording

    async def save_upload(
        self,
        db: DbSession,
        session_id: int,
        upload,
        duration_s: float,
    ) -> Recording:
        self._session(db, session_id)
        consent = db.get(Consent, session_id)
        if (
            consent is None
            or consent.video_recording != "granted"
            or consent.withdrawn_at is not None
        ):
            raise ConsentRequiredError("video recording consent is required")

        recording = db.get(Recording, session_id)
        if recording is None or recording.status != "recording":
            raise RecordingStateError("start recording before uploading a video")

        suffix = Path(upload.filename or "recording.webm").suffix.lower()
        if not suffix or len(suffix) > 10 or not suffix[1:].isalnum():
            suffix = ".webm"
        relative_path = Path(f"session-{session_id}-{uuid4().hex}{suffix}")
        target = self.storage_dir / relative_path
        temporary = target.with_suffix(target.suffix + ".part")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        size_bytes = 0

        try:
            with temporary.open("wb") as destination:
                while chunk := await upload.read(1024 * 1024):
                    size_bytes += len(chunk)
                    destination.write(chunk)
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        recording.status = "completed"
        recording.file_path = str(relative_path)
        recording.mime_type = upload.content_type or recording.mime_type
        recording.size_bytes = size_bytes
        recording.duration_s = duration_s
        recording.completed_at = datetime.now(timezone.utc)
        recording.uploaded_at = recording.completed_at
        db.commit()
        db.refresh(recording)
        return recording
