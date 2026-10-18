"""Descriptions for seed postings (the manual tracker export carries only title, company and a
note), fetched from the posting's own URL before LLM scoring.

A seed posting is scored only once it has a real description: fetched here, or borrowed from a
duplicate listed by another source (pipeline/dedupe.py). Without one it stays unscored ("da
valutare") and is never scored on the title alone.

Same rules as every other source: ATS URLs go through the ATS public API (``fetch_single``),
any other careers page is fetched once with robots.txt respected and parsed from JSON-LD
``JobPosting`` or the page text. LinkedIn and JobTeaser pages are never fetched here: LinkedIn
descriptions come from ``linkedin_jobs`` (``jobs get`` within its daily quota) and JobTeaser
offers are queued for the JobTeaser connector (offer pages, local session). Counts only in
logs and stats.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlmodel import Session, col, select

from radar.connectors import (
    ashby,
    greenhouse,
    lever,
    personio,
    smartrecruiters,
    workable,
    workday,
)
from radar.connectors.base import SourceError
from radar.connectors.closed import CLOSED_MARKERS, LOGIN_WALLED
from radar.connectors.http import PoliteClient, RobotsDisallowed
from radar.models import Posting, Score
from radar.pipeline.closed import close_posting
from radar.pipeline.dedupe import RICH_TEXT
from radar.pipeline.normalize import content_hash
from radar.schemas import RawPosting
from radar.timeutil import utcnow

log = logging.getLogger(__name__)

DESCRIBE_SOURCES = ("seed",)
DESCRIBE_CAP = 40  # single-page fetches per run
RETRY_AFTER = timedelta(days=7)  # an unavailable page is tried again after a week
MAX_TEXT_CHARS = 20000

FETCHED = "fetched"
UNAVAILABLE = "unavailable"


@dataclass
class Described:
    """Outcome of one fetch: ``raw`` with a description, ``closed``, or ``reason`` why not."""

    raw: RawPosting | None = None
    closed: bool = False
    reason: str = ""


Describer = Callable[[str], Described]


def _channel(url: str) -> str | None:
    """Pages that this step never fetches, by the channel that handles them."""
    host = urlsplit(url).netloc.lower()
    if host.endswith("linkedin.com"):
        return "linkedin"
    if "jobteaser.com" in host:
        return "jobteaser"
    if any(marker in host for marker in LOGIN_WALLED):
        return "login"
    return None


def _from_ats(client: PoliteClient, url: str) -> Described | None:
    """The posting through its ATS API; None when ``url`` is not a supported ATS URL."""
    got: RawPosting | None
    if gh := greenhouse.parse_posting_url(url):
        got = greenhouse.fetch_single(client, gh[0], gh[1])
    elif lv := lever.parse_posting_url(url):
        got = lever.fetch_single(client, lv[0], lv[1], lv[2])
    elif (ab := ashby.parse_posting_url(url)) and ab[1]:
        got = ashby.fetch_single(client, ab[0], ab[1])
    elif sr := smartrecruiters.parse_posting_url(url):
        got = smartrecruiters.fetch_single(client, sr[0], sr[1])
    elif personio.parse_posting_url(url):
        got = personio.fetch_single(client, url)
    elif (wk := workable.parse_posting_url(url)) and wk[0]:
        got = workable.fetch_single(client, wk[0], wk[1])
    elif (wd := workday.parse_posting_url(url)) and wd[1]:
        if not client.robots_allowed(f"{wd[0].api}{wd[1]}"):
            return Described(reason="robots")
        got = workday.fetch_single(client, url)
    else:
        return None
    return Described(raw=got) if got is not None else Described(closed=True)


def _from_page(client: PoliteClient, url: str) -> Described:
    from radar.pipeline.manual import _from_html

    if urlsplit(url).scheme not in ("http", "https"):
        return Described(reason="no_url")
    try:
        res = client.fetch(
            url, ok_statuses=frozenset({200, 404, 410}), respect_robots=True, use_cache=False
        )
    except RobotsDisallowed:
        return Described(reason="robots")
    if res.status in (404, 410):
        return Described(closed=True)
    raw = _from_html(res.url, res.text)
    if raw.extra.get("parsed_from") != "jsonld" and urlsplit(res.url).path in ("", "/"):
        return Described(reason="landing")  # careers home (e.g. ?gh_jid= links), not the job
    if any(marker in (raw.raw_text or "").lower() for marker in CLOSED_MARKERS):
        return Described(closed=True)
    return Described(raw=raw)


def describe_url(client: PoliteClient, url: str) -> Described:
    """Fetch one posting's description. Never raises."""
    channel = _channel(url)
    if channel:
        return Described(reason=channel)
    try:
        return _from_ats(client, url) or _from_page(client, url)
    except SourceError as exc:
        log.info("describe: page not readable (%s)", type(exc).__name__)
        return Described(reason="error")
    except Exception as exc:  # one bad page never stops the run
        log.info("describe: failed (%s)", type(exc).__name__)
        return Described(reason="error")


