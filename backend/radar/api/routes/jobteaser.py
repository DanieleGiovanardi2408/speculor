"""``GET /api/jobteaser/pass``: the weekly JobTeaser pass card on the Oggi screen (last pass
counts, whether to remind, direct links from config/jobteaser_searches.yaml)."""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

from radar.api.deps import SessionDep
from radar.config import get_settings
from radar.extension_token import token_configured
from radar.pipeline.jobteaser_ingest import last_pass, pass_links, reminder

router = APIRouter(prefix="/jobteaser", tags=["jobteaser"])


class PassLink(BaseModel):
    label: str
    url: str


class LastPass(BaseModel):
    at: str
    received: int = 0
    new: int = 0
    updated: int = 0
    ignored: int = 0


class PassOut(BaseModel):
    last_pass: LastPass | None
    #: "monday" | "stale" | "never" | None (no card)
    remind: str | None
    links: list[PassLink]
    extension_configured: bool


def _searches_config() -> dict[str, Any]:
    """config/jobteaser_searches.yaml, else the example, else {} (never fails the route)."""
    config_dir = get_settings().config_dir
    for name in ("jobteaser_searches.yaml", "jobteaser_searches.example.yaml"):
        path = config_dir / name
        try:
            if path.exists():
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except (OSError, yaml.YAMLError):
            return {}
    return {}


@router.get("/pass", response_model=PassOut)
def get_pass(session: SessionDep) -> PassOut:
    last = last_pass(session)
    return PassOut(
        last_pass=LastPass(**last) if last else None,
        remind=reminder(last),
        links=[PassLink(**link) for link in pass_links(_searches_config())],
        extension_configured=token_configured(session),
    )
