"""Shared helpers for routes: batch serialization (no N+1), optional modules, dates."""

from __future__ import annotations

import importlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlmodel import Session, SQLModel, col, select

from radar.api import schemas
from radar.api.routes.skip import split_skip
from radar.models import (
    Application,
    ApplicationPack,
    ChangeEvent,
    Contact,
    Draft,
    FilterResult,
    Posting,
    Score,
)
from radar.timeutil import utcnow

PRIORITY_RANK = {"Alta": 0, "Media": 1, "Bassa": 2}
CHUNK = 500
FOLLOWUP_DAYS = 14


def local_today() -> date:
    """Today in Europe/Amsterdam (the user's week), UTC if tz data is unavailable."""
    try:
        return datetime.now(ZoneInfo("Europe/Amsterdam")).date()
    except ZoneInfoNotFoundError:
        return utcnow().date()


def get_or_404[T: SQLModel](session: Session, model: type[T], obj_id: int, label: str) -> T:
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} {obj_id} not found")
    return obj


def optional_attr(module: str, attr: str) -> Any:
    """Import ``module.attr`` lazily; 501 if a parallel component is not available yet."""
    try:
        mod = importlib.import_module(module)
        return getattr(mod, attr)
    except (ImportError, AttributeError) as exc:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, f"{module}.{attr} is not available: {exc}"
        ) from exc


def style_check(text: str, kind: str, language: str = "en") -> list[str] | None:
    """Run ``radar.llm.style.check(text, kind, language)`` if importable; None if unavailable.

    ``kind`` is a draft kind or ``"cover_letter"``.
    """
    try:
        check = importlib.import_module("radar.llm.style").check
    except (ImportError, AttributeError):
        return None
    result = check(text, kind, language)
    if result is None:
        return []
    return [str(x) for x in result]


def _chunks(ids: Sequence[int]) -> Iterable[Sequence[int]]:
    for i in range(0, len(ids), CHUNK):
        yield ids[i : i + CHUNK]


def latest_scores(session: Session, ids: Sequence[int]) -> dict[int, Score]:
    out: dict[int, Score] = {}
    for chunk in _chunks(ids):
        rows = session.exec(
            select(Score)
            .where(col(Score.posting_id).in_(chunk))
            .order_by(col(Score.created_at), col(Score.id))
        ).all()
        for row in rows:
            out[row.posting_id] = row  # later rows win
    return out


def filter_hits(session: Session, ids: Sequence[int]) -> dict[int, list[FilterResult]]:
    out: dict[int, list[FilterResult]] = defaultdict(list)
    for chunk in _chunks(ids):
        rows = session.exec(
            select(FilterResult)
            .where(col(FilterResult.posting_id).in_(chunk))
            .order_by(col(FilterResult.id))
        ).all()
        for row in rows:
            out[row.posting_id].append(row)
    return out


