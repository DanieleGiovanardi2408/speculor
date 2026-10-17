"""Local token for the "Salva in Radar" browser extension (``POST /api/ingest/jobteaser``).

``radar extension token`` makes a new random token, prints it once (the user pastes it into the
extension options) and stores only its SHA-256 in the ``settings`` table, so a copy of the
database never carries a usable token. Generating a new one replaces the old one. Requests are
checked with a constant-time comparison.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlmodel import Session

from radar.db import get_setting, set_setting
from radar.timeutil import utcnow

SETTING_KEY = "extension:token"
HEADER = "X-Radar-Token"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(session: Session) -> str:
    """Create and store (hashed) a new token; returns the plain token (show it once)."""
    token = secrets.token_urlsafe(32)
    set_setting(
        session, SETTING_KEY, {"sha256": _digest(token), "created_at": utcnow().isoformat()}
    )
    return token


def token_configured(session: Session) -> bool:
    stored = get_setting(SETTING_KEY, None, session=session)
    return isinstance(stored, dict) and isinstance(stored.get("sha256"), str)


def check_token(session: Session, provided: str | None) -> bool:
    """True if ``provided`` matches the stored token (constant-time)."""
    stored = get_setting(SETTING_KEY, None, session=session)
    expected = stored.get("sha256") if isinstance(stored, dict) else None
    if not isinstance(expected, str) or not provided:
        return False
    return hmac.compare_digest(_digest(provided), expected)
