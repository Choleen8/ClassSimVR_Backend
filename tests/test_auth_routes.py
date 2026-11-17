from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
import json

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from classsimvr.app import app
from classsimvr.database import get_session
from classsimvr.models import AuthAccount, Base, Trainee
from classsimvr.routes.auth import LoginRequest, get_auth_service, login, me
from classsimvr.services.auth import (
    GoogleIdentity,
    GoogleOAuthService,
    GoogleOAuthSettings,
    get_current_account,
    hash_password,
)


JWT_SECRET = "test-secret-for-auth-tests-that-is-long-enough"
PASSWORD = "Correct Horse Battery Staple"


class FakeOAuthService(GoogleOAuthService):
    def __init__(self):
        super().__init__(
            GoogleOAuthSettings(
                client_id="client-id.apps.googleusercontent.com",
                client_secret="client-secret",
                redirect_uri="http://127.0.0.1:8000/api/v1/auth/google/callback",
                session_secret="test-session-secret",
                post_login_redirect=None,
                session_max_age=3600,
                cookie_secure=False,
            )
        )

    async def exchange_code(self, code: str):
        assert code == "auth-code"
        return {"id_token": "fake-id-token"}

    def verify_identity(self, id_token: str):
        assert id_token == "fake-id-token"
        return GoogleIdentity(
            subject="google-sub-1",
            email="teacher@example.com",
            full_name="Teacher One",
        )


def _make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _seed_account(session: DbSession):
    trainee = Trainee(full_name="Teacher One", email="teacher@example.com")
    session.add(trainee)
    session.flush()
    session.add(
        AuthAccount(
            trainee_id=trainee.trainee_id,
            username="teacher1",
            email="teacher@example.com",
            password_hash=hash_password(PASSWORD),
        )
    )
    session.commit()


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    engine = _make_engine()
    session = DbSession(engine)
    _seed_account(session)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    engine = _make_engine()
    with DbSession(engine) as seed_session:
        _seed_account(seed_session)

    def override_get_session():
        with DbSession(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_auth_service] = FakeOAuthService
    try:
        yield engine
    finally:
        app.dependency_overrides.clear()


def _request_with_cookie(cookie: str | None = None) -> Request:
    headers = [] if cookie is None else [(b"cookie", cookie.encode())]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/auth/google/callback",
            "headers": headers,
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )


def _cookie_value(response, name: str) -> str:
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    return cookies[name].value


def _run_sync(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("mocked OAuth coroutine unexpectedly yielded")


def _login(db, identifier="teacher1", password=PASSWORD):
    return login(LoginRequest(identifier=identifier, password=password), db)


def test_login_accepts_username_and_returns_expiring_access_token(db):
    response = _login(db)

    assert response.token_type == "bearer"
    assert response.expires_in == 1800
    assert response.user["username"] == "teacher1"
    claims = jwt.decode(response.access_token, JWT_SECRET, algorithms=["HS256"])
    assert claims["sub"] == str(response.user["trainee_id"])
    assert claims["exp"] > datetime.now(timezone.utc).timestamp()


def test_password_is_argon2id_hashed_and_trainee_link_is_unique(db):
    account = db.query(AuthAccount).filter_by(username="teacher1").one()
    assert account.password_hash.startswith("$argon2id$")
    assert PASSWORD not in account.password_hash
    constraints = {constraint.name for constraint in AuthAccount.__table__.constraints}
    assert "uq_auth_account_trainee_id" in constraints


def test_login_accepts_email(db):
    response = _login(db, identifier="TEACHER@EXAMPLE.COM")
    assert response.user["email"] == "teacher@example.com"


def test_login_rejects_incorrect_password_and_nonexistent_account(db):
    with pytest.raises(HTTPException) as wrong_password:
        _login(db, password="wrong")
    with pytest.raises(HTTPException) as missing:
        _login(db, identifier="missing", password="wrong")

    assert wrong_password.value.status_code == 401
    assert missing.value.status_code == 401


def test_login_rejects_inactive_account(db):
    account = db.query(AuthAccount).filter_by(username="teacher1").one()
    account.is_active = False
    db.commit()

    with pytest.raises(HTTPException) as error:
        _login(db)

    assert error.value.status_code == 403


def test_me_accepts_valid_token_and_rejects_expired_or_invalid_tokens(db):
    response = _login(db)
    valid_credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=response.access_token
    )
    valid = get_current_account(valid_credentials, db)
    expired_token = jwt.encode(
        {
            "sub": "1",
            "auth_account_id": 1,
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    expired_credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=expired_token)
    invalid_credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt")

    assert me(valid)["username"] == "teacher1"
    with pytest.raises(HTTPException) as expired:
        get_current_account(expired_credentials, db)
    with pytest.raises(HTTPException) as invalid:
        get_current_account(invalid_credentials, db)
    assert expired.value.status_code == 401
    assert invalid.value.status_code == 401


def test_google_login_redirect_sets_state_cookie(auth_client):
    from classsimvr.routes.auth import google_login

    response = google_login(FakeOAuthService())

    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth?"
    )
    assert "client_id=client-id.apps.googleusercontent.com" in response.headers["location"]
    assert "scope=openid+email+profile" in response.headers["location"]
    assert "classsimvr_google_oauth_state" in response.headers["set-cookie"]


