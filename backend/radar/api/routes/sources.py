"""``GET /api/sources``: the Fonti screen (per-source state, last run, quotas). Read-only;
pausing and resuming stay on ``/api/connectors``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from radar.api.deps import SessionDep
from radar.sources import overview

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
def get_sources(session: SessionDep) -> dict[str, Any]:
    return overview(session)
