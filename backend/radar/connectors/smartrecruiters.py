"""SmartRecruiters via the public Posting API.

List: ``GET https://api.smartrecruiters.com/v1/companies/<id>/postings?offset=&limit=100``
(paginated). Details (description) are one request per posting, so they are fetched only for
postings whose title passes the title prefilter, capped at ``max_details`` per run.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from radar.connectors.base import SourceError
from radar.connectors.http import (
    DEFAULT_TITLE_FILTER,
    PoliteClient,
    html_to_text,
    parse_datetime,
    title_matches,
)
from radar.schemas import RawPosting

log = logging.getLogger(__name__)

API = "https://api.smartrecruiters.com/v1/companies"
PAGE_SIZE = 100
MAX_PAGES = 30
_URL_RE = re.compile(r"^https?://(?:jobs|careers)\.smartrecruiters\.com/([^/?#]+)/(\d+)", re.I)
_SECTIONS = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")


def parse_posting_url(url: str) -> tuple[str, str] | None:
    """``(company_identifier, posting_id)`` for a SmartRecruiters posting URL, else None."""
    m = _URL_RE.match(url.strip())
    return (m.group(1), m.group(2)) if m else None


def _location(loc: dict[str, Any]) -> str | None:
    full = loc.get("fullLocation")
    if full:
        return re.sub(r"(,\s*)+", ", ", str(full)).strip(", ") or None
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    return ", ".join(str(p) for p in parts if p) or None


def _description(detail: dict[str, Any]) -> str:
    sections = ((detail.get("jobAd") or {}).get("sections")) or {}
    chunks: list[str] = []
    for key in _SECTIONS:
        sec = sections.get(key) or {}
        body = html_to_text(sec.get("text"))
        if body:
            title = sec.get("title")
            chunks.append(f"{title}\n{body}" if title else body)
    return "\n\n".join(chunks)


def _to_raw(item: dict[str, Any], company: str, detail: dict[str, Any] | None) -> RawPosting:
    src = detail or item
    loc = src.get("location") or {}
    country = loc.get("country")
    ident = (src.get("company") or {}).get("identifier") or ""
    url = (detail or {}).get(
        "postingUrl"
    ) or f"https://jobs.smartrecruiters.com/{ident}/{item['id']}"
    return RawPosting(
        source="smartrecruiters",
        external_id=str(item["id"]),
        url=str(url),
        title=str(src.get("name") or "").strip(),
        company=company,
        location=_location(loc),
        country=str(country).upper() if country else None,
        raw_text=_description(detail) if detail else "",
        department=((src.get("department") or {}).get("label")),
        employment_type=((src.get("typeOfEmployment") or {}).get("label")),
        posted_at=parse_datetime(src.get("releasedDate")),
        extra={
            "experience_level": (src.get("experienceLevel") or {}).get("label"),
            "function": (src.get("function") or {}).get("label"),
            "remote": loc.get("remote"),
            "detail_fetched": detail is not None,
            "apply_url": (detail or {}).get("applyUrl"),
        },
    )


class SmartRecruitersConnector:
    source = "smartrecruiters"

    def __init__(
        self,
        client: PoliteClient,
        company: str,
        slug: str,
        *,
        title_filter: list[str] | None = None,
        max_details: int = 60,
        page_size: int = PAGE_SIZE,
        label: str = "smartrecruiters board",
    ) -> None:
        self.client = client
        self.company = company
        self.slug = slug
        # SmartRecruiters always needs a prefilter (one detail request per posting).
        self.title_filter = list(title_filter) if title_filter else list(DEFAULT_TITLE_FILTER)
        self.max_details = max_details
        self.page_size = page_size
        self.label = label

    def list_postings(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            data = self.client.get_json(
                f"{API}/{self.slug}/postings", {"offset": offset, "limit": self.page_size}
            )
            if not isinstance(data, dict) or not isinstance(data.get("content"), list):
                raise SourceError("smartrecruiters: unexpected response format")
            page = [p for p in data["content"] if isinstance(p, dict) and "id" in p]
            items.extend(page)
            total = int(data.get("totalFound") or 0)
            offset += self.page_size
            if not page or offset >= total:
                break
        return items

    def fetch(self) -> list[RawPosting]:
        items = [
            p
            for p in self.list_postings()
            if title_matches(str(p.get("name") or ""), self.title_filter)
        ]
        out: list[RawPosting] = []
        details = 0
        for item in items:
            detail: dict[str, Any] | None = None
            if details < self.max_details:
                details += 1
                try:
                    got = self.client.get_json(f"{API}/{self.slug}/postings/{item['id']}")
                    detail = got if isinstance(got, dict) else None
                except SourceError:
                    # keep the list entry without description; stop hammering after a failure
                    detail = None
                    details = self.max_details
            out.append(_to_raw(item, self.company, detail))
        if len(items) > self.max_details:
            log.info("smartrecruiters: detail cap reached (%d of %d)", self.max_details, len(items))
        return out


def fetch_single(
    client: PoliteClient, slug: str, posting_id: str, company: str = ""
) -> RawPosting | None:
    """One posting; None when SmartRecruiters answers 404 or marks it inactive."""
    res = client.fetch(f"{API}/{slug}/postings/{posting_id}", ok_statuses={200, 404})
    if res.status == 404:
        return None
    try:
        detail = res.json()
    except ValueError as exc:
        raise SourceError("smartrecruiters: response is not JSON") from exc
    if not isinstance(detail, dict) or "id" not in detail:
        raise SourceError("smartrecruiters: unexpected response format")
    if detail.get("active") is False:
        return None
    name = company or str((detail.get("company") or {}).get("name") or slug)
    return _to_raw(detail, name, detail)
