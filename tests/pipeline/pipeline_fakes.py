"""Shared fakes for pipeline tests (no network)."""

from __future__ import annotations

from datetime import date
from typing import Any

from radar.schemas import RawPosting

TODAY = date(2026, 10, 15)


def raw(external_id: str, **kw: Any) -> RawPosting:
    data: dict[str, Any] = {
        "source": "greenhouse",
        "external_id": external_id,
        "url": f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        "title": f"Data Science Intern {external_id}",
        "company": "Acme",
        "location": "Milan, Italy",
        "raw_text": "Start date: February 2027. Python and SQL.",
    }
    data.update(kw)
    return RawPosting(**data)


class FakeConnector:
    def __init__(
        self,
        source: str,
        postings: list[RawPosting] | None = None,
        *,
        company: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.source = source
        self.label = f"{source} board"
        self.postings = postings or []
        self.error = error
        if company is not None:
            self.company = company

    def fetch(self) -> list[RawPosting]:
        if self.error is not None:
            raise self.error
        return list(self.postings)
