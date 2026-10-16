"""Recruitee career sites via the public Careers Site API.

``GET https://<company>.recruitee.com/api/offers/`` returns every published offer with its
description and requirements (HTML), so the board is exhaustive. A slug containing a dot is
taken as the full careers host (custom domain). robots.txt is checked first.
"""

from __future__ import annotations

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


def api_url(slug: str) -> str:
    host = slug if "." in slug else f"{slug}.recruitee.com"
    return f"https://{host}/api/offers/"


def _text(offer: dict[str, Any]) -> str:
    parts = [html_to_text(offer.get("description")), html_to_text(offer.get("requirements"))]
    return "\n\n".join(p for p in parts if p)


def _to_raw(offer: dict[str, Any], company: str) -> RawPosting:
    # Recruitee timestamps look like "2026-10-15 09:31:36 UTC"
    posted = str(offer.get("published_at") or offer.get("created_at") or "").replace(" UTC", "Z")
    return RawPosting(
        source="recruitee",
        external_id=str(offer["id"]),
        url=str(offer.get("careers_url") or ""),
        title=str(offer.get("title") or "").strip(),
        company=company,
        location=offer.get("location") or offer.get("city") or None,
        country=offer.get("country_code") or offer.get("country") or None,
        raw_text=_text(offer),
        department=offer.get("department") or None,
        employment_type=offer.get("employment_type_code") or None,
        posted_at=parse_datetime(posted.replace(" ", "T", 1) if posted else None),
        deadline=parse_date(offer.get("close_at")),
        extra={
            "apply_url": offer.get("careers_apply_url"),
            "remote": offer.get("remote"),
            "hybrid": offer.get("hybrid"),
            "experience": offer.get("experience_code") or None,
        },
    )


def fetch_offers(client: PoliteClient, slug: str) -> list[dict[str, Any]]:
    data = client.get_json(api_url(slug), respect_robots=True)
    if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
        raise SourceError("recruitee: unexpected response format")
    return [
        o
        for o in data["offers"]
        if isinstance(o, dict) and "id" in o and o.get("status", "published") == "published"
    ]


class RecruiteeConnector:
    source = "recruitee"

    def __init__(
        self,
        client: PoliteClient,
        company: str,
        slug: str,
        *,
        title_filter: list[str] | None = None,
        label: str = "recruitee board",
    ) -> None:
        self.client = client
        self.company = company
        self.slug = slug
        self.title_filter = title_filter
        self.label = label

    def fetch(self) -> list[RawPosting]:
        return [
            _to_raw(o, self.company)
            for o in fetch_offers(self.client, self.slug)
            if title_matches(str(o.get("title") or ""), self.title_filter)
        ]
