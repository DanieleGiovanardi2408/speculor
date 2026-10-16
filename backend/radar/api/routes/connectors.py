"""Connector pause state (LinkedIn checkpoint, JobTeaser session expired, ...)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from radar.api.deps import SessionDep
from radar.connectors.status import all_status, resume

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.get("/status")
def get_status(session: SessionDep) -> dict[str, dict[str, Any]]:
    return all_status(session)


@router.post("/{source}/resume")
def resume_source(source: str, session: SessionDep) -> dict[str, dict[str, Any]]:
    if not resume(session, source):
        raise HTTPException(404, "Fonte non in pausa")
    session.commit()
    return all_status(session)
