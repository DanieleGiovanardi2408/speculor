"""Companies watchlist."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter
from sqlmodel import Session, col, func, select

from radar.api import schemas
from radar.api.deps import SessionDep
from radar.api.routes.common import get_or_404
from radar.models import Company, Posting

router = APIRouter(prefix="/companies", tags=["companies"])


def _open_counts(session: Session) -> tuple[Counter[int], Counter[str]]:
    """Open, non-rejected, non-duplicate postings per company id (and per name if unlinked)."""
    base = (
        Posting.status == "open",
        Posting.filter_state != "rejected",
        col(Posting.duplicate_of).is_(None),
    )
    by_id: Counter[int] = Counter()
    for cid, n in session.exec(
        select(Posting.company_id, func.count())
        .where(*base, col(Posting.company_id).is_not(None))
        .group_by(col(Posting.company_id))
    ).all():
        if cid is not None:
            by_id[cid] = n
    by_name: Counter[str] = Counter()
    for name, n in session.exec(
        select(Posting.company_name, func.count())
        .where(*base, col(Posting.company_id).is_(None))
        .group_by(col(Posting.company_name))
    ).all():
        by_name[name.strip().lower()] = n
    return by_id, by_name


def _out(c: Company, by_id: Counter[int], by_name: Counter[str]) -> schemas.CompanyOut:
    assert c.id is not None
    return schemas.CompanyOut(
        id=c.id,
        name=c.name,
        sector=c.sector,
        city=c.city,
        careers_url=c.careers_url,
        program=c.program,
        why=c.why,
        notes=c.notes,
        priority=c.priority,
        ats=c.ats,
        ats_slug=c.ats_slug,
        active=c.active,
        open_postings=by_id.get(c.id, 0) + by_name.get(c.name.strip().lower(), 0),
    )


@router.get("", response_model=list[schemas.CompanyOut])
def list_companies(session: SessionDep) -> list[schemas.CompanyOut]:
    by_id, by_name = _open_counts(session)
    companies = session.exec(select(Company).order_by(func.lower(col(Company.name)))).all()
    return [_out(c, by_id, by_name) for c in companies]


@router.patch("/{company_id}", response_model=schemas.CompanyOut)
def patch_company(
    company_id: int, body: schemas.CompanyPatch, session: SessionDep
) -> schemas.CompanyOut:
    company = get_or_404(session, Company, company_id, "Company")
    fields = body.model_fields_set
    if "notes" in fields:
        company.notes = body.notes or None
    if "priority" in fields:
        company.priority = body.priority or None
    if "active" in fields and body.active is not None:
        company.active = body.active
    session.add(company)
    session.commit()
    session.refresh(company)
    by_id, by_name = _open_counts(session)
    return _out(company, by_id, by_name)
