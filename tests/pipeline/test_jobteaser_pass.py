"""Weekly JobTeaser pass reminder (Monday in Europe/Rome, or stale) and the configured links."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from radar.pipeline.jobteaser_ingest import pass_links, reminder

MONDAY = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
THURSDAY = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def at(dt: datetime) -> dict[str, object]:
    return {"at": dt.isoformat(), "received": 3, "new": 1, "updated": 2, "ignored": 0}


@pytest.mark.parametrize(
    ("last", "now", "expected"),
    [
        (None, MONDAY, "monday"),
        (at(datetime(2026, 10, 16, 9, tzinfo=UTC)), MONDAY, "monday"),
        (at(datetime(2026, 10, 19, 8, tzinfo=UTC)), MONDAY, None),  # already done today
        (None, THURSDAY, "never"),
        (at(datetime(2026, 10, 19, 8, tzinfo=UTC)), THURSDAY, None),
        (at(datetime(2026, 10, 15, 8, tzinfo=UTC)), THURSDAY, "stale"),  # > 7 days
        (at(datetime(2026, 10, 15, 10, tzinfo=UTC)), THURSDAY, None),  # just under 7 days
        ({"at": "garbage"}, THURSDAY, "never"),
    ],
)
def test_reminder(last: dict[str, object] | None, now: datetime, expected: str | None) -> None:
    assert reminder(last, now) == expected


def test_monday_is_the_rome_calendar_day() -> None:
    sunday_utc_monday_rome = datetime(2026, 10, 18, 23, 30, tzinfo=UTC)  # 01:30 in Rome
    recent = at(datetime(2026, 10, 17, 9, tzinfo=UTC))
    assert reminder(recent, sunday_utc_monday_rome) == "monday"
    monday_utc_tuesday_rome = datetime(2026, 10, 19, 22, 30, tzinfo=UTC)  # 00:30 Tue in Rome
    assert reminder(recent, monday_utc_tuesday_rome) is None


def test_pass_links_from_config_only_https_jobteaser() -> None:
    cfg = {
        "base_url": "https://luiss.jobteaser.com",
        "locale": "it",
        "links": {
            "offerte_mirate": "{base}/{locale}/job-offers?f=1",
            "offerte_partner": "http://luiss.jobteaser.com/it/job-offers?f=2",  # not https
            "saved_searches": [
                {"name": "Data", "url": "{base}/{locale}/job-offers?q=data"},
                {"name": "Evil", "url": "https://evil.example/job-offers"},
                {"name": "", "url": "https://www.jobteaser.com/en/job-offers?q=ai"},
                "not a mapping",
            ],
        },
    }
    assert pass_links(cfg) == [
        {"label": "Offerte mirate", "url": "https://luiss.jobteaser.com/it/job-offers?f=1"},
        {"label": "Data", "url": "https://luiss.jobteaser.com/it/job-offers?q=data"},
        {"label": "Ricerca salvata", "url": "https://www.jobteaser.com/en/job-offers?q=ai"},
    ]
    assert pass_links({}) == []
    assert pass_links({"links": "nope"}) == []