# ---------------------------------------------------------------------- selection


def has_description(posting: Posting) -> bool:
    """Whether a posting may be scored. Only seed postings can lack one."""
    if posting.source not in DESCRIBE_SOURCES:
        return True
    return posting.description_status == FETCHED or (
        posting.description_from not in (None, posting.uid)
        and len(posting.raw_text or "") >= RICH_TEXT
    )


def _due(posting: Posting, now: datetime) -> bool:
    if posting.description_status != UNAVAILABLE or posting.description_checked_at is None:
        return True
    checked = posting.description_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=now.tzinfo)
    return now - checked >= RETRY_AFTER


def missing_descriptions(
    session: Session, now: datetime | None = None, *, channel: str | None = None
) -> list[Posting]:
    """Open primary seed postings without a description, due for a (re)try, oldest check first.

    ``channel`` = "linkedin" / "jobteaser" selects the pages handled by those sources (no retry
    delay: they are attempted only when that source has budget); None the ones fetched here.
    """
    now = now or utcnow()
    rows = session.exec(
        select(Posting)
        .where(col(Posting.source).in_(DESCRIBE_SOURCES))
        .where(Posting.status == "open")
        .where(col(Posting.duplicate_of).is_(None))
        .order_by(col(Posting.id))
    ).all()
    out = [
        p
        for p in rows
        if not has_description(p)
        and _channel(p.url) == channel
        and (channel is not None or _due(p, now))
    ]
    out.sort(key=lambda p: (p.description_checked_at is not None, p.description_checked_at or now))
    return out


# ---------------------------------------------------------------------- apply


def drop_title_only_scores(session: Session, posting: Posting) -> int:
    """Delete LLM scores given to a seed posting before it had a description."""
    rows = session.exec(select(Score).where(Score.posting_id == posting.id)).all()
    stale = [r for r in rows if r.provider not in ("manual", "rules")]
    for row in stale:
        session.delete(row)
    return len(stale)


def apply_description(posting: Posting, raw: RawPosting) -> bool:
    """Store a fetched description on a seed posting; False when the text is only a snippet."""
    text = (raw.raw_text or "").strip()[:MAX_TEXT_CHARS]
    if len(text) < RICH_TEXT:
        return False
    posting.raw_text = text
    posting.content_hash = content_hash(
        posting.title, posting.company_name, posting.location, posting.raw_text
    )
    if posting.deadline is None and raw.deadline is not None:
        posting.deadline = raw.deadline
    posting.description_status = FETCHED
    return True


def describe_postings(
    session: Session,
    describer: Describer,
    *,
    run_id: int | None = None,
    cap: int = DESCRIBE_CAP,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch descriptions for seed postings that lack one. Returns counts and touched ids."""
    now = now or utcnow()
    todo = missing_descriptions(session, now)
    counts: dict[str, Any] = {
        "candidates": len(todo),
        "checked": 0,
        "fetched": 0,
        "closed": 0,
        "unavailable": 0,
        "scores_dropped": 0,
        "reasons": {},
    }
    touched: list[int] = []
    for posting in todo[:cap]:
        result = describer(posting.url)
        counts["checked"] += 1
        posting.description_checked_at = now
        if result.closed:
            close_posting(session, posting, run_id, now, "describe: page closed")
            counts["closed"] += 1
        elif result.raw is not None and apply_description(posting, result.raw):
            counts["fetched"] += 1
            counts["scores_dropped"] += drop_title_only_scores(session, posting)
        else:
            posting.description_status = UNAVAILABLE
            reason = result.reason or "short"
            counts["reasons"][reason] = counts["reasons"].get(reason, 0) + 1
            counts["unavailable"] += 1
        session.add(posting)
        assert posting.id is not None
        touched.append(posting.id)
        session.commit()
    counts["dropped_unavailable"] = drop_scores_without_description(session)
    counts["touched"] = touched
    log.info(
        "describe: %d candidates, %d checked, %d fetched, %d closed, %d unavailable",
        counts["candidates"],
        counts["checked"],
        counts["fetched"],
        counts["closed"],
        counts["unavailable"],
    )
    return counts


def drop_scores_without_description(session: Session) -> int:
    """LLM scores of seed postings that still have no description (scored on the title alone
    before this step existed): removed, so the posting shows as not scored yet."""
    rows = session.exec(
        select(Posting)
        .where(col(Posting.source).in_(DESCRIBE_SOURCES))
        .where(col(Posting.duplicate_of).is_(None))
    ).all()
    n = 0
    for posting in rows:
        if not has_description(posting):
            n += drop_title_only_scores(session, posting)
    session.commit()
    return n