def test_google_callback_links_existing_trainee_and_sets_application_session(auth_client):
    from classsimvr.routes.auth import google_callback, google_login

    service = FakeOAuthService()
    state = _cookie_value(google_login(service), service.STATE_COOKIE)
    with DbSession(auth_client) as session:
        response = _run_sync(
            google_callback(
                _request_with_cookie(f"{service.STATE_COOKIE}={state}"),
                code="auth-code",
                state=state,
                db=session,
                service=service,
            )
        )

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "trainee_id": 1,
        "full_name": "Teacher One",
        "email": "teacher@example.com",
    }
    assert service.SESSION_COOKIE in response.headers["set-cookie"]
    with DbSession(auth_client) as session:
        assert session.query(Trainee).count() == 1
        assert session.query(AuthAccount).count() == 1


def test_google_current_user_and_logout_use_application_session(auth_client):
    from classsimvr.routes.auth import (
        current_google_user,
        google_callback,
        google_login,
        logout,
    )

    service = FakeOAuthService()
    state = _cookie_value(google_login(service), service.STATE_COOKIE)
    with DbSession(auth_client) as session:
        callback = _run_sync(
            google_callback(
                _request_with_cookie(f"{service.STATE_COOKIE}={state}"),
                code="auth-code",
                state=state,
                db=session,
                service=service,
            )
        )
        session_cookie = _cookie_value(callback, service.SESSION_COOKIE)
        current = current_google_user(
            _request_with_cookie(f"{service.SESSION_COOKIE}={session_cookie}"),
            db=session,
            service=service,
        )
    logged_out = logout(service)

    assert current.email == "teacher@example.com"
    assert logged_out.body == b'{"status":"logged_out"}'
    assert service.SESSION_COOKIE in logged_out.headers["set-cookie"]


def test_google_callback_rejects_invalid_state(auth_client):
    from classsimvr.routes.auth import google_callback

    service = FakeOAuthService()
    with DbSession(auth_client) as session:
        with pytest.raises(HTTPException) as error:
            _run_sync(
                google_callback(
                    _request_with_cookie(f"{service.STATE_COOKIE}=valid-state"),
                    code="auth-code",
                    state="wrong-state",
                    db=session,
                    service=service,
                )
            )

    assert error.value.status_code == 400
    assert error.value.detail == "Google OAuth state is invalid or expired"


def test_authentication_routes_are_registered_once():
    paths = app.openapi()["paths"]

    expected = [
        "/auth/login",
        "/auth/me",
        "/api/v1/auth/google/login",
        "/api/v1/auth/google/callback",
        "/api/v1/auth/me",
        "/api/v1/auth/logout",
    ]
    assert all(path in paths for path in expected)
