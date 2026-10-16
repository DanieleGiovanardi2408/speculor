"""LinkedIn job-alert emails -> RawPosting (content of the email only; LinkedIn is never fetched).

Job links look like ``https://www.linkedin.com/comm/jobs/view/<id>/?trackingId=...&refId=...``
and are canonicalized to ``https://www.linkedin.com/jobs/view/<id>`` so they de-duplicate
against manual and LinkedIn postings via ``canonical_url``.
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
SENDERS = (
    "jobalerts-noreply@linkedin.com",
    "jobs-listings@linkedin.com",
    "jobs-noreply@linkedin.com",
)
_VIEW_RE = re.compile(
    r"^https?://(?:[a-z]{2,3}\.|www\.)?linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d{6,})",
    re.I,
)


def is_sender(address: str, display_name: str) -> bool:
    address = address.lower()
    if address in SENDERS:
        return True
    return address.endswith("@linkedin.com") and "job alert" in display_name.lower()


def _is_job(url: str) -> bool:
    return bool(_VIEW_RE.match(url))


def job_key(href: str) -> tuple[str, str] | None:
    url = unwrap_url(href, _is_job)
    m = _VIEW_RE.match(url)
    if not m:
        return None
    return m.group(1), JOB_URL.format(id=m.group(1))


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
