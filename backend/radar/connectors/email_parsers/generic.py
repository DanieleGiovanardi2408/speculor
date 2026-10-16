"""Generic alert emails (configured extra senders): links to ATS postings -> RawPosting.

Only links that look like postings on a supported ATS (Greenhouse, Lever, Ashby, Workday,
SmartRecruiters) are kept. The URL is canonicalized the same way the pipeline does, so a posting
that a board connector also returns is merged by canonical URL.
"""

from __future__ import annotations

from typing import Any

from radar.connectors import ashby, greenhouse, lever, smartrecruiters, workday
from radar.connectors.email_parsers.common import (
    clean_url,
    container_lines,
    find_cards,
    is_noise,
    parse_html,
    pick_title,
    split_dot,
    strip_tracking,
    text_blocks,
)
from radar.schemas import RawPosting

VIA = "generic_alert"


def _ats_key(url: str) -> tuple[str, str] | None:
    """(key, company slug) for a supported ATS posting URL, else None."""
    gh = greenhouse.parse_posting_url(url)
    if gh:
        return f"greenhouse:{gh[0]}:{gh[1]}", gh[0]
    lv = lever.parse_posting_url(url)
    if lv:
        return f"lever:{lv[0]}:{lv[1]}", lv[0]
    ab = ashby.parse_posting_url(url)
    if ab and ab[1]:
        return f"ashby:{ab[0]}:{ab[1]}", ab[0]
    sr = smartrecruiters.parse_posting_url(url)
    if sr:
        return f"smartrecruiters:{sr[0]}:{sr[1]}", sr[0]
    wd = workday.parse_posting_url(url)
    if wd and wd[1]:
        return f"workday:{wd[0].tenant}:{wd[1]}", wd[0].tenant
    return None


def _is_posting(url: str) -> bool:
    return _ats_key(strip_tracking(url)) is not None


def posting_key(href: str) -> tuple[str, str] | None:
    url = clean_url(href, _is_posting)
    got = _ats_key(url)
    return (got[0], url) if got else None


def _company_from_key(key: str) -> str:
    slug = key.split(":", 2)[1]
    return slug.replace("-", " ").replace("_", " ").strip().title()


def _posting(key: str, url: str, title: str, rest: list[str], extra: dict[str, Any]) -> RawPosting:
    company = _company_from_key(key)
    location: str | None = None
    parts = split_dot(rest[0]) if rest else []
    if len(parts) >= 2:  # "FictionalCo · Dublin, Ireland"
        company, location = parts[0], parts[1]
    return RawPosting(
        source="email",
        external_id=key,
        url=url,
        title=title,
        company=company,
        location=location,
        raw_text="\n".join([title, *rest[:4]]),
        extra={"via": VIA, "ats": key.split(":", 1)[0], **extra},
    )


def parse(body_html: str | None, body_text: str | None, extra: dict[str, Any]) -> list[RawPosting]:
    out: dict[str, RawPosting] = {}
    if body_html:
        tree = parse_html(body_html)
        for card in find_cards(tree, posting_key):
            lines = container_lines(card.container)
            title = pick_title(card, lines)
            if not title:
                continue
            rest = [ln for ln in lines if ln != title and not is_noise(ln)]
            out.setdefault(card.key, _posting(card.key, card.url, title, rest, extra))
    if not out and body_text:
        for block in text_blocks(body_text, posting_key):
            if block.key in out or not block.lines:
                continue
            out[block.key] = _posting(block.key, block.url, block.lines[0], block.lines[1:], extra)
    return list(out.values())