def pack_ids(session: Session, ids: Sequence[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for chunk in _chunks(ids):
        rows = session.exec(
            select(ApplicationPack.posting_id, ApplicationPack.id).where(
                col(ApplicationPack.posting_id).in_(chunk)
            )
        ).all()
        for posting_id, pack_id in rows:
            if pack_id is not None:
                out[posting_id] = pack_id
    return out


def score_out(score: Score) -> schemas.ScoreOut:
    return schemas.ScoreOut(
        priority=score.priority,
        why_fits=score.why_fits,
        what_doesnt_fit=score.what_doesnt_fit,
        start_window=score.start_window,
        language_requirements=list(score.language_requirements or []),
        warnings=list(score.warnings or []),
        confidence=score.confidence,
        provider=score.provider,
        model=score.model,
        created_at=score.created_at,
    )


def _summary_fields(
    p: Posting, score: Score | None, hits: list[FilterResult], has_pack: bool
) -> dict[str, Any]:
    assert p.id is not None
    return {
        "id": p.id,
        "source": p.source,
        "url": p.url,
        "title": p.title,
        "company_name": p.company_name,
        "location": p.location,
        "country_code": p.country_code,
        "zone": p.zone,
        "type": p.type,
        "area": p.area,
        "deadline": p.deadline,
        "start_info": p.start_info,
        "status": p.status,
        "user_status": p.user_status,
        "filter_state": p.filter_state,
        "first_seen": p.first_seen,
        "last_seen": p.last_seen,
        "note": p.note,
        "priority": score.priority if score else None,
        "score": score_out(score) if score else None,
        "filter_hits": [
            schemas.FilterHit(
                rule_id=h.rule_id,
                action=h.action,
                reason=h.reason,
                evidence=h.evidence,
                overridden=h.overridden,
            )
            for h in hits
        ],
        "has_pack": has_pack,
        "skip_reason": split_skip(p.note)[0],
    }


def summaries(session: Session, postings: Sequence[Posting]) -> list[schemas.PostingSummary]:
    ids = [p.id for p in postings if p.id is not None]
    scores = latest_scores(session, ids)
    hits = filter_hits(session, ids)
    packs = pack_ids(session, ids)
    out = []
    for p in postings:
        assert p.id is not None
        out.append(
            schemas.PostingSummary(
                **_summary_fields(p, scores.get(p.id), hits.get(p.id, []), p.id in packs)
            )
        )
    return out


def summary(session: Session, posting: Posting) -> schemas.PostingSummary:
    return summaries(session, [posting])[0]


def detail(session: Session, posting: Posting) -> schemas.PostingDetail:
    assert posting.id is not None
    ids = [posting.id]
    score = latest_scores(session, ids).get(posting.id)
    hits = filter_hits(session, ids).get(posting.id, [])
    pack_id = pack_ids(session, ids).get(posting.id)
    events = session.exec(
        select(ChangeEvent)
        .where(ChangeEvent.posting_id == posting.id)
        .order_by(col(ChangeEvent.at), col(ChangeEvent.id))
    ).all()
    return schemas.PostingDetail(
        **_summary_fields(posting, score, hits, pack_id is not None),
        raw_text=posting.raw_text,
        via=posting.via,
        history=[
            schemas.ChangeEventOut(
                kind=e.kind,
                changes={k: list(v) for k, v in e.changes.items()} if e.changes else None,
                at=e.at,
                run_id=e.run_id,
            )
            for e in events
        ],
        pack_id=pack_id,
    )


def sort_summaries(
    items: list[schemas.PostingSummary], sort: str = "priority"
) -> list[schemas.PostingSummary]:
    far = date.max

    def by_priority(s: schemas.PostingSummary) -> tuple[Any, ...]:
        rank = PRIORITY_RANK.get(s.priority or "", 3)
        return (rank, s.deadline or far, s.company_name.lower(), s.id)

    if sort == "deadline":
        return sorted(
            items,
            key=lambda s: (s.deadline or far, PRIORITY_RANK.get(s.priority or "", 3), s.id),
        )
    if sort == "company":
        return sorted(items, key=lambda s: (s.company_name.lower(), s.title.lower(), s.id))
    if sort == "first_seen":
        return sorted(items, key=lambda s: (s.first_seen, s.id), reverse=True)
    return sorted(items, key=by_priority)


def followup_due(app: Application, today: date | None = None) -> bool:
    today = today or local_today()
    return (
        app.stage == "Inviata"
        and not app.responded
        and (today - app.date_sent) >= timedelta(days=FOLLOWUP_DAYS)
    )


def application_out(app: Application, today: date | None = None) -> schemas.ApplicationOut:
    assert app.id is not None
    return schemas.ApplicationOut(
        id=app.id,
        posting_id=app.posting_id,
        company=app.company,
        role=app.role,
        city=app.city,
        date_sent=app.date_sent,
        channel=app.channel,
        cv_version=app.cv_version,
        stage=app.stage,
        next_step=app.next_step,
        next_step_date=app.next_step_date,
        notes=app.notes,
        responded=app.responded,
        updated_at=app.updated_at,
        followup_due=followup_due(app, today),
    )


def contact_out(c: Contact) -> schemas.ContactOut:
    assert c.id is not None
    return schemas.ContactOut(
        id=c.id,
        posting_id=c.posting_id,
        name=c.name,
        role=c.role,
        profile_url=c.profile_url,
        why=c.why,
    )


def drafts_out(session: Session, drafts: Sequence[Draft]) -> list[schemas.DraftOut]:
    posting_ids = sorted({d.posting_id for d in drafts if d.posting_id is not None})
    contact_ids = sorted({d.contact_id for d in drafts if d.contact_id is not None})
    postings: dict[int, Posting] = {}
    for chunk in _chunks(posting_ids):
        for p in session.exec(select(Posting).where(col(Posting.id).in_(chunk))).all():
            assert p.id is not None
            postings[p.id] = p
    contacts: dict[int, Contact] = {}
    for chunk in _chunks(contact_ids):
        for c in session.exec(select(Contact).where(col(Contact.id).in_(chunk))).all():
            assert c.id is not None
            contacts[c.id] = c
    out = []
    for d in drafts:
        assert d.id is not None
        posting = postings.get(d.posting_id) if d.posting_id is not None else None
        contact = contacts.get(d.contact_id) if d.contact_id is not None else None
        out.append(
            schemas.DraftOut(
                id=d.id,
                posting_id=d.posting_id,
                pack_id=d.pack_id,
                contact_id=d.contact_id,
                kind=d.kind,
                channel=d.channel,
                recipient=d.recipient,
                text=d.text,
                status=d.status,
                style_issues=list(d.style_issues or []),
                send_result=d.send_result,
                created_at=d.created_at,
                updated_at=d.updated_at,
                sent_at=d.sent_at,
                posting_title=posting.title if posting else None,
                company_name=posting.company_name if posting else None,
                contact_name=contact.name if contact else None,
            )
        )
    return out


def draft_out(session: Session, d: Draft) -> schemas.DraftOut:
    return drafts_out(session, [d])[0]


def pack_out(session: Session, pack: ApplicationPack) -> schemas.PackOut:
    assert pack.id is not None
    posting = get_or_404(session, Posting, pack.posting_id, "Posting")
    contacts = session.exec(
        select(Contact).where(Contact.posting_id == pack.posting_id).order_by(col(Contact.id))
    ).all()
    drafts = session.exec(
        select(Draft)
        .where((Draft.pack_id == pack.id) | (Draft.posting_id == pack.posting_id))
        .order_by(col(Draft.id))
    ).all()
    answers = [
        schemas.FormAnswer(question=str(a.get("question", "")), answer=str(a.get("answer", "")))
        for a in (pack.form_answers or [])
    ]
    return schemas.PackOut(
        id=pack.id,
        posting_id=pack.posting_id,
        cover_letter=pack.cover_letter,
        form_answers=answers,
        cv_version=pack.cv_version,
        apply_link=pack.apply_link,
        language=pack.language,
        style_issues=list(pack.style_issues or []),
        provider=pack.provider,
        model=pack.model,
        updated_at=pack.updated_at,
        posting=summary(session, posting),
        contacts=[contact_out(c) for c in contacts],
        drafts=drafts_out(session, drafts),
    )
