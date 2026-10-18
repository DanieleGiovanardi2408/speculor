"""Per-source pause state for connectors that use the user's own sessions.

A connector raises :class:`SourcePaused` when it must stop and wait for the user
(LinkedIn checkpoint/captcha/429/999, JobTeaser session expired, IMAP auth failure).
The pipeline records the pause in the ``connector_status`` setting, skips that source
on later runs, and the Oggi screen shows ``action`` (Italian, what the user should do).
A pause is cleared only by the user (``radar connectors resume <source>`` or the UI),
never automatically: no retries against a site that asked us to stop.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from radar.connectors.base import SourceError
from radar.db import get_setting, set_setting
from radar.timeutil import utcnow

SETTING_KEY = "connector_status"


class SourcePaused(SourceError):
    """Stop this source now, do not retry, wait for the user."""

    def __init__(self, source: str, reason: str, action: str) -> None:
        super().__init__(f"{source} paused: {reason}", action=action)
        self.source = source
        self.reason = reason  # short, no personal data (e.g. "HTTP 999", "checkpoint")
        self.action = action  # Italian instruction shown on the Oggi screen


def all_status(session: Session) -> dict[str, dict[str, Any]]:
    stored = get_setting(SETTING_KEY, None, session=session)
    return dict(stored) if isinstance(stored, dict) else {}


def paused_sources(session: Session) -> set[str]:
    return {k for k, v in all_status(session).items() if isinstance(v, dict) and v.get("paused")}


def pause(session: Session, source: str, reason: str, action: str) -> None:
    status = all_status(session)
    status[source] = {
        "paused": True,
        "reason": reason,
        "action": action,
        "since": utcnow().isoformat(),
    }
    set_setting(session, SETTING_KEY, status)


def resume(session: Session, source: str) -> bool:
    status = all_status(session)
    if source not in status:
        return False
    status.pop(source)
    set_setting(session, SETTING_KEY, status)
    return True
