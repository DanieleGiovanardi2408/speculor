"""Small helpers shared by the XML/RSS feed connectors (Personio, Teamtailor)."""

from __future__ import annotations

import email.utils
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from radar.connectors.base import SourceError

_DTD_RE = re.compile(r"<!(?:DOCTYPE|ENTITY)", re.I)


def parse_xml(text: str, source: str) -> ET.Element:
    """Parse a feed body. Documents with a DTD or entity declarations are refused (public job
    feeds never need them, and refusing them rules out entity-expansion tricks)."""
    if _DTD_RE.search(text[:4096]) or "<!ENTITY" in text:
        raise SourceError(f"{source}: feed declares a DTD, refused")
    try:
        return ET.fromstring(text.strip().encode("utf-8"))
    except ET.ParseError as exc:
        raise SourceError(f"{source}: feed is not valid XML") from exc


def child_text(node: ET.Element, tag: str) -> str:
    """Stripped text of the first ``tag`` child ('' if absent). ``tag`` may be namespaced."""
    found = node.find(tag)
    return (found.text or "").strip() if found is not None else ""


def parse_rfc822(value: str | None) -> datetime | None:
    """RSS ``pubDate`` -> aware datetime (UTC), None if unparseable."""
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
