"""LinkedIn job-alert emails -> RawPosting (content of the email only; LinkedIn is never fetched).

Job links look like ``https://www.linkedin.com/comm/jobs/view/<id>/?trackingId=...&refId=...``
(or ``.../jobs/search/?currentJobId=<id>``, possibly wrapped by Outlook safelinks) and are
canonicalized to ``https://www.linkedin.com/jobs/view/<id>``: the same form that
``normalize.canonical_url`` and the LinkedIn guest connector produce (no trailing slash, no
tracking params), so alert postings de-duplicate against LinkedIn and manual postings.
"""

from __future__ import annotations

import re
from typing import Any

from radar.connectors.email_parsers.common import (
    clean_space,
    container_lines,
    find_cards,
    lines_after,
    parse_html,
    pick_title,
    split_dot,
    text_blocks,
    unwrap_url,
)
from radar.schemas import RawPosting

VIA = "linkedin_alert"
JOB_URL = "https://www.linkedin.com/jobs/view/{id}"
#: LinkedIn addresses that send job alerts / job recommendations.
SENDERS = (
    "jobalerts-noreply@linkedin.com",
    "jobs-listings@linkedin.com",
    "jobs-noreply@linkedin.com",
)
#: display names (any language seen so far) of other @linkedin.com job-alert senders
_ALERT_NAMES = re.compile(
    r"job alerts?|jobs? alert|offerte di lavoro|avvisi? (di )?offert|alert (di )?offert", re.I
)
_VIEW_RE = re.compile(
    r"^https?://(?:[a-z]{2,3}\.|www\.)?linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d{6,})",
    re.I,
)
_CURRENT_JOB_RE = re.compile(
    r"^https?://(?:[a-z]{2,3}\.|www\.)?linkedin\.com/(?:comm/)?jobs/[^?#]*\?(?:[^#]*&)?"
    r"currentJobId=(\d{6,})",
    re.I,
)


def is_sender(address: str, display_name: str) -> bool:
    address = address.lower()
    if address in SENDERS:
        return True
    return address.endswith("@linkedin.com") and bool(_ALERT_NAMES.search(display_name))


def _job_id(url: str) -> str | None:
    m = _VIEW_RE.match(url) or _CURRENT_JOB_RE.match(url)
    return m.group(1) if m else None


def _is_job(url: str) -> bool:
    return _job_id(url) is not None


def job_key(href: str) -> tuple[str, str] | None:
    """(job id, canonical ``/jobs/view/<id>`` URL) of a LinkedIn job link, else None."""
    job_id = _job_id(unwrap_url(href, _is_job))
    if job_id is None:
        return None
    return job_id, JOB_URL.format(id=job_id)


def _company_location(rest: list[str]) -> tuple[str, str | None]:
    if not rest:
        return "", None
    parts = split_dot(rest[0])
    if len(parts) >= 2:
        return parts[0], parts[1]
    location = rest[1] if len(rest) > 1 else None
    return parts[0] if parts else rest[0], location


def _posting(
    job_id: str, url: str, title: str, rest: list[str], extra: dict[str, Any]
) -> RawPosting | None:
    if not title:
        return None
    company, location = _company_location(rest)
    snippet = "\n".join([title, *rest[:4]])
    return RawPosting(
        source="email",
        external_id=f"linkedin:{job_id}",
        url=url,
        title=title,
        company=company,
        location=location,
        raw_text=snippet,
        extra={"via": VIA, "linkedin_job_id": job_id, **extra},
    )


def parse(body_html: str | None, body_text: str | None, extra: dict[str, Any]) -> list[RawPosting]:
    out: dict[str, RawPosting] = {}
    if body_html:
        tree = parse_html(body_html)
        for card in find_cards(tree, job_key):
            lines = container_lines(card.container)
            title = pick_title(card, lines)
            rest = lines_after(title, lines)
            if not rest and card.container is not None:
                img = card.container.css_first("img[alt]")
                alt = clean_space(img.attributes.get("alt") or "") if img is not None else ""
                rest = [alt] if alt else []
            posting = _posting(card.key, card.url, title, rest, extra)
            if posting is not None:
                out.setdefault(card.key, posting)
    if not out and body_text:
        for block in text_blocks(body_text, job_key):
            if block.key in out or not block.lines:
                continue
            posting = _posting(block.key, block.url, block.lines[0], block.lines[1:], extra)
            if posting is not None:
                out[block.key] = posting
    return list(out.values())
