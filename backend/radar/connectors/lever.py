"""Lever postings via the public Postings API.

``GET https://api.lever.co/v0/postings/<slug>?mode=json`` (global) or
``https://api.eu.lever.co/v0/postings/<slug>?mode=json`` for boards hosted on ``jobs.eu.lever.co``.
"""

from __future__ import annotations

import re
from typing import Any

from radar.connectors.base import SourceError
from radar.connectors.http import PoliteClient, html_to_text, parse_datetime, title_matches
from radar.schemas import RawPosting

_URL_RE = re.compile(r"^https?://jobs(\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)/([0-9a-f-]{36})", re.I)


def api_base(region: str | None) -> str:
    return (
        "https://api.eu.lever.co/v0/postings"
        if region == "eu"
        else "https://api.lever.co/v0/postings"
    )


def parse_posting_url(url: str) -> tuple[str, str, str | None] | None:
    """``(slug, posting_id, region)`` for a Lever-hosted posting URL, else None."""
    m = _URL_RE.match(url.strip())
    if not m:
        return None
    return m.group(2), m.group(3), ("eu" if m.group(1) else None)


def _text(job: dict[str, Any]) -> str:
    parts: list[str] = []
    desc = job.get("descriptionPlain") or html_to_text(job.get("description"))
    if desc:
        parts.append(str(desc).strip())
    for lst in job.get("lists") or []:
        if not isinstance(lst, dict):
            continue
        body = html_to_text(f"<ul>{lst.get('content') or ''}</ul>")
        parts.append(f"{lst.get('text') or ''}\n{body}".strip())
    extra = job.get("additionalPlain") or html_to_text(job.get("additional"))
    if extra:
        parts.append(str(extra).strip())
    return "\n\n".join(p.replace(" ", " ") for p in parts if p)


def _to_raw(job: dict[str, Any], company: str) -> RawPosting:
    cats = job.get("categories") or {}
    locations = cats.get("allLocations") or []
    location = cats.get("location") or (", ".join(locations) if locations else None)
    return RawPosting(
        source="lever",
        external_id=str(job["id"]),
        url=str(job.get("hostedUrl") or ""),
        title=str(job.get("text") or "").strip(),
        company=company,
        location=location,
        country=job.get("country") or None,
        raw_text=_text(job),
        department=cats.get("department") or cats.get("team"),
        employment_type=cats.get("commitment"),
        posted_at=parse_datetime(job.get("createdAt")),
        extra={
            "apply_url": job.get("applyUrl"),
            "workplace_type": job.get("workplaceType"),
            "all_locations": locations,
            "team": cats.get("team"),
        },
    )


class LeverConnector:
    source = "lever"

    def __init__(
        self,
        client: PoliteClient,
        company: str,
        slug: str,
        *,
        region: str | None = None,
        title_filter: list[str] | None = None,
        label: str = "lever board",
    ) -> None:
        self.client = client
        self.company = company
        self.slug = slug
        self.region = region
        self.title_filter = title_filter
        self.label = label

    def fetch(self) -> list[RawPosting]:
        data = self.client.get_json(f"{api_base(self.region)}/{self.slug}", {"mode": "json"})
        if not isinstance(data, list):
            raise SourceError("lever: unexpected response format")
        return [
            _to_raw(job, self.company)
            for job in data
            if isinstance(job, dict)
            and "id" in job
            and title_matches(str(job.get("text") or ""), self.title_filter)
        ]


def fetch_single(
    client: PoliteClient, slug: str, posting_id: str, region: str | None = None, company: str = ""
) -> RawPosting | None:
    """One posting; None when Lever answers 404 (closed / removed)."""
    res = client.fetch(
        f"{api_base(region)}/{slug}/{posting_id}", {"mode": "json"}, ok_statuses={200, 404}
    )
    if res.status == 404:
        return None
    try:
        job = res.json()
    except ValueError as exc:
        raise SourceError("lever: response is not JSON") from exc
    if not isinstance(job, dict) or "id" not in job:
        raise SourceError("lever: unexpected response format")
    return _to_raw(job, company or slug)
