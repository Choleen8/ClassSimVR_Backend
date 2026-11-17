from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import re
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..models import AuthAccount, Trainee


# Username/password authentication used by the Unreal login UI.
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


# Google OAuth authentication. It intentionally uses a separate signed cookie
# session and route prefix; it does not replace or alter the Unreal JWT flow.
class OAuthConfigurationError(ValueError):
    pass


class OAuthExchangeError(ValueError):
    pass


class OAuthIdentityError(ValueError):
    pass


class OAuthStateError(ValueError):
    pass


@dataclass(frozen=True)
class GoogleOAuthSettings:
    client_id: str | None
    client_secret: str | None
    redirect_uri: str
    session_secret: str | None
    post_login_redirect: str | None
    session_max_age: int
    cookie_secure: bool

    @classmethod
    def from_environment(cls) -> "GoogleOAuthSettings":
        load_dotenv()
        return cls(
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            redirect_uri=os.getenv(
                "GOOGLE_REDIRECT_URI",
                "http://127.0.0.1:8000/api/v1/auth/google/callback",
            ),
            session_secret=os.getenv("APP_SESSION_SECRET"),
            post_login_redirect=os.getenv("GOOGLE_POST_LOGIN_REDIRECT"),
            session_max_age=int(os.getenv("APP_SESSION_MAX_AGE_SECONDS", "28800")),
            cookie_secure=os.getenv("APP_COOKIE_SECURE", "false").casefold() == "true",
        )

    def require_client_config(self) -> None:
        missing = [
            name
            for name, value in (
                ("GOOGLE_CLIENT_ID", self.client_id),
                ("GOOGLE_CLIENT_SECRET", self.client_secret),
                ("APP_SESSION_SECRET", self.session_secret),
            )
            if not value
        ]
        if missing:
            raise OAuthConfigurationError("Google OAuth is not configured: " + ", ".join(missing))


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    full_name: str


class GoogleOAuthService:
    AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    SCOPES = "openid email profile"
    STATE_COOKIE = "classsimvr_google_oauth_state"
    SESSION_COOKIE = "classsimvr_session"

    def __init__(self, settings: GoogleOAuthSettings | None = None) -> None:
        self.settings = settings or GoogleOAuthSettings.from_environment()

    def authorization_url(self, state: str) -> str:
        self.settings.require_client_config()
        return self.AUTHORIZATION_ENDPOINT + "?" + urlencode(
            {
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "response_type": "code",
                "scope": self.SCOPES,
                "state": state,
                "access_type": "offline",
                "prompt": "select_account",
            }
        )

    async def exchange_code(self, code: str) -> dict[str, Any]:
        self.settings.require_client_config()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self.settings.client_id,
                    "client_secret": self.settings.client_secret,
                    "redirect_uri": self.settings.redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        if response.status_code != 200:
            raise OAuthExchangeError("Google authorization code exchange failed")
        payload = response.json()
        if not payload.get("id_token"):
            raise OAuthExchangeError("Google did not return an identity token")
        return payload

    def verify_identity(self, id_token: str) -> GoogleIdentity:
        self.settings.require_client_config()
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.id_token import verify_oauth2_token

            claims = verify_oauth2_token(id_token, Request(), self.settings.client_id)
        except Exception as error:
            raise OAuthIdentityError("Google identity verification failed") from error

        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise OAuthIdentityError("Google identity issuer is invalid")
        if not claims.get("email_verified"):
            raise OAuthIdentityError("Google email is not verified")
        subject = claims.get("sub")
        email = claims.get("email")
        if not subject or not email:
            raise OAuthIdentityError("Google identity is missing required claims")
        return GoogleIdentity(
            subject=str(subject),
            email=str(email).casefold(),
            full_name=str(claims.get("name") or email),
        )

    def upsert_trainee(self, db: DbSession, identity: GoogleIdentity) -> Trainee:
        # Google accounts are linked to the existing trainee by Google's
        # verified email. The Google subject is not persisted by this version.
        trainee = db.query(Trainee).filter(Trainee.email == identity.email).first()
        if trainee is None:
            trainee = Trainee(full_name=identity.full_name, email=identity.email)
            db.add(trainee)
        elif trainee.full_name != identity.full_name:
            trainee.full_name = identity.full_name
        db.commit()
        db.refresh(trainee)
        return trainee

    def create_session_value(self, trainee: Trainee) -> str:
        self.settings.require_client_config()
        serializer = URLSafeTimedSerializer(self.settings.session_secret, salt="classsimvr-session")
        return serializer.dumps({"trainee_id": trainee.trainee_id, "email": trainee.email})

    def read_session_value(self, value: str) -> dict[str, Any]:
        self.settings.require_client_config()
        serializer = URLSafeTimedSerializer(self.settings.session_secret, salt="classsimvr-session")
        try:
            payload = serializer.loads(value, max_age=self.settings.session_max_age)
        except (BadSignature, SignatureExpired) as error:
            raise OAuthStateError("application session is invalid or expired") from error
        if not isinstance(payload, dict) or not payload.get("trainee_id"):
            raise OAuthStateError("application session is invalid")
        return payload

    @staticmethod
    def validate_state(expected: str | None, received: str | None) -> None:
        if not expected or not received or expected != received:
            raise OAuthStateError("Google OAuth state is invalid or expired")
