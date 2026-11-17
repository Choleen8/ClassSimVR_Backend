from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..models import Trainee
from ..services.auth import (
    GoogleOAuthService,
    OAuthConfigurationError,
    OAuthExchangeError,
    OAuthIdentityError,
    OAuthStateError,
)
from ..services.auth_contracts import AuthLogoutResponse, AuthUserResponse

router = APIRouter(prefix="/api/v1")


def get_auth_service() -> GoogleOAuthService:
    return GoogleOAuthService()


def _oauth_error(error: ValueError) -> HTTPException:
    if isinstance(error, OAuthConfigurationError):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, OAuthStateError):
        return HTTPException(status_code=400, detail=str(error))
    return HTTPException(status_code=401, detail="Google authentication failed")


@router.get("/auth/google/login")
def google_login(service: GoogleOAuthService = Depends(get_auth_service)) -> RedirectResponse:
    try:
        state = secrets.token_urlsafe(32)
        location = service.authorization_url(state)
    except OAuthConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    response = RedirectResponse(location=location, status_code=status.HTTP_302_FOUND)
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


@router.get("/auth/google/callback")
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
            location=service.settings.post_login_redirect,
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


@router.get("/auth/me", response_model=AuthUserResponse)
def current_user(
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


@router.post("/auth/logout", response_model=AuthLogoutResponse)
def logout(service: GoogleOAuthService = Depends(get_auth_service)) -> JSONResponse:
    response = JSONResponse(AuthLogoutResponse(status="logged_out").model_dump())
    response.delete_cookie(service.SESSION_COOKIE, path="/")
    response.delete_cookie(service.STATE_COOKIE, path="/")
    return response
