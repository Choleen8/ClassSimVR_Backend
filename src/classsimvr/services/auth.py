from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session as DbSession

from ..models import Trainee


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
            raise OAuthConfigurationError(
                "Google OAuth is not configured: " + ", ".join(missing)
            )


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

        issuer = claims.get("iss")
        if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
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
        return serializer.dumps(
            {"trainee_id": trainee.trainee_id, "email": trainee.email}
        )

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
