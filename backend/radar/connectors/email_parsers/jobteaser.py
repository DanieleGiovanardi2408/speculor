"""JobTeaser (e.g. Luiss career service) offer digests -> RawPosting.

Offer links look like ``https://luiss.jobteaser.com/it/job-offers/<id>-<slug>?utm_...`` where
``<id>`` is a UUID (current) or a number (older offers). They are canonicalized to
``https://<host>/<lang>/job-offers/<id>-<slug>`` (tracking params dropped). Links wrapped by a
click-tracking redirect are unwrapped only when the offer URL is inside the link (query
parameter or encoded path segment); tracking links are never requested. JobTeaser is
login-walled: offers are never fetched; everything comes from the email.

Sender: any address at ``jobteaser.com`` or one of its subdomains (the alert/digest sender is
not documented publicly; e.g. ``notifications@...jobteaser.com``).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from radar.connectors.email_parsers.common import (
    container_lines,
    find_cards,
    lines_after,
    parse_html,
    pick_title,
    split_dot,
    text_blocks,
    unwrap_url,
)
from radar.schemas import RawPosting

VIA = "jobteaser_alert"
_OFFER_RE = re.compile(
    r"^https?://(?P<host>(?:[a-z0-9-]+\.)*jobteaser\.com)"
    r"(?P<prefix>/(?:[a-z]{2}(?:-[a-z]{2})?/)?job-offers/)"
    r"(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)"
    r"(?P<slug>-[^/?#]*)?",
    re.I,
)
# Contract types seen on JobTeaser cards (Italian, English, French).
_CONTRACT = re.compile(
    r"^(stage|stage curriculare|stage extracurriculare|tirocinio|tirocinio curriculare|"
    r"tirocinio extracurriculare|internship|intern|graduate program(me)?|apprendistato|"
    r"alternanza|alternance|apprenticeship|cdi|cdd|v\.?i\.?e\.?|full[- ]time|part[- ]time|"
    r"tempo (in)?determinato|contratto a tempo (in)?determinato|lavoro|job|freelance|"
    r"permanent|temporary|fixed[- ]term|thesis|tesi|summer job|volontariato)$",
    re.I,
)
_META_PREFIX = re.compile(
    r"^(inizio|start|début|durata|duration|durée|scadenza|deadline|pubblicata|published|"
    r"publiée)\b",
    re.I,
)


def is_sender(address: str, display_name: str) -> bool:
    host = address.lower().rsplit("@", 1)[-1]
    return "@" in address and (host == "jobteaser.com" or host.endswith(".jobteaser.com"))


def _is_offer(url: str) -> bool:
    return bool(_OFFER_RE.match(url))


def offer_key(href: str) -> tuple[str, str] | None:
    url = unwrap_url(href, _is_offer)
    m = _OFFER_RE.match(url)
    if not m:
        return None
    offer_id = m.group("id").lower()
    host = m.group("host").lower()
    # Always the Italian interface (/it/): links saved from a page shown in another locale
    # (e.g. /ca/) would otherwise open, and be read, in that language.
    path = f"/{OFFER_LOCALE}/job-offers/{m.group('id')}{m.group('slug') or ''}"
    return offer_id, f"https://{host}{path}"


OFFER_LOCALE = "it"
_MULTI_LOCATION = re.compile(
    r"^(diverses localitzacions|varias ubicaciones|diverse (sedi|località)|più sedi|"
    r"plusieurs (lieux|localisations|sites)|several locations|multiple locations|"
    r"mehrere standorte|meerdere locaties)\b",
    re.I,
)


def normalize_location(location: str | None) -> str | None:
    """JobTeaser's "several locations proposed" placeholder, in any interface language, becomes
    "Più sedi" (it is not a place, and it is not text in a foreign language)."""
    if location and _MULTI_LOCATION.match(location.strip()):
        return "Più sedi"
    return location


def _fields(rest: list[str]) -> tuple[str, str | None, str | None, list[str]]:
    """(company, location, contract, meta lines) from the card lines after the title."""
    company = ""
    location: str | None = None
    contract: str | None = None
    meta: list[str] = []
    for i, line in enumerate(rest):
        if _META_PREFIX.match(line):
            meta.append(line)
            continue
        parts = split_dot(line)
        if i == 0 and not any(_CONTRACT.match(p) for p in parts):
            company = parts[0] if parts else line
            for p in parts[1:]:
                if location is None:
                    location = p
            continue
        for p in parts:
            if contract is None and _CONTRACT.match(p):
                contract = p
            elif location is None:
                location = p
    return company, location, contract, meta


def _posting(
    offer_id: str, url: str, title: str, rest: list[str], extra: dict[str, Any]
) -> RawPosting | None:
    if not title:
        return None
    company, location, contract, meta = _fields(rest)
    return RawPosting(
        source="email",
        external_id=f"jobteaser:{offer_id}",
        url=url,
        title=title,
        company=company,
        location=normalize_location(location),
        employment_type=contract,
        raw_text="\n".join([title, *rest[:6]]),
        extra={
            "via": VIA,
            "jobteaser_host": urlsplit(url).netloc,
            **({"start_info": meta[0]} if meta else {}),
            **extra,
        },
    )


def parse(body_html: str | None, body_text: str | None, extra: dict[str, Any]) -> list[RawPosting]:
    out: dict[str, RawPosting] = {}
    if body_html:
        tree = parse_html(body_html)
        for card in find_cards(tree, offer_key):
            lines = container_lines(card.container)
            title = pick_title(card, lines)
            posting = _posting(card.key, card.url, title, lines_after(title, lines), extra)
            if posting is not None:
                out.setdefault(card.key, posting)
    if not out and body_text:
        for block in text_blocks(body_text, offer_key):
            if block.key in out or not block.lines:
                continue
            posting = _posting(block.key, block.url, block.lines[0], block.lines[1:], extra)
            if posting is not None:
                out[block.key] = posting
    return list(out.values())
