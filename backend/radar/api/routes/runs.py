"""Pipeline runs (read-only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlmodel import col, select

from radar.api import schemas
from radar.api.deps import SessionDep
from radar.models import Run

router = APIRouter(prefix="/runs", tags=["runs"])


def run_out(run: Run) -> schemas.RunSummary:
    assert run.id is not None
    return schemas.RunSummary(
        id=run.id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        stats=run.stats,
    )


@router.get("", response_model=list[schemas.RunSummary])
def list_runs(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 10
) -> list[schemas.RunSummary]:
    runs = session.exec(
        select(Run).order_by(col(Run.started_at).desc(), col(Run.id).desc()).limit(limit)
    ).all()
    return [run_out(r) for r in runs]
