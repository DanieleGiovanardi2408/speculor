from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, inspect, text
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
    add_missing_columns(engine)
    return engine


def add_missing_columns(engine: Engine) -> list[str]:
    """Additive migration: create columns that exist in the models but not in the database.

    ``create_all`` never alters existing tables, so new nullable/defaulted model fields are
    added here with ``ALTER TABLE ... ADD COLUMN``. Columns are never dropped or changed.
    """
    added: list[str] = []
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            ddl_type = column.type.compile(dialect=engine.dialect)
            default = ""
            arg = getattr(column.default, "arg", None)
            if isinstance(arg, bool):
                default = (
                    f" DEFAULT {'1' if arg else '0'}"
                    if engine.dialect.name == "sqlite"
                    else f" DEFAULT {str(arg).upper()}"
                )
            elif isinstance(arg, int | float):
                default = f" DEFAULT {arg}"
            elif isinstance(arg, str):
                default = " DEFAULT '" + arg.replace("'", "''") + "'"
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}{default}'
                    )
                )
            added.append(f"{table.name}.{column.name}")
    return added


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
