"""Skip reasons, stored as a ``[Saltato: reason]`` prefix of ``Posting.note`` (no schema change)."""

from __future__ import annotations

import re

_PREFIX = re.compile(r"^\[Saltato: ([^\]\n]{1,200})\]\s?")
MAX_REASON = 200


def split_skip(note: str | None) -> tuple[str | None, str | None]:
    """``"[Saltato: Sede] ciao"`` -> ``("Sede", "ciao")``; no prefix -> ``(None, note)``."""
    if not note:
        return None, None
    m = _PREFIX.match(note)
    if not m:
        return None, note
    body = note[m.end() :]
    return m.group(1).strip() or None, body or None


def compose_note(reason: str | None, body: str | None) -> str | None:
    """Inverse of :func:`split_skip`. Brackets and newlines in the reason are flattened."""
    body = body or None
    if not reason:
        return body
    clean = re.sub(r"[\[\]\s]+", " ", reason).strip()[:MAX_REASON].strip()
    if not clean:
        return body
    return f"[Saltato: {clean}]" + (f" {body}" if body else "")
