from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from radar import models  # noqa: F401


@pytest.fixture
def engine(tmp_path) -> Engine:  # type: ignore[no-untyped-def]
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _no_real_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never load the real embedding model in tests (even when the `embed` extra is installed);
    tests that need ranking inject a fake embedder."""
    from radar.pipeline import rank

    monkeypatch.setattr(rank, "load_default_embedder", lambda: None)
