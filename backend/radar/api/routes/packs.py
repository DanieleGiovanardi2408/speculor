"""Application packs: manual edits (cover letter, form answers, CV version, apply link)."""

from __future__ import annotations

from fastapi import APIRouter

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
