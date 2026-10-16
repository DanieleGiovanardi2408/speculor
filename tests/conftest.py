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
def _no_real_jobteaser_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """A developer Mac may have the real JobTeaser browser profile; tests must never see it."""
    from radar.config import get_settings

    missing = tmp_path_factory.getbasetemp() / "no-jobteaser-profile"
    monkeypatch.setenv("RADAR_JOBTEASER_PROFILE", str(missing))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
