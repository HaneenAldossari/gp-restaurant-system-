"""
Team management — managers create / update / remove their sub-users.

Sub-users inherit their parent's data scope automatically (the JWT
carries parent_id; auth.get_current_user_id returns the parent's
id for any sub-user, so all data routes return the manager's data
to the sub-user without code changes elsewhere).

Permissions enforced here:
  read_only   — can read everything in the workspace, no writes
  write_only  — can upload data, no analytics views
  read_write  — can do everything except create/manage sub-users
                (only the manager can do that)

A sub-user can never create another sub-user. An admin can create
top-level managers (not yet wired to a UI; admins are seeded).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from auth import get_current_user, hash_password
from db import get_engine

router = APIRouter(prefix="/api/team", tags=["Team"])

VALID_PERMISSIONS = ("read_only", "write_only", "read_write")


class CreateSubUser(BaseModel):
    name: str
    email: str
    password: str
    permission: str


class UpdateSubUser(BaseModel):
    name: str | None = None
    permission: str | None = None
    password: str | None = None  # optional reset


class SubUserOut(BaseModel):
    id: int
    name: str
    email: str
    permission: str
    createdAt: str | None = None


def _require_manager(claims: dict) -> int:
    """Return the manager's user_id; raise 403 if the caller isn't
    a manager/admin or is themselves a sub-user trying to escalate."""
    role = claims.get("role")
    if role not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Only managers can manage the team")
    if claims.get("parent_id"):
        raise HTTPException(status_code=403, detail="Sub-users cannot manage other sub-users")
    return int(claims["id"])


def _check_permission(perm: str) -> None:
    if perm not in VALID_PERMISSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"permission must be one of {VALID_PERMISSIONS}",
        )


@router.get("/sub-users", summary="List the manager's sub-users")
def list_sub_users(claims: dict = Depends(get_current_user)) -> dict:
    parent_id = _require_manager(claims)
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT id, name, email, permission, created_at
            FROM users
            WHERE parent_id = :pid AND role = 'sub_user'
            ORDER BY created_at DESC
        """), {"pid": parent_id}).fetchall()
    return {
        "subUsers": [
            {
                "id": r[0],
                "name": r[1],
                "email": r[2],
                "permission": r[3] or "read_only",
                "createdAt": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ],
    }


@router.post("/sub-users", response_model=SubUserOut, summary="Create a sub-user")
def create_sub_user(req: CreateSubUser, claims: dict = Depends(get_current_user)) -> dict:
    parent_id = _require_manager(claims)
    _check_permission(req.permission)
    if not req.password or len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    pw_hash = hash_password(req.password)
    with get_engine().begin() as conn:
        # Pre-check for duplicate email so we get a clean error instead
        # of a Postgres unique-violation traceback.
        existing = conn.execute(
            text("SELECT 1 FROM users WHERE LOWER(email) = LOWER(:e)"),
            {"e": req.email},
        ).scalar()
        if existing:
            raise HTTPException(status_code=409, detail="That email is already registered")

        row = conn.execute(text("""
            INSERT INTO users (name, email, password_hash, role, parent_id, permission)
            VALUES (:n, :e, :p, 'sub_user', :pid, :perm)
            RETURNING id, created_at
        """), {
            "n": req.name.strip(),
            "e": req.email.strip(),
            "p": pw_hash,
            "pid": parent_id,
            "perm": req.permission,
        }).fetchone()

    return {
        "id": row[0],
        "name": req.name,
        "email": req.email,
        "permission": req.permission,
        "createdAt": row[1].isoformat() if row[1] else None,
    }


@router.patch("/sub-users/{sub_user_id}", response_model=SubUserOut, summary="Update a sub-user")
def update_sub_user(
    sub_user_id: int,
    req: UpdateSubUser,
    claims: dict = Depends(get_current_user),
) -> dict:
    parent_id = _require_manager(claims)
    if req.permission is not None:
        _check_permission(req.permission)

    sets, params = [], {"id": sub_user_id, "pid": parent_id}
    if req.name is not None:
        sets.append("name = :n"); params["n"] = req.name.strip()
    if req.permission is not None:
        sets.append("permission = :perm"); params["perm"] = req.permission
    if req.password is not None:
        if len(req.password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        sets.append("password_hash = :p"); params["p"] = hash_password(req.password)
    if not sets:
        raise HTTPException(status_code=400, detail="No fields to update")

    with get_engine().begin() as conn:
        row = conn.execute(text(f"""
            UPDATE users SET {', '.join(sets)}
            WHERE id = :id AND parent_id = :pid AND role = 'sub_user'
            RETURNING id, name, email, permission, created_at
        """), params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sub-user not found in your team")
    return {
        "id": row[0], "name": row[1], "email": row[2],
        "permission": row[3] or "read_only",
        "createdAt": row[4].isoformat() if row[4] else None,
    }


@router.delete("/sub-users/{sub_user_id}", summary="Remove a sub-user")
def delete_sub_user(
    sub_user_id: int,
    claims: dict = Depends(get_current_user),
) -> dict:
    parent_id = _require_manager(claims)
    with get_engine().begin() as conn:
        row = conn.execute(text("""
            DELETE FROM users
            WHERE id = :id AND parent_id = :pid AND role = 'sub_user'
            RETURNING id
        """), {"id": sub_user_id, "pid": parent_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sub-user not found in your team")
    return {"deleted": True, "id": sub_user_id}
