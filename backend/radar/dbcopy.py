"""Copy every table from one database to another (e.g. local SQLite -> Supabase Postgres).

Keeps primary keys, copies tables in foreign-key order, then resets Postgres sequences.
Refuses to write into a target that already has rows unless ``replace=True``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, MetaData, func, insert, select, text
from sqlmodel import SQLModel

from radar.db import init_db

BATCH = 500


def copy_database(source: Engine, target: Engine, *, replace: bool = False) -> dict[str, int]:
    init_db(source)
    init_db(target)
    tables = SQLModel.metadata.sorted_tables
    counts: dict[str, int] = {}
    with target.begin() as dst:
        existing = {
            t.name: dst.execute(select(func.count()).select_from(t)).scalar_one() for t in tables
        }
        if any(existing.values()) and not replace:
            busy = {k: v for k, v in existing.items() if v}
            raise RuntimeError(f"target is not empty: {busy} (use replace=True to overwrite)")
        if replace:
            for t in reversed(tables):
                dst.execute(t.delete())
    with source.connect() as src, target.begin() as dst:
        for t in tables:
            rows = [dict(r._mapping) for r in src.execute(select(t))]
            for i in range(0, len(rows), BATCH):
                dst.execute(insert(t), rows[i : i + BATCH])
            counts[t.name] = len(rows)
        if target.dialect.name == "postgresql":
            _reset_sequences(dst, SQLModel.metadata)
    return counts


def _reset_sequences(conn: Any, metadata: MetaData) -> None:
    for t in metadata.sorted_tables:
        pk = [c for c in t.primary_key.columns if c.autoincrement is not False]
        if len(pk) != 1 or pk[0].type.python_type is not int:
            continue
        col = pk[0].name
        conn.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{t.name}', '{col}'), "
                f"COALESCE((SELECT MAX({col}) FROM {t.name}), 0) + 1, false)"
            )
        )


def enable_rls(target: Engine) -> list[str]:
    """Enable row level security (no policies) on every table: the Supabase Data API
    (anon / authenticated keys) then cannot read or write anything. The backend connects
    as the database owner, which bypasses RLS."""
    names = [t.name for t in SQLModel.metadata.sorted_tables]
    with target.begin() as conn:
        for name in names:
            conn.execute(text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
    return names
