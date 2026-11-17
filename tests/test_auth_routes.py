import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from classsimvr.app import app
from classsimvr.database import get_session
from classsimvr.models import Base, Trainee
from classsimvr.routes.auth import get_auth_service
from classsimvr.services.auth import GoogleIdentity, GoogleOAuthService, GoogleOAuthSettings


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


@pytest.fixture
def auth_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_get_session():
        with DbSession(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_auth_service] = FakeOAuthService
    yield TestClient(app), engine
    app.dependency_overrides.clear()


def test_google_login_redirect_sets_state_cookie(auth_client):
    http, _ = auth_client

    response = http.get("/api/v1/auth/google/login", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth?"
    )
    assert "client_id=client-id.apps.googleusercontent.com" in response.headers["location"]
    assert "scope=openid+email+profile" in response.headers["location"]
    assert "classsimvr_google_oauth_state" in response.cookies


def test_google_callback_creates_trainee_and_sets_application_session(auth_client):
    http, engine = auth_client
    login = http.get("/api/v1/auth/google/login", follow_redirects=False)
    state = login.cookies["classsimvr_google_oauth_state"]

    response = http.get(
        f"/api/v1/auth/google/callback?code=auth-code&state={state}"
    )

    assert response.status_code == 200
    assert response.json() == {
        "trainee_id": 1,
        "full_name": "Teacher One",
        "email": "teacher@example.com",
    }
    assert "classsimvr_session" in response.cookies
    with DbSession(engine) as db:
        assert db.query(Trainee).count() == 1


def test_current_user_and_logout_use_application_session(auth_client):
    http, _ = auth_client
    login = http.get("/api/v1/auth/google/login", follow_redirects=False)
    state = login.cookies["classsimvr_google_oauth_state"]
    http.get(f"/api/v1/auth/google/callback?code=auth-code&state={state}")

    current = http.get("/api/v1/auth/me")
    logged_out = http.post("/api/v1/auth/logout")
    after_logout = http.get("/api/v1/auth/me")

    assert current.status_code == 200
    assert current.json()["email"] == "teacher@example.com"
    assert logged_out.json() == {"status": "logged_out"}
    assert after_logout.status_code == 401


def test_google_callback_rejects_invalid_state(auth_client):
    http, _ = auth_client
    response = http.get(
        "/api/v1/auth/google/callback?code=auth-code&state=wrong-state"
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Google OAuth state is invalid or expired"
