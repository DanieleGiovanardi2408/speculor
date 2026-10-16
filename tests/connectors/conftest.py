from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import respx
from conn_helpers import UA, SleepRecorder

from radar.connectors.http import PoliteClient


@pytest.fixture
def sleeps() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
def client(tmp_path: Path, sleeps: SleepRecorder) -> Iterator[PoliteClient]:
    with PoliteClient(UA, tmp_path / "cache", cache_ttl_hours=1, sleep=sleeps) as c:
        yield c


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    # assert_all_mocked (default True): any unmocked request fails -> no live network in tests
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture(autouse=True)
def _no_real_imap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's .env may configure IMAP; connector tests must not pick it up."""
    from radar.config import get_settings

    for name in (
        "RADAR_IMAP_HOST",
        "RADAR_IMAP_USER",
        "RADAR_IMAP_PASSWORD",
        "RADAR_IMAP_OAUTH_CLIENT_ID",
    ):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("RADAR_IMAP_AUTH", "auto")
    get_settings.cache_clear()
