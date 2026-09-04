"""SQLAlchemy engine/session factory. Lazy so tests and the health route work without a database."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from api.config import settings


def to_sqlalchemy_url(url: str) -> str:
    """Neon hands out postgresql:// URLs; SQLAlchemy needs the psycopg (v3) driver prefix."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine | None:
    if not settings.database_url:
        return None
    # Small pool: this runs inside a serverless function and talks to Neon's pooler.
    return create_engine(
        to_sqlalchemy_url(settings.database_url),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        pool_recycle=300,
    )


def get_sessionmaker() -> sessionmaker[Session]:
    engine = get_engine()
    if engine is None:
        raise RuntimeError("DATABASE_URL is not configured")
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Request-scoped session. 503 (not a crash) when the database is not configured."""
    if get_engine() is None:
        raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL)")
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def get_db_optional() -> Generator[Session | None, None, None]:
    """Like get_db but yields None when no database is configured, for read routes that degrade gracefully."""
    if get_engine() is None:
        yield None
        return
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def ping_db() -> tuple[bool, str]:
    engine = get_engine()
    if engine is None:
        return False, "DATABASE_URL not configured"
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return True, "ok"
    except Exception as exc:  # surfaced, never hidden
        return False, f"{type(exc).__name__}: {exc}"[:300]
