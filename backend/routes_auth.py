"""
Auth endpoints — login, logout (client-side), current-user lookup.

JWT lifecycle:
  POST /api/auth/login → { access_token, user }
  GET  /api/auth/me    → current user info (decodes the bearer token)

There's no server-side logout because JWTs are stateless — the client
just discards the token. If we ever need real revocation we'd add a
denylist table; for the thesis demo + 24h expiry, discard-on-logout
is plenty.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from auth import (
    create_token,
    get_current_user,
    verify_password,
)
from db import get_engine

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    parent_id: int | None = None
    permission: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


@router.post("/login", response_model=TokenResponse, summary="Email + password login")
def login(req: LoginRequest):
    """Verify credentials, return a 24-hour JWT plus the user record.

    Sub-users authenticate the same way; the returned JWT carries
    `parent_id` so all subsequent data requests scope to the parent's
    workspace automatically (handled in `auth.get_current_user_id`)."""
    with get_engine().connect() as conn:
        row = conn.execute(text("""
            SELECT id, name, email, password_hash, role, parent_id, permission
            FROM users WHERE LOWER(email) = LOWER(:email)
        """), {"email": req.email}).fetchone()

    if not row or not verify_password(req.password, row[3]):
        # Same message for both cases — don't leak whether email exists
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token(user_id=row[0], role=row[4], parent_id=row[5])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "role": row[4],
            "parent_id": row[5],
            "permission": row[6],
        },
    }


@router.get("/me", response_model=UserOut, summary="Current logged-in user")
def me(claims: dict = Depends(get_current_user)):
    """Return the full user record for whoever the bearer token
    represents. The frontend calls this on app load so a refresh
    doesn't lose context."""
    user_id = int(claims["id"])
    with get_engine().connect() as conn:
        row = conn.execute(text("""
            SELECT id, name, email, role, parent_id, permission
            FROM users WHERE id = :id
        """), {"id": user_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": row[0], "name": row[1], "email": row[2],
        "role": row[3], "parent_id": row[4], "permission": row[5],
    }
