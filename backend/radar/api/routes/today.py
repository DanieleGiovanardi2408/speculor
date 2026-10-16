"""Today dashboard: weekly progress, diff since yesterday, deadlines, follow-ups."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter
from sqlmodel import Session, col, func, select

from radar.api import schemas
from radar.api.deps import SessionDep
from radar.api.routes.common import (
    FOLLOWUP_DAYS,
    application_out,
    local_today,
    sort_summaries,
    summaries,
)
from radar.api.routes.runs import run_out
from radar.api.routes.settings import weekly_goal
from radar.models import Application, ChangeEvent, Draft, Posting, Run
from radar.timeutil import utcnow

router = APIRouter(prefix="/today", tags=["today"])

UPCOMING_DAYS = 14


def _event_posting_ids(session: Session, latest: Run | None, kind: str) -> list[int]:
    """'Since yesterday' = events of the latest finished run, else the last 24 hours."""
    stmt = select(ChangeEvent.posting_id).where(ChangeEvent.kind == kind)
    if latest is not None:
        stmt = stmt.where(ChangeEvent.run_id == latest.id)
    else:
        stmt = stmt.where(col(ChangeEvent.at) >= utcnow() - timedelta(hours=24))
    return sorted(set(session.exec(stmt).all()))


def _relevant(session: Session, ids: list[int]) -> list[Posting]:
    if not ids:
        return []
    return list(
        session.exec(
            select(Posting).where(
                col(Posting.id).in_(ids),
                col(Posting.duplicate_of).is_(None),
                Posting.filter_state != "rejected",
            )
        ).all()
    )


@router.get("", response_model=schemas.Today)
def today_view(session: SessionDep) -> schemas.Today:
    today = local_today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    last_run = session.exec(
        select(Run).order_by(col(Run.started_at).desc(), col(Run.id).desc())
    ).first()
    finished_run = session.exec(
        select(Run)
        .where(Run.status != "running")
        .order_by(col(Run.started_at).desc(), col(Run.id).desc())
    ).first()

    # New postings and upcoming deadlines are the internship track; post-graduation roles
    # are only counted (they have their own view).
    new_all = summaries(
        session, _relevant(session, _event_posting_ids(session, finished_run, "new"))
    )
    new_internships = [s for s in new_all if s.track == "internship"]
    closed_postings = _relevant(session, _event_posting_ids(session, finished_run, "closed"))
    upcoming_rows = session.exec(
        select(Posting).where(
            Posting.status == "open",
            Posting.user_status == "todo",
            Posting.filter_state != "rejected",
            col(Posting.duplicate_of).is_(None),
            col(Posting.deadline) >= today,
            col(Posting.deadline) <= today + timedelta(days=UPCOMING_DAYS),
        )
    ).all()
    upcoming = [s for s in summaries(session, upcoming_rows) if s.track == "internship"]

    apps_week = session.exec(
        select(func.count())
        .select_from(Application)
        .where(col(Application.date_sent) >= monday, col(Application.date_sent) <= sunday)
    ).one()
    due = session.exec(
        select(Application)
        .where(
            Application.stage == "Inviata",
            col(Application.responded).is_(False),
            col(Application.date_sent) <= today - timedelta(days=FOLLOWUP_DAYS),
        )
        .order_by(col(Application.date_sent))
    ).all()
    pending = session.exec(
        select(func.count()).select_from(Draft).where(Draft.status == "draft")
    ).one()

    return schemas.Today(
        weekly_goal=weekly_goal(session),
        applications_this_week=apps_week,
        new_since_yesterday=sort_summaries(new_internships),
        new_post_graduation=len(new_all) - len(new_internships),
        closed_since_yesterday=sort_summaries(summaries(session, closed_postings)),
        upcoming_deadlines=sort_summaries(upcoming, "deadline"),
        followups_due=[application_out(a, today) for a in due],
        pending_approvals=pending,
        last_run=run_out(last_run) if last_run else None,
    )
