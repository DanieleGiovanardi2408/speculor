"""Ashby job boards via the public Posting API.

``GET https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=false``.
There is no single-posting endpoint: single-URL lookups read the (cached) board.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from radar.connectors.base import SourceError
from radar.connectors.http import PoliteClient, html_to_text, parse_datetime, title_matches
from radar.schemas import RawPosting

API = "https://api.ashbyhq.com/posting-api/job-board"
_URL_RE = re.compile(r"^https?://jobs\.ashbyhq\.com/([^/?#]+)(?:/([0-9a-f-]{36}))?", re.I)


def parse_posting_url(url: str) -> tuple[str, str | None] | None:
    """``(slug, posting_id or None)`` for an Ashby-hosted URL, else None."""
    m = _URL_RE.match(url.strip())
    return (unquote(m.group(1)), m.group(2)) if m else None


def _country(job: dict[str, Any]) -> str | None:
    addr = (job.get("address") or {}).get("postalAddress") or {}
    country = addr.get("addressCountry")
    return str(country).strip() if country else None


def _to_raw(job: dict[str, Any], company: str) -> RawPosting:
    secondary = [
        s.get("location") for s in job.get("secondaryLocations") or [] if isinstance(s, dict)
    ]
    text = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml"))
    return RawPosting(
        source="ashby",
        external_id=str(job["id"]),
        url=str(job.get("jobUrl") or ""),
        title=str(job.get("title") or "").strip(),
        company=company,
        location=job.get("location") or None,
        country=_country(job),
        raw_text=str(text or "").replace(" ", " ").strip(),
        department=job.get("department") or job.get("team"),
        employment_type=job.get("employmentType"),
        posted_at=parse_datetime(job.get("publishedAt")),
        extra={
            "apply_url": job.get("applyUrl"),
            "secondary_locations": [s for s in secondary if s],
            "workplace_type": job.get("workplaceType"),
            "is_remote": job.get("isRemote"),
        },
    )


def _board(client: PoliteClient, slug: str) -> list[dict[str, Any]]:
    data = client.get_json(f"{API}/{slug}", {"includeCompensation": "false"})
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceError("ashby: unexpected response format")
    return [j for j in data["jobs"] if isinstance(j, dict) and "id" in j]


class AshbyConnector:
    source = "ashby"

    def __init__(
        self,
        client: PoliteClient,
        company: str,
        slug: str,
        *,
        title_filter: list[str] | None = None,
        label: str = "ashby board",
    ) -> None:
        self.client = client
        self.company = company
        self.slug = slug
        self.title_filter = title_filter
        self.label = label

    def fetch(self) -> list[RawPosting]:
        return [
            _to_raw(job, self.company)
            for job in _board(self.client, self.slug)
            if job.get("isListed", True)
            and title_matches(str(job.get("title") or ""), self.title_filter)
        ]


def fetch_single(
    client: PoliteClient, slug: str, posting_id: str, company: str = ""
) -> RawPosting | None:
    """Find one posting in the board; None when the board no longer lists it."""
    for job in _board(client, slug):
        if str(job["id"]).lower() == posting_id.lower():
            return _to_raw(job, company or slug)
    return None
