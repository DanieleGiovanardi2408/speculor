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


@pytest.fixture(autouse=True)
def _no_real_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never load the real embedding model in tests (even when the `embed` extra is installed);
    tests that need ranking inject a fake embedder."""
    from radar.pipeline import rank

    monkeypatch.setattr(rank, "load_default_embedder", lambda: None)


class FakeKeyring:
    """In-memory stand-in for ``keyring``: tests never touch the real macOS keychain."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, key: str) -> str | None:
        return self.store.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        self.store[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        if (service, key) not in self.store:
            raise KeyError(key)
        del self.store[(service, key)]


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    from radar.connectors import imap_auth

    ring = FakeKeyring()
    monkeypatch.setattr(imap_auth, "_keyring", lambda: ring)
    return ring


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
