"""Contacts (people to reach out to for a posting). Entered by the user; nothing is sent."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlmodel import select

from radar.api import schemas
from radar.api.deps import SessionDep
from radar.api.routes.common import contact_out, get_or_404
from radar.models import Contact, Draft, Posting

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.post("", response_model=schemas.ContactOut)
def create_contact(body: schemas.ContactCreate, session: SessionDep) -> schemas.ContactOut:
    posting = get_or_404(session, Posting, body.posting_id, "Posting")
    contact = Contact(
        posting_id=body.posting_id,
        company_id=posting.company_id,
        name=body.name.strip(),
        role=body.role,
        profile_url=body.profile_url,
        why=body.why,
    )
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact_out(contact)


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(contact_id: int, session: SessionDep) -> Response:
    contact = get_or_404(session, Contact, contact_id, "Contact")
    for draft in session.exec(select(Draft).where(Draft.contact_id == contact_id)).all():
        draft.contact_id = None
        session.add(draft)
    session.delete(contact)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
