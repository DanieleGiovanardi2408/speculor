"""``POST /api/ingest/jobteaser``: offers saved with the "Salva in Radar" browser extension.

Not behind the Supabase ``require_auth`` (Radar runs locally and the extension has no Supabase
session). Instead every request needs:

* ``Origin: chrome-extension://<id>`` (Chrome sets it on requests from the extension's service
  worker; a web page cannot forge it). With ``RADAR_EXTENSION_ID`` set, only that id passes.
* ``X-Radar-Token`` equal to the local token from ``radar extension token`` (constant-time
  comparison against the stored hash).

Only counts are logged.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from radar.api.deps import SessionDep, _env
from radar.extension_token import check_token, token_configured
from radar.pipeline.jobteaser_ingest import MAX_OFFERS, IncomingOffer, ingest_offers

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

EXTENSION_SCHEME = "chrome-extension://"


class OfferIn(BaseModel):
    # generous input limits (abuse guard); stored values are cut shorter by the ingest
    title: str = Field(max_length=1000)
    company: str = Field(default="", max_length=1000)
    url: str = Field(max_length=2000)
    location: str | None = Field(default=None, max_length=1000)
    date: str | None = Field(default=None, max_length=200)


class IngestIn(BaseModel):
    offers: list[OfferIn] = Field(max_length=MAX_OFFERS)


class IngestOut(BaseModel):
    received: int
    new: int
    updated: int
    ignored: int


def require_extension(
    session: SessionDep,
    origin: Annotated[str | None, Header()] = None,
    x_radar_token: Annotated[str | None, Header()] = None,
) -> None:
    """Origin must be the extension, the token must match. 403 for the origin, 401 for the
    token (no detail about which part of the token was wrong)."""
    pinned = (_env("RADAR_EXTENSION_ID") or "").strip()
    allowed = origin is not None and origin.startswith(EXTENSION_SCHEME)
    if allowed and pinned:
        allowed = origin == f"{EXTENSION_SCHEME}{pinned}"
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origine non consentita")
    if not token_configured(session):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Nessun token: esegui `radar extension token`"
        )
    if not check_token(session, x_radar_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token non valido")


@router.post("/jobteaser", response_model=IngestOut, dependencies=[Depends(require_extension)])
def ingest_jobteaser(body: IngestIn, session: SessionDep) -> IngestOut:
    from radar.pipeline.filters import load_rules

    try:
        rules = load_rules()
    except Exception:  # missing/broken rules: the next run filters these postings
        rules = None
    counts = ingest_offers(
        session,
        [IncomingOffer(**o.model_dump()) for o in body.offers],
        rules=rules,
    )
    return IngestOut(**counts)
