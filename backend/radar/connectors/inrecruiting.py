"""inRecruiting (Zucchetti, intervieweb.it) career pages via the public announcements feed.

Company career pages embed ``https://<host>.intervieweb.it/annunci.php?lang=<l>&d=<domain>&k=<key>
&format=json_en``: a JSON list of every published announcement for that site (title, place,
publish and expiry dates, HTML description). ``k`` is the public site key printed in the page
source, not a credential. Config slug: ``"<host>|<domain>|<key>"`` (``lang`` optional, default
``it``). The list is complete, so the board is exhaustive. robots.txt is checked first (the
shared ``inrecruiting.intervieweb.it`` host disallows ``LAC`` URLs; this feed does not use them).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from radar.connectors.base import SourceError
from radar.connectors.http import PoliteClient, html_to_text, title_matches
from radar.schemas import RawPosting

_HOST_RE = re.compile(r"^[a-z0-9-]+$", re.I)
_ROME = ZoneInfo("Europe/Rome")
_DMY_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})(?:\s*\((\d{2}):(\d{2})\))?")


@dataclass(frozen=True)
class Feed:
    host: str  # subdomain of intervieweb.it, e.g. "acme"
    domain: str  # the ``d`` parameter (company site domain)
    key: str  # the public ``k`` site key

    @property
    def url(self) -> str:
        return f"https://{self.host}.intervieweb.it/annunci.php"


def parse_slug(slug: str) -> Feed | None:
    parts = [p.strip() for p in str(slug).split("|")]
    if len(parts) != 3 or not all(parts) or not _HOST_RE.match(parts[0]):
        return None
    return Feed(parts[0].lower(), parts[1], parts[2])


def _dmy(value: Any) -> datetime | None:
    """``"12-10-2026 (18:01)"`` (Italian local time) -> aware UTC datetime."""
    m = _DMY_RE.search(str(value or ""))
    if not m:
        return None
    day, month, year, hh, mm = m.groups()
    try:
        return datetime(
            int(year), int(month), int(day), int(hh or 0), int(mm or 0), tzinfo=_ROME
        ).astimezone(UTC)
    except ValueError:
        return None


def _deadline(value: Any) -> date | None:
    """Expiry as the local calendar date (no UTC shift)."""
    m = _DMY_RE.search(str(value or ""))
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _text(item: dict[str, Any]) -> str:
    desc = item.get("description")
    if not desc:
        desc = "".join(
            str(item.get(k) or "")
            for k in (
                "company_description",
                "position_description",
                "requirements_description",
                "other_information_description",
            )
        )
    return html_to_text(str(desc or ""))


def _to_raw(item: dict[str, Any], company: str) -> RawPosting:
    place = str(item.get("location") or item.get("city") or "").strip()
    return RawPosting(
        source="inrecruiting",
        external_id=str(item["id"]),
        url=str(item.get("url") or ""),
        title=str(item.get("title") or "").strip(),
        company=company,
        location=place or None,
        country=item.get("nation") or None,
        raw_text=_text(item),
        department=item.get("function") or item.get("team") or None,
        employment_type=item.get("contract_type") or None,
        posted_at=_dmy(item.get("published")),
        deadline=_deadline(item.get("expires")),
        extra={
            "seniority": item.get("seniority_level") or None,
            "region": item.get("region") or None,
            "group_company": item.get("company") or None,
            "remote": item.get("remote_working") or None,
        },
    )


def fetch_items(client: PoliteClient, feed: Feed, lang: str = "it") -> list[dict[str, Any]]:
    data = client.get_json(
        feed.url,
        {"lang": lang, "d": feed.domain, "k": feed.key, "format": "json_en"},
        respect_robots=True,
    )
    if not isinstance(data, list):
        raise SourceError("inrecruiting: unexpected response format")
    return [i for i in data if isinstance(i, dict) and i.get("id")]


class InRecruitingConnector:
    source = "inrecruiting"

    def __init__(
        self,
        client: PoliteClient,
        company: str,
        feed: Feed,
        *,
        lang: str | None = None,
        title_filter: list[str] | None = None,
        label: str = "inrecruiting board",
    ) -> None:
        self.client = client
        self.company = company
        self.feed = feed
        self.lang = lang or "it"
        self.title_filter = title_filter
        self.label = label

    def fetch(self) -> list[RawPosting]:
        return [
            _to_raw(item, self.company)
            for item in fetch_items(self.client, self.feed, self.lang)
            if title_matches(str(item.get("title") or ""), self.title_filter)
        ]
