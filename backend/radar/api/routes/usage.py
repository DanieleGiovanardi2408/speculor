"""LLM usage aggregates (read-only): sums of ``llm_usage`` rows by day, model and purpose."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, timedelta, tzinfo
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query
from sqlmodel import col, select

from radar.api import schemas
from radar.api.deps import SessionDep
from radar.api.routes.common import local_today
from radar.models import LLMUsage
from radar.timeutil import utcnow

router = APIRouter(prefix="/usage", tags=["usage"])


def _tz() -> tzinfo:
    try:
        return ZoneInfo("Europe/Amsterdam")
    except ZoneInfoNotFoundError:
        return UTC


def _by_model(rows: list[schemas.UsageRow]) -> list[schemas.UsageTotal]:
    acc: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        a = acc[(r.provider, r.model)]
        a[0] += r.requests
        a[1] += r.input_tokens
        a[2] += r.output_tokens
    return [
        schemas.UsageTotal(
            provider=p, model=m, requests=a[0], input_tokens=a[1], output_tokens=a[2]
        )
        for (p, m), a in sorted(acc.items(), key=lambda kv: (-kv[1][1] - kv[1][2], kv[0]))
    ]


@router.get("", response_model=schemas.UsageOut)
def usage(session: SessionDep, days: Annotated[int, Query(ge=1, le=90)] = 7) -> schemas.UsageOut:
    """Last ``days`` local days (today included). Counts only: no prompts, no texts."""
    tz = _tz()
    today = local_today()
    since: date = today - timedelta(days=days - 1)
    # Over-fetch by a day in UTC, then bucket by local day.
    cutoff = utcnow() - timedelta(days=days + 1)
    rows = session.exec(select(LLMUsage).where(col(LLMUsage.at) >= cutoff)).all()
    acc: dict[tuple[date, str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for u in rows:
        at = u.at if u.at.tzinfo else u.at.replace(tzinfo=UTC)
        day = at.astimezone(tz).date()
        if day < since or day > today:
            continue
        a = acc[(day, u.provider, u.model, u.purpose)]
        a[0] += u.requests
        a[1] += u.input_tokens
        a[2] += u.output_tokens
    out_rows = [
        schemas.UsageRow(
            day=d,
            provider=p,
            model=m,
            purpose=pu,
            requests=a[0],
            input_tokens=a[1],
            output_tokens=a[2],
        )
        for (d, p, m, pu), a in sorted(acc.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[0][3]))
    ]
    return schemas.UsageOut(
        since=since,
        until=today,
        rows=out_rows,
        today=_by_model([r for r in out_rows if r.day == today]),
        period=_by_model(out_rows),
    )
