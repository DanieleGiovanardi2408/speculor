from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from radar import config as radar_config
from radar import models  # noqa: F401

EXAMPLE_FACTS = radar_config.REPO_ROOT / "config" / "facts.example.yaml"


@pytest.fixture(autouse=True)
def _example_facts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests never read the real (gitignored) fact bank: always the fictional example."""
    monkeypatch.setenv("RADAR_FACTS_PATH", str(EXAMPLE_FACTS))
    radar_config.get_settings.cache_clear()
    yield
    radar_config.get_settings.cache_clear()


@pytest.fixture
def engine(tmp_path) -> Engine:  # type: ignore[no-untyped-def]
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s
