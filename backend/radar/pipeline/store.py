"""Upsert connector output (RawPosting) into ``postings`` with change events and de-duplication.

* uid = ``<source>:<external_id>``.
* Existing posting: ``last_seen`` is bumped; changes to title/location/deadline/raw_text produce a
  ``changed`` event (raw_text changes are recorded as "content changed", never the text); a
  closed posting that is seen again is ``reopened``.
* New posting: ``new`` event. If the same canonical URL (any source) or the same dedupe key
  (other source) already exists, the group's primary is chosen (seed > ATS > manual >
  linkedin, then earliest) and the others get ``duplicate_of`` = primary. Duplicates are kept
  but excluded from new-counts, the report and scoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, or_, select

from radar.models import ChangeEvent, Company, Posting
from radar.pipeline.normalize import (
    canonical_url,
    content_hash,
    country_code,
    dedupe_key,
    normalize_company,
    zone_for,
)
from radar.schemas import RawPosting
from radar.timeutil import utcnow

ATS_SOURCES = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters", "workday"})
CONTENT_CHANGED = "content changed"

_INTERN_RE = re.compile(
    r"\b(intern\w*|stage|stagiaire|tirocini\w*|praktikum|praktikant\w*|pr[áa]cticas|"
    r"trainee\w*|werkstudent|working student|off[- ]cycle|placement|thesis|tesi)\b",
    re.I,
)
_GRAD_RE = re.compile(r"\b(graduate|new grad|graduates?|neolaureat\w*|early careers?)\b", re.I)


@dataclass
class UpsertOutcome:
    posting: Posting
    kind: str  # new | changed | reopened | unchanged
    changes: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def is_duplicate(self) -> bool:
        return self.posting.duplicate_of is not None


def source_rank(source: str) -> int:
    if source == "seed":
        return 0
    if source in ATS_SOURCES:
        return 1
    if source == "manual":
        return 2
    if source == "linkedin":
        return 4
    return 3


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def infer_type(title: str, employment_type: str | None = None) -> str:
    text = f"{title} {employment_type or ''}"
    if _INTERN_RE.search(text):
        return "Stage"
    if _GRAD_RE.search(text):
        return "Graduate"
    return "Lavoro"


def company_index(session: Session) -> dict[str, int]:
    """normalized company name -> company id."""
    out: dict[str, int] = {}
    for company in session.exec(select(Company)).all():
        if company.id is not None:
            out.setdefault(normalize_company(company.name), company.id)
    return out


def _event(
    session: Session,
    posting: Posting,
    kind: str,
    run_id: int | None,
    now: datetime,
    changes: dict[str, list[Any]] | None = None,
) -> None:
    assert posting.id is not None
    session.add(
        ChangeEvent(posting_id=posting.id, run_id=run_id, kind=kind, changes=changes, at=now)
    )


def upsert_raw(
    session: Session,
    raw: RawPosting,
    run_id: int | None = None,
    now: datetime | None = None,
    companies: dict[str, int] | None = None,
) -> UpsertOutcome:
    now = now or utcnow()
    uid = f"{raw.source}:{raw.external_id}"
    code = country_code(raw.country, raw.location)
    raw_text = raw.raw_text or ""
    title = raw.title.strip()
    chash = content_hash(title, raw.company, raw.location, raw_text)
    existing = session.exec(select(Posting).where(Posting.uid == uid)).first()

    if existing is not None:
        changes: dict[str, list[Any]] = {}
        if existing.title != title:
            changes["title"] = [existing.title, title]
            existing.title = title
        if (existing.location or None) != (raw.location or None):
            changes["location"] = [existing.location, raw.location]
            existing.location = raw.location
            existing.country = raw.country or existing.country
            existing.country_code = code
            existing.zone = zone_for(code)
        if raw.deadline is not None and existing.deadline != raw.deadline:
            changes["deadline"] = [
                existing.deadline.isoformat() if existing.deadline else None,
                raw.deadline.isoformat(),
            ]
            existing.deadline = raw.deadline
        if (existing.raw_text or "") != raw_text:
            changes["raw_text"] = [None, CONTENT_CHANGED]
            existing.raw_text = raw_text
        existing.content_hash = chash
        existing.last_seen = now
        if raw.url and existing.url != raw.url:
            existing.url = raw.url
        kind = "unchanged"
        if existing.status == "closed":
            existing.status = "open"
            existing.closed_at = None
            kind = "reopened"
        session.add(existing)
        session.flush()
        if kind == "reopened":
            _event(session, existing, "reopened", run_id, now)
        if changes:
            _event(session, existing, "changed", run_id, now, changes)
            if kind == "unchanged":
                kind = "changed"
        return UpsertOutcome(existing, kind, changes)

    if companies is None:
        companies = company_index(session)
    start_info = (
        raw.extra.get("start_info") if isinstance(raw.extra.get("start_info"), str) else None
    )
    posting = Posting(
        uid=uid,
        source=raw.source,
        external_id=raw.external_id,
        url=raw.url,
        canonical_url=canonical_url(raw.url) if raw.url else uid,
        title=title,
        company_name=raw.company,
        company_id=companies.get(normalize_company(raw.company)),
        location=raw.location,
        country=raw.country,
        country_code=code,
        zone=zone_for(code),
        type=infer_type(title, raw.employment_type),
        area=raw.department,
        start_info=start_info,
        deadline=raw.deadline,
        via="LinkedIn" if raw.source == "linkedin" else "Sito aziendale",
        raw_text=raw_text,
        content_hash=chash,
        dedupe_key=dedupe_key(raw.company, title, code),
        first_seen=now,
        last_seen=now,
        status="open",
    )
    session.add(posting)
    session.flush()
    _event(session, posting, "new", run_id, now)
    _dedupe(session, posting, run_id, now)
    return UpsertOutcome(posting, "new")


def _valid_key(key: str) -> bool:
    parts = key.split("|")
    return len(parts) >= 2 and bool(parts[0]) and bool(parts[1])


def _dedupe(session: Session, posting: Posting, run_id: int | None, now: datetime) -> None:
    conds = [col(Posting.canonical_url) == posting.canonical_url]
    if _valid_key(posting.dedupe_key):
        conds.append(
            (col(Posting.dedupe_key) == posting.dedupe_key)
            & (col(Posting.source) != posting.source)
        )
    matches = session.exec(
        select(Posting).where(col(Posting.id) != posting.id).where(or_(*conds))
    ).all()
    if not matches:
        return
    primaries: dict[int, Posting] = {}
    for m in matches:
        root = m
        if m.duplicate_of is not None:
            parent = session.get(Posting, m.duplicate_of)
            if parent is not None:
                root = parent
        assert root.id is not None
        primaries[root.id] = root
    group = [*primaries.values(), posting]
    best = min(group, key=lambda p: (source_rank(p.source), _aware(p.first_seen), p.id or 0))
    assert best.id is not None
    for other in group:
        if other.id == best.id:
            continue
        other.duplicate_of = best.id
        session.add(other)
        for child in session.exec(select(Posting).where(Posting.duplicate_of == other.id)).all():
            child.duplicate_of = best.id
            session.add(child)
    if best.id != posting.id and best.status == "closed" and posting.status == "open":
        best.status = "open"
        best.closed_at = None
        best.last_seen = now
        session.add(best)
        session.flush()
        _event(session, best, "reopened", run_id, now)
    session.flush()
