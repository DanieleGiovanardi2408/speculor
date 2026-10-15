"""LinkedIn public (guest) job search, low volume.

Search: ``https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search`` (HTML job cards)
Detail: ``https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<id>`` (HTML; description).

Polite-collection rules: robots.txt is checked BEFORE any request to these paths. LinkedIn's
robots.txt ends with ``User-agent: * / Disallow: /`` (only whitelisted crawlers are allowed),
so with an honest user agent this connector reports a SourceError instead of fetching. The
parsing code is kept (and tested on fixtures) so it works if access is ever granted.
No login, cookies, proxies or browser-UA spoofing.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from radar.connectors.base import SourceError
from radar.connectors.http import PoliteClient, html_to_text, parse_datetime, title_matches
from radar.schemas import RawPosting

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
JOB_URL = "https://www.linkedin.com/jobs/view/{id}"
_URN_RE = re.compile(r"jobPosting:(\d+)")
_VIEW_RE = re.compile(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d+)", re.I)
CLOSED_MARKERS = ("no longer accepting applications", "closed-job__flavor--closed")


def parse_job_id(url: str) -> str | None:
    """Numeric job id from a ``linkedin.com/jobs/view/...`` URL, else None."""
    m = _VIEW_RE.search(url)
    if m:
        return m.group(1)
    m = re.search(r"currentJobId=(\d+)", url)
    return m.group(1) if m else None


def _text(node: LexborNode | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(deep=True, separator=" ")).strip()


def _clean_url(href: str) -> str:
    parts = urlsplit(href)
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path, "", ""))


def parse_search_cards(html: str) -> list[dict[str, Any]]:
    """Parse the guest search HTML into card dicts (id, title, company, location, date, url)."""
    tree = LexborHTMLParser(html)
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in tree.css("[data-entity-urn]"):
        m = _URN_RE.search(node.attributes.get("data-entity-urn") or "")
        if not m or m.group(1) in seen:
            continue
        job_id = m.group(1)
        seen.add(job_id)
        link = node.css_first("a.base-card__full-link") or node.css_first("a[href*='/jobs/view/']")
        href = (link.attributes.get("href") if link is not None else None) or JOB_URL.format(
            id=job_id
        )
        time_node = node.css_first("time")
        cards.append(
            {
                "id": job_id,
                "title": _text(node.css_first(".base-search-card__title, h3")),
                "company": _text(node.css_first(".base-search-card__subtitle, h4")),
                "location": _text(node.css_first(".job-search-card__location")) or None,
                "date": time_node.attributes.get("datetime") if time_node is not None else None,
                "url": _clean_url(href),
            }
        )
    return cards


def parse_detail(html: str) -> dict[str, Any]:
    """Parse a guest jobPosting page: description, criteria, closed flag."""
    tree = LexborHTMLParser(html)
    markup = tree.css_first(".show-more-less-html__markup") or tree.css_first(".description__text")
    criteria: dict[str, str] = {}
    for item in tree.css(".description__job-criteria-item"):
        key = _text(item.css_first(".description__job-criteria-subheader"))
        val = _text(item.css_first(".description__job-criteria-text"))
        if key:
            criteria[key] = val
    lowered = html.lower()
    return {
        "title": _text(tree.css_first(".top-card-layout__title, .topcard__title")) or None,
        "company": _text(tree.css_first(".topcard__org-name-link, .topcard__flavor")) or None,
        "description": html_to_text(markup.html if markup is not None else ""),
        "criteria": criteria,
        "closed": any(marker in lowered for marker in CLOSED_MARKERS),
    }


class LinkedInGuestConnector:
    source = "linkedin"

    def __init__(
        self,
        client: PoliteClient,
        queries: list[dict[str, str]],
        *,
        max_details: int = 5,
        time_range: str = "r604800",
        title_filter: list[str] | None = None,
        label: str = "linkedin guest search",
    ) -> None:
        self.client = client
        self.queries = queries
        self.max_details = max_details
        self.time_range = time_range
        self.title_filter = title_filter
        self.label = label

    def _search_url(self, query: dict[str, str]) -> str:
        params = {
            "keywords": query.get("keywords", ""),
            "location": query.get("location", ""),
            "f_TPR": self.time_range,
            "start": "0",
        }
        return f"{SEARCH_URL}?{urlencode(params)}"

    def fetch(self) -> list[RawPosting]:
        if not self.queries:
            return []
        if not self.client.robots_allowed(self._search_url(self.queries[0])):
            raise SourceError(
                "linkedin: robots.txt disallows /jobs-guest/ for our user agent; not fetching"
            )
        cards: dict[str, dict[str, Any]] = {}
        for query in self.queries:
            html = self.client.get_text(self._search_url(query), respect_robots=True)
            for card in parse_search_cards(html):
                if title_matches(card["title"], self.title_filter):
                    cards.setdefault(card["id"], card)
        out: list[RawPosting] = []
        details = 0
        for card in cards.values():
            detail: dict[str, Any] | None = None
            if details < self.max_details:
                details += 1
                try:
                    detail = parse_detail(
                        self.client.get_text(DETAIL_URL.format(id=card["id"]), respect_robots=True)
                    )
                except SourceError:
                    details = self.max_details  # stop detail requests after a failure
            if detail and detail["closed"]:
                continue
            criteria = (detail or {}).get("criteria") or {}
            out.append(
                RawPosting(
                    source="linkedin",
                    external_id=card["id"],
                    url=JOB_URL.format(id=card["id"]),
                    title=card["title"],
                    company=card["company"],
                    location=card["location"],
                    raw_text=(detail or {}).get("description") or "",
                    employment_type=criteria.get("Employment type"),
                    posted_at=parse_datetime(card["date"]),
                    extra={
                        "seniority": criteria.get("Seniority level"),
                        "detail_fetched": detail is not None,
                    },
                )
            )
        log.info("linkedin: %d cards, %d details", len(cards), min(details, len(cards)))
        return out
