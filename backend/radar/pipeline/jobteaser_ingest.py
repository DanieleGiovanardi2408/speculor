"""Offers saved with the "Salva in Radar" browser extension (``POST /api/ingest/jobteaser``).

The user opens a JobTeaser listing page in their own browser and clicks the button; the
extension sends only the offers already rendered on that page (title, company, offer URL,
location, date if shown). Radar never opens those listing pages itself (robots.txt disallows
them). Each offer is upserted as source ``jobteaser`` (uid ``jobteaser:<offer id>``, the same
row the connector's detail fetch later fills with the description) through the normal store, so
it de-duplicates with the alert-email copy of the same offer on the canonical offer URL. New
postings go through the filters at once; ranking, scoring and packs follow at the next run.

The setting ``jobteaser:last_pass`` keeps the time and counts of the last import (no titles,
no companies, no URLs), shown on the Oggi screen with the weekly reminder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Session, select

from radar.connectors.email_parsers.jobteaser import normalize_location, offer_key
from radar.db import get_setting, set_setting
from radar.models import Posting
from radar.pipeline.filters import Rules, apply_filters
from radar.pipeline.store import company_index, upsert_raw
from radar.schemas import RawPosting
from radar.timeutil import utcnow

log = logging.getLogger(__name__)

SOURCE = "jobteaser"
LAST_PASS_KEY = "jobteaser:last_pass"
MAX_OFFERS = 500
#: Stored lengths (longer values are cut, not rejected).
CAPS = {"title": 300, "company": 200, "location": 200, "date": 60}
STALE_DAYS = 7


@dataclass
class IncomingOffer:
    title: str
    company: str = ""
    url: str = ""
    location: str | None = None
    date: str | None = None


def _clean(value: str | None, cap: int) -> str:
    return " ".join((value or "").split())[:cap]


def _parse_shown_date(text: str | None) -> datetime | None:
    from radar.connectors.jobteaser import parse_human_date

    day = parse_human_date(text)
    return datetime(day.year, day.month, day.day, tzinfo=UTC) if day else None


def ingest_offers(
    session: Session,
    offers: list[IncomingOffer],
    *,
    rules: Rules | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Upsert the offers; returns {received, new, updated, ignored}.

    new = a job Radar did not know; updated = already known (saved before, or known from an
    alert email: same canonical URL); ignored = not a JobTeaser offer URL, no title, or the
    same offer twice in one request."""
    now = now or utcnow()
    counts = {"received": len(offers), "new": 0, "updated": 0, "ignored": 0}
    companies = company_index(session)
    seen: set[str] = set()
    touched: set[int] = set()
    for offer in offers:
        key = offer_key(offer.url) if offer.url else None
        title = _clean(offer.title, CAPS["title"])
        if key is None or not title or key[0] in seen:
            counts["ignored"] += 1
            continue
        offer_id, url = key
        seen.add(offer_id)
        company = _clean(offer.company, CAPS["company"])
        location = normalize_location(_clean(offer.location, CAPS["location"]) or None)
        existing = session.exec(
            select(Posting).where(Posting.uid == f"{SOURCE}:{offer_id}")
        ).first()
        if existing is not None:
            # Seen again: keep what is stored (the detail page's title and description are
            # better than the listing card's), only fill gaps and bump last_seen.
            raw = RawPosting(
                source=SOURCE,
                external_id=offer_id,
                url=existing.url or url,
                title=existing.title,
                company=existing.company_name or company,
                location=existing.location or location,
                raw_text=existing.raw_text or "",
                deadline=existing.deadline,
            )
        else:
            raw = RawPosting(
                source=SOURCE,
                external_id=offer_id,
                url=url,
                title=title,
                company=company,
                location=location,
                raw_text="",
                posted_at=_parse_shown_date(_clean(offer.date, CAPS["date"]) or None),
                extra={"via": SOURCE, "list": "extension", "detail_fetched": False},
            )
        outcome = upsert_raw(session, raw, run_id=None, now=now, companies=companies)
        if outcome.kind == "new" and not outcome.is_duplicate:
            counts["new"] += 1
        else:
            counts["updated"] += 1
        if outcome.kind != "unchanged":
            for pid in (outcome.posting.id, outcome.primary_id):
                if pid is not None:
                    touched.add(pid)
    if rules is not None:
        for pid in sorted(touched):
            posting = session.get(Posting, pid)
            if posting is not None:
                apply_filters(session, posting, rules)
    set_setting(session, LAST_PASS_KEY, {"at": now.isoformat(), **counts})
    session.commit()
    log.info(
        "jobteaser ingest: received=%d new=%d updated=%d ignored=%d",
        counts["received"],
        counts["new"],
        counts["updated"],
        counts["ignored"],
    )
    return counts


