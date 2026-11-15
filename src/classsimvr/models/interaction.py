from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Consent(Base):
    __tablename__ = "consent"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), primary_key=True
    )
    video_recording: Mapped[str | None] = mapped_column(String(255))
    purpose: Mapped[str | None] = mapped_column(Text)
    notice_version: Mapped[str | None] = mapped_column(String(50))
    given_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped["Session"] = relationship("Session", back_populates="consent")


class Recording(Base):
    __tablename__ = "recording"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), primary_key=True
    )
    file_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float | None] = mapped_column(Float)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped["Session"] = relationship("Session", back_populates="recording")


class Report(Base):
    __tablename__ = "report"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), primary_key=True
    )
    summary: Mapped[str | None] = mapped_column(Text)
    next_focus: Mapped[str | None] = mapped_column(Text)
    model_assess: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    session: Mapped["Session"] = relationship("Session", back_populates="report")
    indicator_scores: Mapped[list["IndicatorScore"]] = relationship(
        "IndicatorScore", back_populates="report", cascade="all, delete-orphan"
    )


class Turn(Base):
    __tablename__ = "turn"

    turn_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False
    )
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("persona.persona_id"), nullable=False
    )
    t_offset_s: Mapped[float | None] = mapped_column(Float)
    teacher_text: Mapped[str | None] = mapped_column(Text)
    learner_text: Mapped[str | None] = mapped_column(Text)
    emotion: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(100))

    session: Mapped["Session"] = relationship("Session", back_populates="turns")
    persona: Mapped["Persona"] = relationship("Persona", back_populates="turns")


class SessionPersona(Base):
    __tablename__ = "session_persona"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), primary_key=True
    )
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("persona.persona_id", ondelete="CASCADE"), primary_key=True
    )
    initial_state: Mapped[str] = mapped_column(
        String(50), default="engaged", nullable=False
    )
    current_state: Mapped[str] = mapped_column(
        String(50), default="engaged", nullable=False
    )
    current_emotion: Mapped[str] = mapped_column(
        String(80), default="neutral", nullable=False
    )
    state_summary: Mapped[str | None] = mapped_column(Text)
    state_data: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    session: Mapped["Session"] = relationship(
        "Session", back_populates="session_personas", overlaps="personas"
    )
    persona: Mapped["Persona"] = relationship(
        "Persona", back_populates="session_personas", overlaps="sessions,personas"
    )


class SessionBti(Base):
    __tablename__ = "session_bti"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), primary_key=True
    )
    bti_id: Mapped[int] = mapped_column(
        ForeignKey("bti.bti_id", ondelete="CASCADE"), primary_key=True
    )

    session: Mapped["Session"] = relationship(
        "Session", back_populates="session_btis", overlaps="btis"
    )
    bti: Mapped["Bti"] = relationship(
        "Bti", back_populates="session_btis", overlaps="sessions,btis"
    )
