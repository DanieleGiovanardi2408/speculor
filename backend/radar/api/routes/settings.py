"""User settings stored in the settings table (weekly goal, enabled sources)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlmodel import Session

from radar.api import schemas
from radar.api.deps import SessionDep
from radar.db import get_setting, set_setting

router = APIRouter(prefix="/settings", tags=["settings"])

DEFAULT_WEEKLY_GOAL = 10
DEFAULT_SOURCES = (
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workday",
    "linkedin",
    "email",
)


def default_sources() -> tuple[str, ...]:
    """DEFAULT_SOURCES, plus "jobteaser" once its browser profile exists on this machine and
    "linkedin_jobs" when its searches file and the linkedin-cli binary exist."""
    from radar.config import get_settings
    from radar.connectors.jobteaser import profile_ready
    from radar.connectors.linkedin_jobs import available

    settings = get_settings()
    extra = (("jobteaser",) if profile_ready(settings) else ()) + (
        ("linkedin_jobs",) if available(settings) else ()
    )
    return (*DEFAULT_SOURCES, *extra)


def weekly_goal(session: Session) -> int:
    value: Any = get_setting("weekly_goal", DEFAULT_WEEKLY_GOAL, session=session)
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_WEEKLY_GOAL


def sources_enabled(session: Session) -> dict[str, bool]:
    out = dict.fromkeys(default_sources(), True)
    stored: Any = get_setting("sources_enabled", None, session=session)
    if isinstance(stored, dict):
        out.update({str(k): bool(v) for k, v in stored.items()})
    return out


def read_settings(session: Session) -> schemas.SettingsOut:
    return schemas.SettingsOut(
        weekly_goal=weekly_goal(session), sources_enabled=sources_enabled(session)
    )


@router.get("", response_model=schemas.SettingsOut)
def get_settings_route(session: SessionDep) -> schemas.SettingsOut:
    return read_settings(session)


@router.put("", response_model=schemas.SettingsOut)
def put_settings(body: schemas.SettingsIn, session: SessionDep) -> schemas.SettingsOut:
    if body.weekly_goal is not None:
        set_setting(session, "weekly_goal", body.weekly_goal)
    if body.sources_enabled is not None:
        merged = sources_enabled(session)
        merged.update(body.sources_enabled)
        set_setting(session, "sources_enabled", merged)
    session.commit()
    return read_settings(session)
