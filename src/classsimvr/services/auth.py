from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..models import AuthAccount


password_hasher = PasswordHasher()
bearer_scheme = HTTPBearer(auto_error=False)
_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,79}$")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def normalize_identifier(value: str) -> str:
    return value.strip().casefold()


def validate_username(username: str) -> str:
    username = username.strip()
    if not _USERNAME_PATTERN.fullmatch(username):
        raise ValueError("username must be 3-80 characters and contain only letters, numbers, _, ., or -")
    return username


def validate_email(email: str) -> str:
    email = email.strip().casefold()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("email must be valid")
    return email


def _jwt_settings() -> tuple[str, int]:
    secret = os.getenv("JWT_SECRET", "").strip()
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be configured with at least 32 characters")
    try:
        minutes = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    except ValueError as error:
        raise RuntimeError("JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be an integer") from error
    if minutes <= 0:
        raise RuntimeError("JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be positive")
    return secret, minutes


def create_access_token(account: AuthAccount) -> tuple[str, int]:
    secret, minutes = _jwt_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=minutes)
    token = jwt.encode(
        {
            "sub": str(account.trainee_id),
            "auth_account_id": account.auth_account_id,
            "iat": now,
            "exp": expires,
        },
        secret,
        algorithm="HS256",
    )
    return token, minutes * 60


def authenticate(db: DbSession, identifier: str, password: str) -> AuthAccount:
    normalized = normalize_identifier(identifier)
    account = db.scalar(
        select(AuthAccount).where(
            or_(
                func.lower(AuthAccount.username) == normalized,
                func.lower(AuthAccount.email) == normalized,
            )
        )
    )
    if account is None or not verify_password(password, account.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    if not account.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account is inactive")
    account.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(account)
    return account


def account_user(account: AuthAccount) -> dict[str, object]:
    return {
        "trainee_id": account.trainee_id,
        "username": account.username,
        "email": account.email,
        "full_name": account.trainee.full_name,
        "is_active": account.is_active,
    }


def get_current_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: DbSession = Depends(get_session),
) -> AuthAccount:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        secret, _ = _jwt_settings()
        claims = jwt.decode(credentials.credentials, secret, algorithms=["HS256"])
        trainee_id = int(claims["sub"])
        account_id = int(claims["auth_account_id"])
    except (RuntimeError, KeyError, TypeError, ValueError, jwt.InvalidTokenError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token") from error
    account = db.scalar(
        select(AuthAccount).where(
            AuthAccount.auth_account_id == account_id,
            AuthAccount.trainee_id == trainee_id,
        )
    )
    if account is None or not account.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or inactive account")
    return account
