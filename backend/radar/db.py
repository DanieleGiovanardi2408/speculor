from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

from radar import models
from radar.config import get_settings
from radar.timeutil import utcnow


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if os.environ.get("VERCEL") or ":6543/" in url:
        # Serverless / Supabase transaction pooler: no client-side pool, no prepared statements.
        return create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None})
    return create_engine(url, pool_pre_ping=True)


def init_db(engine: Engine | None = None) -> Engine:
    engine = engine or get_engine()
    SQLModel.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    engine = engine or init_db()
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_setting(key: str, default: Any = None, session: Session | None = None) -> Any:
    if session is not None:
        row = session.get(models.Setting, key)
        return default if row is None else row.value
    with session_scope() as s:
        row = s.get(models.Setting, key)
        return default if row is None else row.value


def set_setting(session: Session, key: str, value: Any) -> None:
    row = session.get(models.Setting, key)
    if row is None:
        session.add(models.Setting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()
        session.add(row)
