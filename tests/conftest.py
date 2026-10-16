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
