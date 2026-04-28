"""PostgreSQL connection helper — single SQLAlchemy engine for all routers.

In local dev, the per-piece DB_USER / DB_HOST / etc. variables are used so a
developer can override anything quickly. In hosted environments (Render,
Railway, Heroku) a single `DATABASE_URL` is provided and takes precedence.

Render hands out URLs starting with `postgres://`, but psycopg2 needs the
explicit `postgresql+psycopg2://` scheme — the conversion happens here so
the rest of the codebase doesn't have to care which provider is in front.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

DB_USER = os.getenv("DB_USER", "haneenal-dossari")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "gp_restaurant")


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        return f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    # Render / Heroku style — normalize so SQLAlchemy + psycopg2 are happy.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and "+psycopg2" not in url:
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _resolve_database_url()

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return _engine
