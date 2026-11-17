from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from classsimvr.models import AuthAccount, Base, Trainee
from classsimvr.routes.auth import LoginRequest, login, me
from classsimvr.services.auth import get_current_account, hash_password


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-auth-tests-that-is-long-enough")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = DbSession(engine)
    trainee = Trainee(full_name="Teacher One", email="teacher@example.com")
    session.add(trainee)
    session.flush()
    session.add(AuthAccount(
        trainee_id=trainee.trainee_id,
        username="teacher1",
        email="teacher@example.com",
        password_hash=hash_password("Correct Horse Battery Staple"),
    ))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _login(db, identifier="teacher1", password="Correct Horse Battery Staple"):
    return login(LoginRequest(identifier=identifier, password=password), db)


def test_login_accepts_username_and_returns_expiring_access_token(db):
    response = _login(db)

    assert response.token_type == "bearer"
    assert response.expires_in == 1800
    assert response.user["username"] == "teacher1"
    claims = jwt.decode(response.access_token, "test-secret-for-auth-tests-that-is-long-enough", algorithms=["HS256"])
    assert claims["sub"] == str(response.user["trainee_id"])
    assert claims["exp"] > datetime.now(timezone.utc).timestamp()


def test_password_is_argon2id_hashed_and_trainee_link_is_unique(db):
    account = db.query(AuthAccount).filter_by(username="teacher1").one()
    assert account.password_hash.startswith("$argon2id$")
    assert "Correct Horse Battery Staple" not in account.password_hash
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
        {"sub": "1", "auth_account_id": 1, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        "test-secret-for-auth-tests-that-is-long-enough",
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
