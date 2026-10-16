"""Upsert connector output (RawPosting) into ``postings`` with change events and de-duplication.

* uid = ``<source>:<external_id>``.
* Existing posting: ``last_seen`` is bumped; changes to title/location/deadline/raw_text produce a
  ``changed`` event (raw_text changes are recorded as "content changed", never the text); a
  closed posting that is seen again is ``reopened``.
* New posting: ``new`` event, then cross-source de-duplication (pipeline/dedupe.py): if it is
  the same job as an existing group (same canonical URL, or same company/country/city with a
  near-identical title from another source family) it gets ``duplicate_of`` = the group's
  primary, which keeps its id and gains the source (better link, richer description).
  Duplicates are kept but excluded from new-counts, the report and scoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from radar.models import ChangeEvent, Company, Posting
from radar.pipeline import dedupe
from radar.pipeline.dedupe import ATS_SOURCES, CONTENT_CHANGED, source_rank
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

__all__ = ["ATS_SOURCES", "CONTENT_CHANGED", "UpsertOutcome", "source_rank", "upsert_raw"]

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

    @property
    def primary_id(self) -> int | None:
        """The id shown in the app for this job (the group's primary)."""
        return self.posting.duplicate_of or self.posting.id


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
        return _update_existing(session, existing, raw, title, code, chash, run_id, now)

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
        via=_via(raw),
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
    dedupe.link_new(session, posting, run_id, now, via=_alert_via(raw))
    return UpsertOutcome(posting, "new")


def _update_existing(
    session: Session,
    existing: Posting,
    raw: RawPosting,
    title: str,
    code: str | None,
    chash: str,
    run_id: int | None,
    now: datetime,
) -> UpsertOutcome:
    raw_text = raw.raw_text or ""
    primary = existing if existing.duplicate_of is None else dedupe.root_of(session, existing)
    grouped = primary.id != existing.id or len(primary.sources or []) > 1
    # A primary showing a richer member's description: its own (shorter) text is not compared.
    borrowed = existing.duplicate_of is None and existing.description_from not in (
        None,
        existing.uid,
    )
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
    if not borrowed:
        if (existing.raw_text or "") != raw_text:
            changes["raw_text"] = [None, CONTENT_CHANGED]
            existing.raw_text = raw_text
        existing.content_hash = chash
    existing.last_seen = now
    if raw.url and existing.url != raw.url and not grouped:
        existing.url = raw.url
    kind = "unchanged"
    # A non-exhaustive source (LinkedIn search, alerts) still showing a job that every board
    # stopped listing does not reopen it: the boards decide.
    board_closed = (
        grouped
        and not dedupe.is_exhaustive_source(existing.source)
        and dedupe.all_exhaustive_gone(primary)
    )
    if existing.status == "closed" and not board_closed:
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
    _refresh_group(session, primary, existing, raw, kind, run_id, now)
    return UpsertOutcome(existing, kind, changes)


def _refresh_group(
    session: Session,
    primary: Posting,
    member: Posting,
    raw: RawPosting,
    kind: str,
    run_id: int | None,
    now: datetime,
) -> None:
    """Keep the primary's source entry for ``member`` current (seen now, still listed)."""
    dedupe.ensure_entries(session, primary)
    fields: dict[str, Any] = {"last_seen": now.isoformat(), "listed": True}
    if raw.url:
        fields["url"] = raw.url
    dedupe.update_entry(primary, member.uid, **fields)
    session.add(primary)
    if primary.id == member.id and len(primary.sources or []) <= 1:
        return
    dedupe.sync_primary(session, primary, run_id, now)
    if primary.id != member.id and kind == "reopened" and primary.status == "closed":
        dedupe.reopen(session, primary, run_id, now)
    session.flush()


def _alert_via(raw: RawPosting) -> str | None:
    via = raw.extra.get("via")
    return via if isinstance(via, str) and via else None


def _via(raw: RawPosting) -> str:
    """Default application channel shown in the UI."""
    alert = raw.extra.get("via")
    if raw.source in dedupe.LINKEDIN_SOURCES or alert == "linkedin_alert":
        return "LinkedIn"
    if raw.source == "jobteaser" or alert == "jobteaser_alert":
        return "Career Service Luiss"
    return "Sito aziendale"
