"""
Authentication for the GP Restaurant System.

Two layers stacked here:

  1. JWT (real auth) — issued by /api/auth/login, sent as
     `Authorization: Bearer <token>` header. Carries the user's id,
     role (admin/manager/sub_user), and parent_id (for sub-users so
     their data scope inherits from the parent's workspace).

  2. X-User-Id header (legacy demo override) — unchanged from the
     pre-auth thesis-testing setup. Kept so the workspace switcher
     keeps working during the transition; will be removed once all
     teammates are using real login.

Both routes through `get_current_user_id` which downstream queries
use for data scoping. Sub-users inherit their parent's data scope —
when a sub-user is logged in, queries return the parent's data.

Password hashing uses passlib's bcrypt scheme; JWT signing uses
HMAC-SHA256 with `JWT_SECRET` from env (must be set in production —
the dev default is fine for local but should be overridden on
Render with a long random string).
"""
from __future__ import annotations

import datetime
import os
from typing import Any

import jwt
from fastapi import Header, HTTPException
from passlib.context import CryptContext

DEFAULT_USER_ID = 1

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))

# bcrypt is the standard password hash. passlib wraps it with a slow
# cost factor that resists brute force.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:
        # passlib raises on malformed hashes (e.g. legacy seeded users
        # with the placeholder). Treat as auth failure rather than 500.
        return False


def create_token(user_id: int, role: str, parent_id: int | None = None) -> str:
    payload = {
        "sub": str(user_id),
        "role": role,
        "parent_id": parent_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired — log in again")
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def _claims_from_headers(authorization: str | None, x_user_id: int | None) -> dict[str, Any]:
    """Resolve the caller's identity from either auth path.

    JWT wins over X-User-Id when both are present. Returns a
    normalised claims dict with keys: id (int), role (str),
    parent_id (int|None). Falls back to DEFAULT_USER_ID when
    neither is supplied — preserves the old demo behaviour for
    routes called directly during testing.
    """
    if authorization and authorization.startswith("Bearer "):
        payload = decode_token(authorization[len("Bearer "):])
        return {
            "id": int(payload["sub"]),
            "role": payload.get("role", "manager"),
            "parent_id": payload.get("parent_id"),
        }
    uid = x_user_id if x_user_id is not None else DEFAULT_USER_ID
    return {"id": int(uid), "role": "manager", "parent_id": None}


def get_current_user_id(
    authorization: str | None = Header(default=None),
    x_user_id: int | None = Header(default=None, alias="X-User-Id"),
) -> int:
    """FastAPI dependency — return the user_id whose data the caller
    should see. For sub-users this is the PARENT's id (sub-users
    inherit the parent's workspace). Used by every data route."""
    claims = _claims_from_headers(authorization, x_user_id)
    return int(claims["parent_id"]) if claims.get("parent_id") else int(claims["id"])


def get_current_user(
    authorization: str | None = Header(default=None),
    x_user_id: int | None = Header(default=None, alias="X-User-Id"),
) -> dict[str, Any]:
    """FastAPI dependency — return the full claims dict. Routes that
    care about role / permission (e.g. /api/team) use this to gate
    actions; routes that only care about WHOSE data to load use
    get_current_user_id."""
    return _claims_from_headers(authorization, x_user_id)
