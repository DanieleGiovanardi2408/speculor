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
