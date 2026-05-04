"""
Seed the demo manager accounts for the GP system.

Each teammate gets one manager-level account. Passwords are bcrypt-
hashed at seed time. The default password for every account is
"demo1234" — change it from the Team management page after first login,
or override per-user via the SEED_PASSWORDS env var (JSON).

A "Demo Manager" account at id=1 exists so the legacy X-User-Id=1
fallback (workspace switcher / demo links) keeps mapping to a real
record. That account also has password "demo1234".

Idempotent: re-running upgrades the password for users that still
have the placeholder hash from earlier seeding, but leaves real
bcrypt hashes alone (so it doesn't reset a password the user has
already changed).
"""
from __future__ import annotations

import json
import os

from sqlalchemy import text

from auth import hash_password
from db import get_engine

DEFAULT_PASSWORD = os.getenv("SEED_DEFAULT_PASSWORD", "demo1234")

USERS = [
    {"name": "Demo Manager", "email": "demo@psau.sa"},
    {"name": "Haneen", "email": "haneen@psau.sa"},
    {"name": "Arwa",   "email": "arwa@psau.sa"},
    {"name": "Noura",  "email": "noura@psau.sa"},
    {"name": "Norah",  "email": "norah@psau.sa"},
]


def _resolve_password(email: str) -> str:
    """Allow per-user override via JSON env var, fall back to default."""
    overrides_raw = os.getenv("SEED_PASSWORDS")
    if overrides_raw:
        try:
            overrides = json.loads(overrides_raw)
            if email in overrides:
                return str(overrides[email])
        except Exception:
            pass
    return DEFAULT_PASSWORD


def seed() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for u in USERS:
            password = _resolve_password(u["email"])
            pw_hash = hash_password(password)
            # Insert if missing; if existing has the legacy "placeholder"
            # hash, upgrade to a real bcrypt hash so /api/auth/login works.
            # If the user has already set a real password, leave it alone.
            conn.execute(
                text("""
                    INSERT INTO users (name, email, password_hash, role)
                    VALUES (:name, :email, :hash, 'manager')
                    ON CONFLICT (email) DO UPDATE
                    SET password_hash = EXCLUDED.password_hash
                    WHERE users.password_hash = 'placeholder'
                       OR LENGTH(users.password_hash) < 20
                """),
                {"name": u["name"], "email": u["email"], "hash": pw_hash},
            )
        rows = conn.execute(
            text("SELECT id, name, email, role FROM users WHERE role IN ('admin','manager') ORDER BY id")
        ).fetchall()

    print("Manager accounts in database:")
    for r in rows:
        print(f"  [{r[0]}] {r[1]:<14} {r[2]:<22} role={r[3]}")
    print(f"\nDefault password for every seeded account: {DEFAULT_PASSWORD}")


if __name__ == "__main__":
    seed()
