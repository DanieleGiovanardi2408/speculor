"""Shared fixtures for LLM tests: temp SQLite DB, fictional profile, fake settings. No network."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from llm_testkit import FICTIONAL_PROFILE
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from radar import config as radar_config
from radar import models  # noqa: F401  (register tables)
from radar.llm import provider as provider_mod


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s


@pytest.fixture()
def fake_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """FakeProvider for both purposes, fictional profile, no keys."""
    monkeypatch.setenv("RADAR_LLM_PROVIDER", "fake")
    monkeypatch.delenv("RADAR_DRAFT_PROVIDER", raising=False)
    monkeypatch.setattr(radar_config, "load_yaml_config", lambda name: FICTIONAL_PROFILE)
    radar_config.get_settings.cache_clear()
    provider_mod.reset_limiters()
    yield
    radar_config.get_settings.cache_clear()