def normalize_stored(session: Session, rules: Rules | None = None) -> dict[str, int]:
    """Stored JobTeaser offer links -> the Italian interface (/it/), the "several locations"
    placeholder -> "Più sedi"; the filters run again on what changed. Any source (extension,
    alert emails, seed). Returns counts."""
    from sqlmodel import col

    counts = {"urls": 0, "locations": 0}
    changed: list[Posting] = []
    for p in session.exec(select(Posting).where(col(Posting.url).contains("jobteaser.com"))):
        key = offer_key(p.url)
        touched = False
        if key is not None and key[1] != p.url:
            p.url = key[1]
            counts["urls"] += 1
            touched = True
        location = normalize_location(p.location)
        if location != p.location:
            p.location = location
            counts["locations"] += 1
            touched = True
        if touched:
            session.add(p)
            changed.append(p)
    if rules is not None:
        for p in changed:
            apply_filters(session, p, rules)
    session.commit()
    return counts


# --------------------------------------------------------------------------- weekly reminder


def last_pass(session: Session) -> dict[str, Any] | None:
    stored = get_setting(LAST_PASS_KEY, None, session=session)
    return dict(stored) if isinstance(stored, dict) and stored.get("at") else None


def _rome_today(now: datetime) -> date:
    try:
        return now.astimezone(ZoneInfo("Europe/Rome")).date()
    except ZoneInfoNotFoundError:
        return now.date()


def reminder(last: dict[str, Any] | None, now: datetime | None = None) -> str | None:
    """Why the Oggi screen shows the "pass JobTeaser" card, or None: "monday" (every Monday,
    Europe/Rome), "stale" (last pass older than 7 days), "never" (no pass yet)."""
    now = now or utcnow()
    at: datetime | None = None
    if last is not None:
        try:
            at = datetime.fromisoformat(str(last["at"]))
        except (KeyError, ValueError):
            at = None
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
    today = _rome_today(now)
    if today.weekday() == 0 and (at is None or _rome_today(at) != today):
        return "monday"  # until the pass is done today
    if at is None:
        return "never"
    return "stale" if now - at > timedelta(days=STALE_DAYS) else None


def _link(name: Any, url: Any, fmt: dict[str, str]) -> dict[str, str] | None:
    """{label, url} for an https JobTeaser link from the config, else None."""
    if not isinstance(url, str) or not url.strip():
        return None
    full = url.strip().replace("{base}", fmt["base"]).replace("{locale}", fmt["locale"])
    parts = urlsplit(full)
    host = parts.netloc.lower()
    if parts.scheme != "https" or not (host == "jobteaser.com" or host.endswith(".jobteaser.com")):
        return None
    return {"label": _clean(str(name or ""), 80) or "Ricerca salvata", "url": full}


def pass_links(cfg: dict[str, Any]) -> list[dict[str, str]]:
    """Direct links for the weekly pass: "Offerte mirate", "Offerte dei partner", then the
    user's saved searches (``links`` in config/jobteaser_searches.yaml). Anything missing or
    not an https JobTeaser URL is left out."""
    from radar.connectors.jobteaser import DEFAULT_BASE_URL, DEFAULT_LOCALE

    links = cfg.get("links")
    if not isinstance(links, dict):
        links = {}
    fmt = {
        "base": str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
        "locale": str(cfg.get("locale") or DEFAULT_LOCALE),
    }
    out = [
        _link("Offerte mirate", links.get("offerte_mirate"), fmt),
        _link("Offerte dei partner", links.get("offerte_partner"), fmt),
    ]
    for entry in links.get("saved_searches") or []:
        if isinstance(entry, dict):
            out.append(_link(entry.get("name"), entry.get("url"), fmt))
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for link in out:
        if link is not None and (link["label"], link["url"]) not in seen:
            seen.add((link["label"], link["url"]))
            unique.append(link)
    return unique
