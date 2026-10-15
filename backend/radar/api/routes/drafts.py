"""Outreach drafts: review, edit, approve. The API never sends anything.

``sent`` is only set by ``radar send`` (local, one confirmation per message) or by
``POST /api/drafts/{id}/mark-sent`` when the user sent it by hand (copy button flow).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import Session, col, select

from radar.api import schemas
from radar.api.deps import SessionDep
from radar.api.routes.common import draft_out, drafts_out, get_or_404, optional_attr, style_check
from radar.models import ApplicationPack, Contact, Draft, Posting
from radar.timeutil import utcnow

router = APIRouter(prefix="/drafts", tags=["drafts"])


def _language(session: Session, draft: Draft) -> str:
    """Language of the related pack, English by default."""
    if draft.pack_id is not None:
        pack = session.get(ApplicationPack, draft.pack_id)
        if pack is not None and pack.language:
            return pack.language
    return "en"


@router.get("", response_model=list[schemas.DraftOut])
def list_drafts(
    session: SessionDep,
    status_: Annotated[str, Query(alias="status")] = "draft",
    channel: str | None = None,
) -> list[schemas.DraftOut]:
    stmt = select(Draft)
    if status_ != "all":
        stmt = stmt.where(col(Draft.status).in_([s.strip() for s in status_.split(",")]))
    if channel:
        stmt = stmt.where(Draft.channel == channel)
    stmt = stmt.order_by(col(Draft.updated_at).desc(), col(Draft.id).desc())
    return drafts_out(session, session.exec(stmt).all())


@router.post("", response_model=schemas.DraftOut)
def create_draft(body: schemas.DraftCreate, session: SessionDep) -> schemas.DraftOut:
    get_or_404(session, Posting, body.posting_id, "Posting")
    contact = get_or_404(session, Contact, body.contact_id, "Contact") if body.contact_id else None
    channel = body.channel or ("email" if body.kind == "email" else "linkedin")
    draft = Draft(
        posting_id=body.posting_id,
        contact_id=body.contact_id,
        kind=body.kind,
        channel=channel,
        recipient=body.recipient or (contact.profile_url if contact else None),
        text=body.text,
        status="draft",
        style_issues=style_check(body.text, body.kind) or [],
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft_out(session, draft)


@router.post("/for-contact", response_model=schemas.DraftOut)
def draft_for_contact(body: schemas.DraftForContact, session: SessionDep) -> schemas.DraftOut:
    """Ask the LLM to write a draft for one contact. Creates a ``draft``; sends nothing."""
    get_or_404(session, Posting, body.posting_id, "Posting")
    get_or_404(session, Contact, body.contact_id, "Contact")
    fn = optional_attr("radar.llm.packs", "draft_for_contact")
    try:
        draft: Draft = fn(session, body.posting_id, body.contact_id, body.kind)
    except Exception as exc:
        session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Draft generation failed: {exc}") from exc
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft_out(session, draft)


@router.patch("/{draft_id}", response_model=schemas.DraftOut)
def patch_draft(draft_id: int, body: schemas.DraftPatch, session: SessionDep) -> schemas.DraftOut:
    draft = get_or_404(session, Draft, draft_id, "Draft")
    if draft.status == "sent":
        raise HTTPException(status.HTTP_409_CONFLICT, "Draft already sent; it cannot be edited")
    if body.text is not None and body.text != draft.text:
        draft.text = body.text
        issues = style_check(body.text, draft.kind, _language(session, draft))
        if issues is not None:
            draft.style_issues = issues
    if "recipient" in body.model_fields_set:
        draft.recipient = body.recipient or None
    if body.status is not None and body.status != draft.status:
        draft.status = body.status
        draft.approved_at = utcnow() if body.status == "approved" else None
    draft.updated_at = utcnow()
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft_out(session, draft)


@router.post("/{draft_id}/mark-sent", response_model=schemas.DraftOut)
def mark_draft_sent(draft_id: int, session: SessionDep) -> schemas.DraftOut:
    """Record that the user sent this message by hand. The API itself sends nothing."""
    draft = get_or_404(session, Draft, draft_id, "Draft")
    if draft.status != "sent":
        now = utcnow()
        draft.status = "sent"
        draft.sent_at = now
        draft.updated_at = now
        draft.send_result = "manual"
        session.add(draft)
        session.commit()
        session.refresh(draft)
    return draft_out(session, draft)
