"""Single-URL open/closed checks for postings not covered by a full board fetch.

``check_open(client, url)`` returns True (open), False (closed) or None (unknown: unsupported
domain, login-walled site, robots.txt disallow, or any error). It never raises.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from radar.connectors import ashby, greenhouse, lever, smartrecruiters
from radar.connectors.http import PoliteClient
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

    return None


def check_open(client: PoliteClient, url: str) -> bool | None:
    try:
        return _check(client, url)
    except Exception as exc:  # never raise: unknown on any failure
        log.info("closed-check: unknown (%s)", type(exc).__name__)
        return None
