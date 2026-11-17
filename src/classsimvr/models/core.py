from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Trainee(Base):
    __tablename__ = "trainee"

    trainee_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    sessions: Mapped[list["Session"]] = relationship(
        "Session",
        back_populates="trainee", cascade="all, delete-orphan"
    )
    auth_account: Mapped["AuthAccount | None"] = relationship(
        "AuthAccount", back_populates="trainee", uselist=False,
        cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "session"

    session_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trainee_id: Mapped[int] = mapped_column(
        ForeignKey("trainee.trainee_id", ondelete="CASCADE"), nullable=False
    )
    grade: Mapped[str | None] = mapped_column(String(50))
    competency: Mapped[str | None] = mapped_column(Text)
    lesson_plan: Mapped[str | None] = mapped_column(Text)
    answer_key: Mapped[str | None] = mapped_column(Text)
    class_size: Mapped[int | None] = mapped_column(Integer)
    difficulty: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="created", nullable=False)
    assessment_status: Mapped[str] = mapped_column(
        String(30), default="gradable", nullable=False
    )
    assessment_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    trainee: Mapped["Trainee"] = relationship("Trainee", back_populates="sessions")
    consent: Mapped["Consent | None"] = relationship(
        "Consent", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    recording: Mapped["Recording | None"] = relationship(
        "Recording", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    report: Mapped["Report | None"] = relationship(
        "Report", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    turns: Mapped[list["Turn"]] = relationship(
        "Turn", back_populates="session", cascade="all, delete-orphan"
    )
    session_personas: Mapped[list["SessionPersona"]] = relationship(
        "SessionPersona", back_populates="session", cascade="all, delete-orphan"
    )
    generated_personas: Mapped[list["Persona"]] = relationship(
        "Persona", back_populates="generated_for_session", cascade="all, delete-orphan"
    )
    session_btis: Mapped[list["SessionBti"]] = relationship(
        "SessionBti", back_populates="session", cascade="all, delete-orphan"
    )
    personas: Mapped[list["Persona"]] = relationship(
        "Persona",
        secondary="session_persona",
        back_populates="sessions",
        overlaps="session_personas",
        viewonly=True,
    )
    btis: Mapped[list["Bti"]] = relationship(
        "Bti", secondary="session_bti", back_populates="sessions", overlaps="session_btis"
    )


