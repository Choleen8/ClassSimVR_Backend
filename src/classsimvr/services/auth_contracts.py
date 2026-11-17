from __future__ import annotations

from pydantic import BaseModel


class AuthUserResponse(BaseModel):
    trainee_id: int
    full_name: str
    email: str


class AuthLogoutResponse(BaseModel):
    status: str
