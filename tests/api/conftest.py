from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from radar.api.deps import get_db_engine
from radar.api.main import app
from radar.db import init_db
from radar.demo import load_demo


@pytest.fixture()
def api_engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False}
    )
    init_db(eng)
    with Session(eng) as s:
        load_demo(s)
        s.commit()
    yield eng
    eng.dispose()


@pytest.fixture()
def client(api_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("RADAR_AUTH_REQUIRED", "false")
    app.dependency_overrides[get_db_engine] = lambda: api_engine
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def db(api_engine: Engine) -> Iterator[Session]:
    with Session(api_engine) as s:
        yield s
