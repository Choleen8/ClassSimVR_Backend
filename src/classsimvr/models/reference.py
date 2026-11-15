from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Persona(Base):
    __tablename__ = "persona"

    persona_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str | None] = mapped_column(String(100))
    academic_baseline: Mapped[str | None] = mapped_column(Text)
    avatar_ref: Mapped[str | None] = mapped_column(String(255))
    personality_traits: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    behavior_triggers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    response_style: Mapped[str | None] = mapped_column(String(100))
    participation_level: Mapped[str] = mapped_column(
        String(50), default="medium", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    generated_for_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE")
    )

    turns: Mapped[list["Turn"]] = relationship("Turn", back_populates="persona")
    session_personas: Mapped[list["SessionPersona"]] = relationship(
        "SessionPersona", back_populates="persona", cascade="all, delete-orphan"
    )
    generated_for_session: Mapped["Session | None"] = relationship(
        "Session", back_populates="generated_personas"
    )
    sessions: Mapped[list["Session"]] = relationship(
        "Session",
        secondary="session_persona",
        back_populates="personas",
        overlaps="session_personas,persona,session",
        viewonly=True,
    )


class Bti(Base):
    __tablename__ = "bti"

    bti_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strand: Mapped[str] = mapped_column(String(255), nullable=False)
    descriptor: Mapped[str | None] = mapped_column(Text)

    session_btis: Mapped[list["SessionBti"]] = relationship(
        "SessionBti", back_populates="bti", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["Session"]] = relationship(
        "Session", secondary="session_bti", back_populates="btis", overlaps="session_btis,bti,session"
    )
    indicator_scores: Mapped[list["IndicatorScore"]] = relationship(
        "IndicatorScore", back_populates="bti"
    )
