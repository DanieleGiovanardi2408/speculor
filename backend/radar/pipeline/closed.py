"""Closed-posting detection.

(a) By absence: after a connector's ``fetch()`` SUCCEEDED, open postings of the same source
    (and, for ATS boards, the same company) that it did not return are closed. Nothing is
    closed for a connector that failed, and never for non-exhaustive sources (LinkedIn search,
    email alerts). A job listed by several sources (pipeline/dedupe.py) closes only when ALL
    its exhaustive sources stopped listing it; until then only that source's entry is
    marked as not listed.
(b) By URL: other open postings (seed, manual, linkedin) are checked one by one with a
    per-run cap, least-recently-checked first (skipping jobs a board still lists). ``False``
    closes, ``None`` (unknown) leaves the posting unchanged. Last-check times live in the
    ``closed_check:last`` setting.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, select

from radar.db import get_setting, set_setting
from radar.models import ChangeEvent, Posting
from radar.pipeline import dedupe
from radar.pipeline.dedupe import NON_EXHAUSTIVE_SOURCES
from radar.pipeline.normalize import normalize_company
from radar.schemas import RawPosting
from radar.timeutil import utcnow

URL_CHECK_SOURCES = ("seed", "manual", "linkedin", "linkedin_jobs")
CHECK_SETTING = "closed_check:last"
DEFAULT_CHECK_CAP = 80

Checker = Callable[[str], bool | None]


def is_exhaustive(connector: Any) -> bool:
    value = getattr(connector, "exhaustive", None)
    if isinstance(value, bool):
        return value
    return str(getattr(connector, "source", "")) not in NON_EXHAUSTIVE_SOURCES


def board_companies(connector: Any, returned: Iterable[RawPosting]) -> set[str] | None:
    """Normalized company names a board covers; None = the board covers the whole source."""
    names: set[str] = set()
    company = getattr(connector, "company", None) or getattr(connector, "company_name", None)
    if isinstance(company, str) and company.strip():
        names.add(normalize_company(company))
    if names:
        return names
    for raw in returned:
        if raw.company.strip():
            names.add(normalize_company(raw.company))
    if getattr(connector, "single_board", False) and not names:
        return None
    return names


def close_posting(
    session: Session, posting: Posting, run_id: int | None, now: datetime, how: str
) -> None:
    posting.status = "closed"
    posting.closed_at = now
    session.add(posting)
    assert posting.id is not None
    session.add(
        ChangeEvent(
            posting_id=posting.id,
            run_id=run_id,
            kind="closed",
            changes={"via": [None, how]},
            at=now,
        )
    )


def close_missing(
    session: Session,
    *,
    source: str,
    companies: set[str] | None,
    returned_uids: set[str],
    run_id: int | None,
    now: datetime | None = None,
) -> int:
    """Close open postings of ``source`` (limited to ``companies``) not in ``returned_uids``.

    ``companies`` = None closes across the whole source; an empty set closes nothing.
    """
    if companies is not None and not companies:
        return 0
    now = now or utcnow()
    closed = 0
    rows = session.exec(
        select(Posting).where(Posting.source == source).where(Posting.status == "open")
    ).all()
    for posting in rows:
        if posting.uid in returned_uids:
            continue
        if companies is not None and normalize_company(posting.company_name) not in companies:
            continue
        closed += _close_absent(session, posting, run_id, now)
    session.flush()
    return closed


def _close_absent(session: Session, posting: Posting, run_id: int | None, now: datetime) -> int:
    """Close one absent row; its group's primary only when no exhaustive source lists it."""
    primary = posting if posting.duplicate_of is None else dedupe.root_of(session, posting)
    if primary.id == posting.id and len(primary.sources or []) <= 1:
        if primary.sources:
            dedupe.update_entry(primary, posting.uid, listed=False)
        close_posting(session, posting, run_id, now, "absent from board")
        return 1
    dedupe.ensure_entries(session, primary)
    dedupe.update_entry(primary, posting.uid, listed=False)
    session.add(primary)
    n = 0
    if primary.id != posting.id:
        close_posting(session, posting, run_id, now, "absent from board")
        n += 1
    if primary.status == "open" and dedupe.all_exhaustive_gone(primary):
        close_posting(session, primary, run_id, now, "absent from all sources")
        n += 1
    elif primary.status == "open":
        dedupe.sync_primary(session, primary, run_id, now)  # best link among listed sources
    return n


def _parse_iso(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=UTC)


def check_open_postings(
    session: Session,
    checker: Checker,
    *,
    run_id: int | None,
    cap: int = DEFAULT_CHECK_CAP,
    sources: Iterable[str] = URL_CHECK_SOURCES,
    exclude_ids: Iterable[int] = (),
    now: datetime | None = None,
) -> dict[str, int]:
    """URL-check open postings not covered by a board fetch. Returns counts only."""
    now = now or utcnow()
    excluded = set(exclude_ids)
    stored = get_setting(CHECK_SETTING, {}, session=session)
    last: dict[str, str] = dict(stored) if isinstance(stored, dict) else {}
    candidates = [
        p
        for p in session.exec(
            select(Posting)
            .where(col(Posting.source).in_(list(sources)))
            .where(Posting.status == "open")
            .where(col(Posting.duplicate_of).is_(None))
            .order_by(col(Posting.id))
        ).all()
        if p.id is not None
        and p.id not in excluded
        and p.url.startswith("http")
        and not dedupe.covered_by_board(p)
    ]
    candidates.sort(key=lambda p: _parse_iso(last.get(str(p.id))))
    counts = {"candidates": len(candidates), "checked": 0, "open": 0, "closed": 0, "unknown": 0}
    for posting in candidates[: max(0, cap)]:
        try:
            result = checker(posting.url)
        except Exception:
            result = None
        counts["checked"] += 1
        last[str(posting.id)] = now.isoformat()
        if result is False:
            close_posting(session, posting, run_id, now, "url check")
            counts["closed"] += 1
        elif result is True:
            counts["open"] += 1
        else:
            counts["unknown"] += 1
    open_ids = {str(p.id) for p in candidates}
    set_setting(session, CHECK_SETTING, {k: v for k, v in last.items() if k in open_ids})
    session.flush()
    return counts
