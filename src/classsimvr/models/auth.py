from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class AuthAccount(Base):
    __tablename__ = "auth_account"
    __table_args__ = (
        UniqueConstraint("trainee_id", name="uq_auth_account_trainee_id"),
        UniqueConstraint("username", name="uq_auth_account_username"),
        UniqueConstraint("email", name="uq_auth_account_email"),
    )

    auth_account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trainee_id: Mapped[int] = mapped_column(
        ForeignKey("trainee.trainee_id", ondelete="CASCADE"), nullable=False
    )
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trainee: Mapped["Trainee"] = relationship("Trainee", back_populates="auth_account")
