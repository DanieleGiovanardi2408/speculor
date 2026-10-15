"""Applications tracker and stats. Records what the user did; never submits anything."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence

from fastapi import APIRouter
from sqlmodel import col, select

from radar.api import schemas
from radar.api.deps import SessionDep
from radar.api.routes.common import application_out, get_or_404, local_today
from radar.models import APPLICATION_STAGES, Application, Posting
from radar.timeutil import utcnow

router = APIRouter(prefix="/applications", tags=["applications"])

INTERVIEW_STAGES = {"Colloquio 1", "Colloquio 2+", "Offerta"}
NO_RESPONSE_STAGES = {"Inviata", "Nessuna risposta", "Ritirata"}


def is_interview(app: Application) -> bool:
    return app.stage in INTERVIEW_STAGES


def is_response(app: Application) -> bool:
    """Responded flag, or any stage that implies the company answered (incl. Rifiutata)."""
    return app.responded or app.stage not in NO_RESPONSE_STAGES


def _rows(apps: Sequence[Application], key: Callable[[Application], str]) -> list[schemas.StatsRow]:
    groups: dict[str, list[Application]] = {}
    for a in apps:
        groups.setdefault(key(a) or "(none)", []).append(a)
    rows = []
    for k, items in groups.items():
        sent = len(items)
        interviews = sum(1 for a in items if is_interview(a))
        rows.append(
            schemas.StatsRow(
                key=k,
                sent=sent,
                responses=sum(1 for a in items if is_response(a)),
                interviews=interviews,
                interview_rate=round(interviews / sent, 4) if sent else 0.0,
            )
        )
    return sorted(rows, key=lambda r: (-r.sent, r.key))


def compute_stats(apps: Sequence[Application]) -> schemas.AppStats:
    stages = Counter(a.stage for a in apps)
    by_stage = {s: stages.get(s, 0) for s in APPLICATION_STAGES}
    for s, n in stages.items():  # keep unexpected legacy values visible
        by_stage.setdefault(s, n)
    return schemas.AppStats(
        by_channel=_rows(apps, lambda a: a.channel),
        by_cv=_rows(apps, lambda a: a.cv_version),
        by_stage=by_stage,
    )


@router.get("", response_model=list[schemas.ApplicationOut])
def list_applications(session: SessionDep) -> list[schemas.ApplicationOut]:
    apps = session.exec(
        select(Application).order_by(col(Application.date_sent).desc(), col(Application.id).desc())
    ).all()
    today = local_today()
    return [application_out(a, today) for a in apps]


@router.get("/stats", response_model=schemas.AppStats)
def application_stats(session: SessionDep) -> schemas.AppStats:
    return compute_stats(session.exec(select(Application)).all())


@router.post("", response_model=schemas.ApplicationOut)
def create_application(
    body: schemas.ApplicationCreate, session: SessionDep
) -> schemas.ApplicationOut:
    posting = get_or_404(session, Posting, body.posting_id, "Posting") if body.posting_id else None
    app = Application(**body.model_dump())
    session.add(app)
    if posting is not None:
        posting.user_status = "sent"
        session.add(posting)
    session.commit()
    session.refresh(app)
    return application_out(app)


@router.patch("/{app_id}", response_model=schemas.ApplicationOut)
def patch_application(
    app_id: int, body: schemas.ApplicationPatch, session: SessionDep
) -> schemas.ApplicationOut:
    app = get_or_404(session, Application, app_id, "Application")
    data = body.model_dump(exclude_unset=True)
    required = {"company", "role", "date_sent", "channel", "cv_version", "stage", "responded"}
    for field, value in data.items():
        if value is None and field in required:
            continue  # cannot null a required column
        if field == "posting_id" and value is not None:
            get_or_404(session, Posting, value, "Posting")
        setattr(app, field, value)
    app.updated_at = utcnow()
    session.add(app)
    session.commit()
    session.refresh(app)
    return application_out(app)
