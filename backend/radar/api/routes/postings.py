"""Postings: list/detail, manual add, triage, override, mark-sent, application pack."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import col, func, or_, select

from radar.api import schemas
from radar.api.deps import SessionDep
from radar.api.routes.common import (
    application_out,
    detail,
    get_or_404,
    local_today,
    optional_attr,
    pack_out,
    sort_summaries,
    summaries,
)
from radar.api.routes.skip import compose_note, split_skip
from radar.models import Application, ApplicationPack, FilterResult, Posting
from radar.pipeline.dedupe import public_sources
from radar.pipeline.filters import POST_GRADUATION_ID
from radar.timeutil import utcnow

router = APIRouter(prefix="/postings", tags=["postings"])


def _multi(value: str | None) -> list[str]:
    """Query values accept a single value or a comma-separated list."""
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


@router.get("", response_model=list[schemas.PostingSummary])
def list_postings(
    session: SessionDep,
    priority: str | None = None,
    zone: str | None = None,
    type_: Annotated[str | None, Query(alias="type")] = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    source: str | None = None,
    user_status: str | None = None,
    q: str | None = None,
    sort: schemas.PostingSort = "priority",
    include_rejected: bool = False,
    only_rejected: bool = False,
    track: schemas.TrackFilter = "internship",
) -> list[schemas.PostingSummary]:
    stmt = select(Posting).where(col(Posting.duplicate_of).is_(None))
    if only_rejected:
        stmt = stmt.where(Posting.filter_state == "rejected")
    elif not include_rejected:
        stmt = stmt.where(Posting.filter_state != "rejected")
    for column, raw in (
        (Posting.zone, zone),
        (Posting.type, type_),
        (Posting.status, status_),
        (Posting.user_status, user_status),
    ):
        values = _multi(raw)
        if values:
            stmt = stmt.where(col(column).in_(values))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(col(Posting.title)).like(like),
                func.lower(col(Posting.company_name)).like(like),
                func.lower(func.coalesce(col(Posting.location), "")).like(like),
            )
        )
    rows = session.exec(stmt).all()
    wanted_sources = set(_multi(source))
    if wanted_sources:  # any source of the job (a LinkedIn-first job also listed on the board)
        rows = [p for p in rows if wanted_sources & {e["source"] for e in public_sources(p)}]
    items = summaries(session, rows)
    if track != "all":
        items = [s for s in items if s.track == track]
    wanted = _multi(priority)
    if wanted:
        want_unscored = "none" in {w.lower() for w in wanted}
        items = [s for s in items if s.priority in wanted or (want_unscored and s.priority is None)]
    return sort_summaries(items, sort)


@router.get("/{posting_id}", response_model=schemas.PostingDetail)
def get_posting(posting_id: int, session: SessionDep) -> schemas.PostingDetail:
    return detail(session, get_or_404(session, Posting, posting_id, "Posting"))


@router.post("", response_model=schemas.PostingDetail)
def add_posting(body: schemas.PostingCreate, session: SessionDep) -> schemas.PostingDetail:
    add_from_url = optional_attr("radar.pipeline.manual", "add_posting_from_url")
    try:
        posting: Posting = add_from_url(session, body.url)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except Exception as exc:  # network / parsing failures from the fetcher
        session.rollback()
        if type(exc).__name__ == "SourceError":
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        raise
    session.flush()
    try:
        apply_filters = optional_attr("radar.pipeline.filters", "apply_filters")
    except HTTPException:
        apply_filters = None
    if apply_filters is not None:
        apply_filters(session, posting)
    session.commit()
    session.refresh(posting)
    return detail(session, posting)


@router.patch("/{posting_id}", response_model=schemas.PostingDetail)
def patch_posting(
    posting_id: int, body: schemas.PostingPatch, session: SessionDep
) -> schemas.PostingDetail:
    posting = get_or_404(session, Posting, posting_id, "Posting")
    fields = body.model_fields_set
    if "user_status" in fields and body.user_status is not None:
        posting.user_status = body.user_status
    if "status" in fields and body.status is not None and body.status != posting.status:
        posting.status = body.status
        posting.closed_at = utcnow() if body.status == "closed" else None
    # Skip reasons live in a "[Saltato: reason]" note prefix (no extra column).
    reason, note_body = split_skip(posting.note)
    if "note" in fields:
        given_reason, note_body = split_skip(body.note)
        reason = given_reason or reason
    if body.skip_reason and body.skip_reason.strip():
        reason = body.skip_reason.strip()
        if body.user_status is None:
            posting.user_status = "skipped"
    if posting.user_status != "skipped":
        reason = None
    posting.note = compose_note(reason, note_body)
    session.add(posting)
    session.commit()
    session.refresh(posting)
    return detail(session, posting)


@router.post("/{posting_id}/override", response_model=schemas.PostingDetail)
def override_posting(posting_id: int, session: SessionDep) -> schemas.PostingDetail:
    """Keep it anyway: reject hits and a post_graduation flag (-> internship track) are
    marked overridden."""
    posting = get_or_404(session, Posting, posting_id, "Posting")
    hits = session.exec(
        select(FilterResult).where(
            FilterResult.posting_id == posting_id,
            or_(
                col(FilterResult.action) == "reject",
                col(FilterResult.rule_id) == POST_GRADUATION_ID,
            ),
        )
    ).all()
    for hit in hits:
        hit.overridden = True
        session.add(hit)
    posting.filter_state = "overridden"
    session.add(posting)
    session.commit()
    session.refresh(posting)
    return detail(session, posting)


@router.delete("/{posting_id}/override", response_model=schemas.PostingDetail)
def undo_override(posting_id: int, session: SessionDep) -> schemas.PostingDetail:
    """Undo a restore: reject hits count again and the posting goes back to "rejected"."""
    posting = get_or_404(session, Posting, posting_id, "Posting")
    hits = session.exec(select(FilterResult).where(FilterResult.posting_id == posting_id)).all()
    for hit in hits:
        if hit.overridden:
            hit.overridden = False
            session.add(hit)
    if any(h.action == "reject" for h in hits):
        posting.filter_state = "rejected"
    elif posting.filter_state == "overridden":
        posting.filter_state = "flagged" if any(h.action == "flag" for h in hits) else "pass"
    session.add(posting)
    session.commit()
    session.refresh(posting)
    return detail(session, posting)


@router.post("/{posting_id}/mark-sent", response_model=schemas.ApplicationOut)
def mark_posting_sent(
    posting_id: int, body: schemas.MarkSent, session: SessionDep
) -> schemas.ApplicationOut:
    """Record that the user applied (manually). Never submits anything."""
    posting = get_or_404(session, Posting, posting_id, "Posting")
    app = session.exec(select(Application).where(Application.posting_id == posting_id)).first()
    if app is None:  # idempotent: a second click returns the existing application
        app = Application(
            posting_id=posting_id,
            company=posting.company_name,
            role=posting.title,
            city=posting.location,
            date_sent=body.date_sent or local_today(),
            channel=body.channel,
            cv_version=body.cv_version,
            stage="Inviata",
        )
        session.add(app)
    posting.user_status = "sent"
    session.add(posting)
    session.commit()
    session.refresh(app)
    return application_out(app)


@router.get("/{posting_id}/pack", response_model=schemas.PackOut)
def get_pack(posting_id: int, session: SessionDep) -> schemas.PackOut:
    get_or_404(session, Posting, posting_id, "Posting")
    pack = session.exec(
        select(ApplicationPack).where(ApplicationPack.posting_id == posting_id)
    ).first()
    if pack is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No pack for posting {posting_id}")
    return pack_out(session, pack)


@router.post("/{posting_id}/pack", response_model=schemas.PackOut)
def generate_pack(posting_id: int, session: SessionDep) -> schemas.PackOut:
    """Generate (or regenerate) the application pack now with the LLM. Nothing is sent."""
    get_or_404(session, Posting, posting_id, "Posting")
    build_packs = optional_attr("radar.llm.packs", "build_packs")
    try:
        stats: Any = build_packs(session, run_id=None, limit=1, posting_id=posting_id)
    except Exception as exc:
        session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Pack generation failed: {exc}") from exc
    session.commit()
    pack = session.exec(
        select(ApplicationPack).where(ApplicationPack.posting_id == posting_id)
    ).first()
    if pack is None:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Pack generation produced no pack: {stats}"
        )
    return pack_out(session, pack)
