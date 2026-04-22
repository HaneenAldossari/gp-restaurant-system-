"""
Lightweight per-user workspace isolation for the GP demo.

Each request carries an `X-User-Id` header. All routes use the
`get_current_user_id` FastAPI dependency so reads/writes filter to
that user's workspace. No passwords, no JWT — this is a graduation
demo placeholder that a real auth layer will replace later.
"""
from fastapi import Header

DEFAULT_USER_ID = 1


def get_current_user_id(x_user_id: int | None = Header(default=None)) -> int:
    """FastAPI dependency — read X-User-Id header, default to 1 if missing."""
    return x_user_id if x_user_id is not None else DEFAULT_USER_ID
