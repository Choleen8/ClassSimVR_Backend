from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from ..database import get_session
from ..services.auth import (
    account_user,
    authenticate,
    create_access_token,
    get_current_account,
)


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
