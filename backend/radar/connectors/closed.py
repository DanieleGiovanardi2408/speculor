"""Single-URL open/closed checks for postings not covered by a full board fetch.

``check_open(client, url)`` returns True (open), False (closed) or None (unknown: login-walled
site, robots.txt disallow, a JavaScript-only page, or any error). It never raises. ATS URLs use
the ATS API; any other careers page gets a generic HTML check (status, redirect, markers).
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from radar.connectors import (
    ashby,
    greenhouse,
    lever,
    personio,
    smartrecruiters,
    workable,
    workday,
)
from radar.connectors.http import PoliteClient, RobotsDisallowed, html_to_text
from radar.connectors.linkedin_guest import DETAIL_URL, parse_detail, parse_job_id

log = logging.getLogger(__name__)

# Sites that need a login (or block anonymous access): never fetched, always unknown.
LOGIN_WALLED = ("jobteaser.com", "glassdoor.", "indeed.")


def _check(client: PoliteClient, url: str) -> bool | None:
    host = urlsplit(url).netloc.lower()
    if any(marker in host for marker in LOGIN_WALLED):
        return None

    if host.endswith("linkedin.com"):
        job_id = parse_job_id(url)
        if not job_id:
            return None
        detail_url = DETAIL_URL.format(id=job_id)
        if not client.robots_allowed(detail_url):
            return None  # honest UA is disallowed by robots.txt: we do not know
        res = client.fetch(detail_url, ok_statuses={200, 404, 410})
        if res.status in (404, 410):
            return False
        return not parse_detail(res.text)["closed"]

    gh = greenhouse.parse_posting_url(url)
    if gh:
        return greenhouse.fetch_single(client, gh[0], gh[1]) is not None

    lv = lever.parse_posting_url(url)
    if lv:
        return lever.fetch_single(client, lv[0], lv[1], lv[2]) is not None

    ab = ashby.parse_posting_url(url)
    if ab:
        if not ab[1]:
            return None  # board URL, not a posting
        return ashby.fetch_single(client, ab[0], ab[1]) is not None

    sr = smartrecruiters.parse_posting_url(url)
    if sr:
        return smartrecruiters.fetch_single(client, sr[0], sr[1]) is not None

    if personio.parse_posting_url(url):
        return personio.fetch_single(client, url) is not None

    wk = workable.parse_posting_url(url)
    if wk:
        if not wk[0]:
            return None  # short link without the account slug: board unknown
        return workable.fetch_single(client, wk[0], wk[1]) is not None

    wd = workday.parse_posting_url(url)
    if wd:
        if not wd[1]:
            return None  # board URL, not a posting
        if not client.robots_allowed(f"{wd[0].api}{wd[1]}"):
            return None  # robots.txt disallows the CXS path: we do not know
        return workday.fetch_single(client, url) is not None

    return _check_generic(client, url)


# Generic company career pages: closed markers in EN / IT / FR / DE / ES / NL.
CLOSED_MARKERS = (
    "no longer accepting applications",
    "no longer available",
    "no longer open",
    "this job has expired",
    "this position has been filled",
    "position has been filled",
    "job has been closed",
    "job is closed",
    "posting has expired",
    "vacancy has been closed",
    "job not found",
    "offerta scaduta",
    "offerta non più disponibile",
    "annuncio scaduto",
    "non è più disponibile",
    "posizione chiusa",
    "candidature chiuse",
    "cette offre n'est plus disponible",
    "offre expirée",
    "stelle ist nicht mehr verfügbar",
    "nicht mehr verfügbar",
    "esta oferta ya no está disponible",
    "vacature is gesloten",
)
_ID_SEGMENT = re.compile(
    r"(?:\d{4,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|R\d{3,})", re.I
)
MIN_TEXT_CHARS = 400  # below this the page is a JavaScript shell: we cannot tell


def _check_generic(client: PoliteClient, url: str) -> bool | None:
    """Any other careers page, robots.txt respected: 404/410 or a redirect that loses the job
    id -> closed; a closed marker in the text -> closed; a real page at the same address ->
    open; an empty JavaScript shell -> unknown."""
    if urlsplit(url).scheme not in ("http", "https"):
        return None
    try:
        res = client.fetch(
            url, ok_statuses=frozenset({200, 404, 410}), respect_robots=True, use_cache=False
        )
    except RobotsDisallowed:
        return None
    if res.status in (404, 410):
        return False
    ids = set(_ID_SEGMENT.findall(urlsplit(url).path + "?" + urlsplit(url).query))
    final = urlsplit(res.url)
    if ids and not any(i in final.path + "?" + final.query for i in ids):
        return False  # redirected to a listing or the careers home: the job is gone
    text = html_to_text(res.text).lower()
    if any(marker in text for marker in CLOSED_MARKERS):
        return False
    if len(text) < MIN_TEXT_CHARS:
        return None
    return True


def check_open(client: PoliteClient, url: str) -> bool | None:
    try:
        return _check(client, url)
    except Exception as exc:  # never raise: unknown on any failure
        log.info("closed-check: unknown (%s)", type(exc).__name__)
        return None
