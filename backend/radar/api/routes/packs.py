"""Application packs: manual edits (cover letter, form answers, CV version, apply link) and
the fact check (re-verify the current texts against the fact bank)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from radar.api import schemas
from radar.api.deps import SessionDep
from radar.api.routes.common import get_or_404, pack_out, style_check
from radar.models import ApplicationPack
from radar.timeutil import utcnow

router = APIRouter(prefix="/packs", tags=["packs"])


@router.patch("/{pack_id}", response_model=schemas.PackOut)
def patch_pack(pack_id: int, body: schemas.PackPatch, session: SessionDep) -> schemas.PackOut:
    pack = get_or_404(session, ApplicationPack, pack_id, "Pack")
    if body.cover_letter is not None:
        pack.cover_letter = body.cover_letter
        issues = style_check(body.cover_letter, "cover_letter", pack.language)
        if issues is not None:
            pack.style_issues = issues
    if body.form_answers is not None:
        pack.form_answers = [a.model_dump() for a in body.form_answers]
    if body.cv_version is not None:
        pack.cv_version = body.cv_version
    if body.apply_link is not None:
        pack.apply_link = body.apply_link
    pack.updated_at = utcnow()
    session.add(pack)
    session.commit()
    session.refresh(pack)
    return pack_out(session, pack)


@router.post("/{pack_id}/verify", response_model=schemas.PackOut)
def verify_pack(
    pack_id: int,
    session: SessionDep,
    llm: Annotated[bool, Query(description="Also run the LLM verifier")] = True,
) -> schemas.PackOut:
    """Re-check the current texts of the pack and its outreach drafts against the fact bank.
    Texts are not changed. Without a key (or with ``llm=false``) only the pre-check runs."""
    pack = get_or_404(session, ApplicationPack, pack_id, "Pack")
    from radar.facts import FactsError
    from radar.llm.verify import verify_pack as run_verify

    try:
        run_verify(session, pack, use_llm=llm)
    except FactsError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Fact bank: {exc}") from exc
    session.commit()
    session.refresh(pack)
    return pack_out(session, pack)
