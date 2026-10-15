"""Greenhouse job boards via the public Job Board API.

``GET https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true``. The same API host
serves boards hosted on ``job-boards.eu.greenhouse.io`` (there is no ``boards-api.eu`` host).
"""

from __future__ import annotations

import re
from typing import Any

from radar.connectors.base import SourceError
from radar.connectors.http import (
    PoliteClient,
    html_to_text,
    parse_date,
    parse_datetime,
    title_matches,
)
from radar.schemas import RawPosting

API = "https://boards-api.greenhouse.io/v1/boards"
_URL_RE = re.compile(
    r"^https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?"
    r"([A-Za-z0-9_-]+)/jobs/(\d+)",
    re.I,
)


def parse_posting_url(url: str) -> tuple[str, str] | None:
    """``(slug, job_id)`` for a Greenhouse-hosted posting URL, else None."""
    m = _URL_RE.match(url.strip())
    return (m.group(1), m.group(2)) if m else None


def _to_raw(job: dict[str, Any], company: str) -> RawPosting:
    location = (job.get("location") or {}).get("name") or None
    departments = job.get("departments") or []
    offices = job.get("offices") or []
    return RawPosting(
        source="greenhouse",
        external_id=str(job["id"]),
        url=str(job.get("absolute_url") or ""),
        title=str(job.get("title") or "").strip(),
        company=company or str(job.get("company_name") or ""),
        location=location,
        raw_text=html_to_text(job.get("content"), unescape_first=True),
        department=(departments[0].get("name") if departments else None),
        posted_at=parse_datetime(job.get("first_published") or job.get("updated_at")),
        deadline=parse_date(job.get("application_deadline")),
        extra={
            "offices": [o.get("location") or o.get("name") for o in offices],
            "updated_at": job.get("updated_at"),
            "language": job.get("language"),
        },
    )


class GreenhouseConnector:
    source = "greenhouse"

    def __init__(
        self,
        client: PoliteClient,
        company: str,
        slug: str,
        *,
        title_filter: list[str] | None = None,
        label: str = "greenhouse board",
    ) -> None:
        self.client = client
        self.company = company
        self.slug = slug
        self.title_filter = title_filter
        self.label = label

    def fetch(self) -> list[RawPosting]:
        data = self.client.get_json(f"{API}/{self.slug}/jobs", {"content": "true"})
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError("greenhouse: unexpected response format")
        out = []
        for job in data["jobs"]:
            if not isinstance(job, dict) or "id" not in job:
                continue
            if not title_matches(str(job.get("title") or ""), self.title_filter):
                continue
            out.append(_to_raw(job, self.company))
        return out


def fetch_single(
    client: PoliteClient, slug: str, job_id: str, company: str = ""
) -> RawPosting | None:
    """One posting by id; None when the board says 404 (closed / removed)."""
    res = client.fetch(f"{API}/{slug}/jobs/{job_id}", ok_statuses={200, 404})
    if res.status == 404:
        return None
    try:
        job = res.json()
    except ValueError as exc:
        raise SourceError("greenhouse: response is not JSON") from exc
    if not isinstance(job, dict) or "id" not in job:
        raise SourceError("greenhouse: unexpected response format")
    return _to_raw(job, company or str(job.get("company_name") or ""))
