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
def _no_real_linkedin_jobs(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> Iterator[None]:  # type: ignore[no-untyped-def]
    """The developer's machine may have config/linkedin_searches.yaml and a real `linkedin`
    binary: tests must never pick them up (or call LinkedIn). Tests opt in explicitly."""
    from radar.config import get_settings

    monkeypatch.setenv("RADAR_LINKEDIN_BIN", str(tmp_path_factory.mktemp("nobin") / "linkedin"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
