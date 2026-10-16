"""Alert-email parsers: classify a message by sender and turn it into RawPosting objects.

Kinds: ``linkedin`` (LinkedIn job alerts), ``jobteaser`` (JobTeaser offer digests) and
``generic`` (configured extra senders; only links to supported ATS postings are kept).
Any other message is ignored.
"""

from __future__ import annotations

import contextlib
import email.utils
from collections.abc import Iterable
from email.message import Message
from typing import Any, Literal

from radar.connectors.email_parsers import generic, jobteaser, linkedin
from radar.connectors.email_parsers.common import message_bodies
from radar.schemas import RawPosting

AlertKind = Literal["linkedin", "jobteaser", "generic"]

#: IMAP ``FROM`` search terms for the built-in alert kinds (substring match on the From header).
BUILTIN_SEARCH_SENDERS: tuple[str, ...] = (*linkedin.SENDERS, "jobteaser.com")


def sender_of(msg: Message) -> tuple[str, str]:
    """(lowercased address, display name) of the From header."""
    name, addr = email.utils.parseaddr(str(msg.get("From", "")))
    return addr.strip().lower(), name.strip()


def _matches_extra(address: str, extra_senders: Iterable[str]) -> bool:
    for raw in extra_senders:
        s = raw.strip().lower()
        if not s:
            continue
        if "@" in s and not s.startswith("@"):
            if address == s:
                return True
        else:
            domain = s.lstrip("@")
            host = address.rsplit("@", 1)[-1]
            if host == domain or host.endswith("." + domain):
                return True
    return False


def classify(msg: Message, extra_senders: Iterable[str] = ()) -> AlertKind | None:
    address, name = sender_of(msg)
    if not address:
        return None
    if linkedin.is_sender(address, name):
        return "linkedin"
    if jobteaser.is_sender(address, name):
        return "jobteaser"
    if _matches_extra(address, extra_senders):
        return "generic"
    return None


def parse_message(msg: Message, extra_senders: Iterable[str] = ()) -> list[RawPosting]:
    """Postings found in one alert email ([] for non-alert emails)."""
    kind = classify(msg, extra_senders)
    if kind is None:
        return []
    body_html, body_text = message_bodies(msg)
    extra: dict[str, Any] = {}
    date_header = msg.get("Date")
    if date_header:
        with contextlib.suppress(TypeError, ValueError):
            extra["alert_date"] = email.utils.parsedate_to_datetime(str(date_header)).isoformat()
    if kind == "linkedin":
        return linkedin.parse(body_html, body_text, extra)
    if kind == "jobteaser":
        return jobteaser.parse(body_html, body_text, extra)
    return generic.parse(body_html, body_text, extra)


__all__ = ["BUILTIN_SEARCH_SENDERS", "AlertKind", "classify", "parse_message", "sender_of"]
