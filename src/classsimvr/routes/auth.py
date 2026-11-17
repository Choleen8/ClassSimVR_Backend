from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..models import Trainee
from ..services.auth import (
    GoogleOAuthService,
    OAuthConfigurationError,
    OAuthExchangeError,
    OAuthIdentityError,
    OAuthStateError,
    account_user,
    authenticate,
    create_access_token,
    get_current_account,
)
from ..services.auth_contracts import AuthLogoutResponse, AuthUserResponse


# Existing Unreal/JWT API. Keep these paths and response models stable.
router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict[str, object]


@router.post("/login", response_model=LoginResponse)
def login(data: LoginRequest, db: DbSession = Depends(get_session)) -> LoginResponse:
    account = authenticate(db, data.identifier, data.password)
    token, expires_in = create_access_token(account)
    return LoginResponse(
        access_token=token,
        expires_in=expires_in,
        user=account_user(account),
    )


@router.get("/me")
def me(account=Depends(get_current_account)) -> dict[str, object]:
    return account_user(account)


# Separate Google OAuth/cookie API. It intentionally does not replace the
# bearer-token endpoints above.
oauth_router = APIRouter(prefix="/api/v1/auth", tags=["google-authentication"])


def get_auth_service() -> GoogleOAuthService:
    return GoogleOAuthService()


def _oauth_error(error: ValueError) -> HTTPException:
    if isinstance(error, OAuthConfigurationError):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, OAuthStateError):
        return HTTPException(status_code=400, detail=str(error))
    return HTTPException(status_code=401, detail="Google authentication failed")


@oauth_router.get("/google/login")
def google_login(service: GoogleOAuthService = Depends(get_auth_service)) -> RedirectResponse:
    try:
        state = secrets.token_urlsafe(32)
        location = service.authorization_url(state)
    except OAuthConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    response = RedirectResponse(url=location, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        service.STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=service.settings.cookie_secure,
        path="/",
    )
    return response


@oauth_router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: DbSession = Depends(get_session),
    service: GoogleOAuthService = Depends(get_auth_service),
):
    if error:
        raise HTTPException(status_code=400, detail="Google authentication was cancelled")
    try:
        service.settings.require_client_config()
        service.validate_state(request.cookies.get(service.STATE_COOKIE), state)
        if not code:
            raise OAuthExchangeError("Google callback did not include an authorization code")
        token_payload = await service.exchange_code(code)
        identity = service.verify_identity(token_payload["id_token"])
        trainee = service.upsert_trainee(db, identity)
        session_value = service.create_session_value(trainee)
    except (OAuthConfigurationError, OAuthExchangeError, OAuthIdentityError, OAuthStateError) as caught:
        raise _oauth_error(caught) from caught

    if service.settings.post_login_redirect:
        response = RedirectResponse(
            url=service.settings.post_login_redirect,
            status_code=status.HTTP_303_SEE_OTHER,
        )
    else:
        response = JSONResponse(
            AuthUserResponse(
                trainee_id=trainee.trainee_id,
                full_name=trainee.full_name,
                email=trainee.email,
            ).model_dump()
        )
    response.set_cookie(
        service.SESSION_COOKIE,
        session_value,
        max_age=service.settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=service.settings.cookie_secure,
        path="/",
    )
    response.delete_cookie(service.STATE_COOKIE, path="/")
    return response


@oauth_router.get("/me", response_model=AuthUserResponse)
def current_google_user(
    request: Request,
    db: DbSession = Depends(get_session),
    service: GoogleOAuthService = Depends(get_auth_service),
) -> AuthUserResponse:
    value = request.cookies.get(service.SESSION_COOKIE)
    if not value:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = service.read_session_value(value)
    except (OAuthConfigurationError, OAuthStateError) as error:
        raise HTTPException(status_code=401, detail="Authentication required") from error
    trainee = db.get(Trainee, int(payload["trainee_id"]))
    if trainee is None or trainee.email != payload.get("email"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return AuthUserResponse(
        trainee_id=trainee.trainee_id,
        full_name=trainee.full_name,
        email=trainee.email,
    )


@oauth_router.post("/logout", response_model=AuthLogoutResponse)
def logout(service: GoogleOAuthService = Depends(get_auth_service)) -> JSONResponse:
    response = JSONResponse(AuthLogoutResponse(status="logged_out").model_dump())
    response.delete_cookie(service.SESSION_COOKIE, path="/")
    response.delete_cookie(service.STATE_COOKIE, path="/")
    return response
