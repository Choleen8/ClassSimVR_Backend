from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class AssessmentJob(Base):
    __tablename__ = "assessment_job"
    __table_args__ = (UniqueConstraint("session_id", name="uq_assessment_job_session"),)

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    provider: Mapped[str] = mapped_column(String(80), default="multimodal", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IndicatorScore(Base):
    __tablename__ = "indicator_score"

    score_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_session_id: Mapped[int] = mapped_column(
        ForeignKey("report.session_id", ondelete="CASCADE"), nullable=False
    )
    bti_id: Mapped[int] = mapped_column(
        ForeignKey("bti.bti_id"), nullable=False
    )
    observed: Mapped[bool] = mapped_column(nullable=False)
    score_type: Mapped[str] = mapped_column(String(50), nullable=False)

    report: Mapped["Report"] = relationship("Report", back_populates="indicator_scores")
    bti: Mapped["Bti"] = relationship("Bti", back_populates="indicator_scores")

    __mapper_args__ = {
        "polymorphic_on": score_type,
        "polymorphic_identity": "indicator",
    }


class ObservedScore(IndicatorScore):
    __tablename__ = "observed_score"
    __table_args__ = (
        CheckConstraint("score >= 1 AND score <= 4", name="ck_observed_score_range"),
    )

    score_id: Mapped[int] = mapped_column(
        ForeignKey("indicator_score.score_id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)

    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence", back_populates="score", cascade="all, delete-orphan"
    )

    __mapper_args__ = {"polymorphic_identity": "observed"}


class NotObserved(IndicatorScore):
    __tablename__ = "not_observed"

    score_id: Mapped[int] = mapped_column(
        ForeignKey("indicator_score.score_id", ondelete="CASCADE"), primary_key=True
    )

    __mapper_args__ = {"polymorphic_identity": "not_observed"}


class Evidence(Base):
    __tablename__ = "evidence"

    evidence_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    score_id: Mapped[int] = mapped_column(
        ForeignKey("observed_score.score_id", ondelete="CASCADE"), nullable=False
    )
    t_start_s: Mapped[float | None]
    t_end_s: Mapped[float | None]
    note: Mapped[str | None] = mapped_column(Text)

    score: Mapped["ObservedScore"] = relationship("ObservedScore", back_populates="evidence")
