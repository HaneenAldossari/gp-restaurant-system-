"""
Seed the 4 teammate users into the database for the GP demo.

No passwords, no JWT — the real auth layer will replace this later. Each user
gets their own isolated workspace via `uploads.user_id` and the X-User-Id
header. Safe to run multiple times — uses ON CONFLICT (email) DO NOTHING.

Run:  python seed_users.py
"""
from sqlalchemy import text

from db import get_engine

USERS = [
    {"name": "Haneen", "email": "haneen@psau.sa"},
    {"name": "Arwa",   "email": "arwa@psau.sa"},
    {"name": "Noura",  "email": "noura@psau.sa"},
    {"name": "Norah",  "email": "norah@psau.sa"},
]


def seed() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for u in USERS:
            conn.execute(
                text("""
                    INSERT INTO users (name, email, password_hash, role)
                    VALUES (:name, :email, 'placeholder', 'manager')
                    ON CONFLICT (email) DO NOTHING
                """),
                u,
            )
        rows = conn.execute(
            text("SELECT id, name, email FROM users ORDER BY id")
        ).fetchall()

    print("Users in database:")
    for r in rows:
        print(f"  [{r[0]}] {r[1]:<8} {r[2]}")


if __name__ == "__main__":
    seed()
