"""Workable career pages via the public jobs widget API.

``GET https://apply.workable.com/api/v1/widget/accounts/<slug>?details=true`` returns the
account's complete list of published jobs with their HTML descriptions (the same endpoint the
embeddable careers widget uses), so the board is exhaustive. robots.txt is checked first.
"""

from __future__ import annotations

import re
from typing import Any

from radar.connectors.base import SourceError
from radar.connectors.http import (
    PoliteClient,
    html_to_text,
    parse_datetime,
    title_matches,
)
from radar.schemas import RawPosting

API = "https://apply.workable.com/api/v1/widget/accounts"
_URL_RE = re.compile(r"^https?://apply\.workable\.com/(?:([a-z0-9_-]+)/)?j/([A-Z0-9]{6,})", re.I)


def parse_posting_url(url: str) -> tuple[str | None, str] | None:
    """``(slug or None, shortcode)`` for a Workable-hosted posting URL, else None."""
    m = _URL_RE.match(url.strip())
    return (m.group(1), m.group(2).upper()) if m else None


def _location(job: dict[str, Any]) -> tuple[str | None, str | None]:
    locs = [loc for loc in job.get("locations") or [] if isinstance(loc, dict)]
    if locs:
        names = [
            ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
            for loc in locs
            if not loc.get("hidden")
        ]
        names = [n for n in names if n]
        return ("; ".join(dict.fromkeys(names)) or None, locs[0].get("countryCode") or None)
    parts = [job.get("city"), job.get("state"), job.get("country")]
    return (", ".join(str(p) for p in parts if p) or None, job.get("country") or None)


def _to_raw(job: dict[str, Any], company: str) -> RawPosting:
    location, country = _location(job)
    return RawPosting(
        source="workable",
        external_id=str(job["shortcode"]),
        url=str(job.get("url") or job.get("shortlink") or ""),
        title=str(job.get("title") or "").strip(),
        company=company,
        location=location,
        country=country,
        raw_text=html_to_text(job.get("description")),
        department=job.get("department") or None,
        employment_type=job.get("employment_type") or None,
        posted_at=parse_datetime(job.get("published_on") or job.get("created_at")),
        extra={
            "apply_url": job.get("application_url"),
            "remote": job.get("telecommuting"),
            "experience": job.get("experience") or None,
            "function": job.get("function") or None,
        },
    )


def fetch_jobs(client: PoliteClient, slug: str) -> list[dict[str, Any]]:
    data = client.get_json(f"{API}/{slug}", {"details": "true"}, respect_robots=True)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceError("workable: unexpected response format")
    return [j for j in data["jobs"] if isinstance(j, dict) and j.get("shortcode")]


class WorkableConnector:
    source = "workable"

    def __init__(
        self,
        client: PoliteClient,
        company: str,
        slug: str,
        *,
        title_filter: list[str] | None = None,
        label: str = "workable board",
    ) -> None:
        self.client = client
        self.company = company
        self.slug = slug
        self.title_filter = title_filter
        self.label = label

    def fetch(self) -> list[RawPosting]:
        return [
            _to_raw(job, self.company)
            for job in fetch_jobs(self.client, self.slug)
            if title_matches(str(job.get("title") or ""), self.title_filter)
        ]


def fetch_single(client: PoliteClient, slug: str, shortcode: str) -> RawPosting | None:
    """One posting looked up in the (cached) board; None when the board no longer lists it."""
    for job in fetch_jobs(client, slug):
        if str(job["shortcode"]).upper() == shortcode.upper():
            return _to_raw(job, slug)
    return None
