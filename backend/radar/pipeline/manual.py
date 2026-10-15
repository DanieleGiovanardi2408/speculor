"""Add a single posting from a URL pasted by the user.

Supported ATS URLs (Greenhouse, Lever, Ashby, SmartRecruiters) are read through their public
APIs. Any other page is fetched once (robots.txt permitting) and parsed from JSON-LD
``JobPosting`` if present, else from ``<title>``/OpenGraph tags and visible text. When the page
cannot be fetched (robots.txt disallow, e.g. LinkedIn; login wall; error), a stub posting is
created from what the URL itself says, with ``status="unknown"``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import unquote, urlsplit

from selectolax.lexbor import LexborHTMLParser
from sqlmodel import Session, select

from radar.connectors import ashby, greenhouse, lever, smartrecruiters
from radar.connectors.base import SourceError
from radar.connectors.http import PoliteClient, html_to_text, parse_date, parse_datetime
from radar.connectors.linkedin_guest import parse_job_id
from radar.models import Company, Posting
from radar.pipeline.normalize import (
    canonical_url,
    content_hash,
    country_code,
    dedupe_key,
    sha256,
    zone_for,
)
from radar.schemas import RawPosting
from radar.timeutil import utcnow

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 20000
LOGIN_WALLED = ("jobteaser.com",)


# ---------------------------------------------------------------------- JSON-LD
def _iter_jsonld(obj: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(obj, list):
        for item in obj:
            out.extend(_iter_jsonld(item))
    elif isinstance(obj, dict):
        out.append(obj)
        if "@graph" in obj:
            out.extend(_iter_jsonld(obj["@graph"]))
    return out


def _is_job_posting(obj: dict[str, Any]) -> bool:
    t = obj.get("@type")
    types = t if isinstance(t, list) else [t]
    return "JobPosting" in types


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, list):
        value = value[0] if value else None
        return _name(value)
    return str(value).strip() if value else None


def _jsonld_location(job: dict[str, Any]) -> tuple[str | None, str | None]:
    locs = job.get("jobLocation")
    if isinstance(locs, dict):
        locs = [locs]
    if not isinstance(locs, list) or not locs:
        if job.get("jobLocationType") == "TELECOMMUTE":
            return "Remote", None
        return None, None
    addr = (locs[0] or {}).get("address") if isinstance(locs[0], dict) else None
    if isinstance(addr, str):
        return addr, None
    if not isinstance(addr, dict):
        return None, None
    country = _name(addr.get("addressCountry"))
    parts = [addr.get("addressLocality"), addr.get("addressRegion"), country]
    location = ", ".join(str(p).strip() for p in parts if p and str(p).strip()) or None
    return location, country


def parse_jobposting_jsonld(html: str) -> dict[str, Any] | None:
    """First schema.org JobPosting found in ``<script type=application/ld+json>`` blocks."""
    tree = LexborHTMLParser(html)
    for script in tree.css('script[type="application/ld+json"]'):
        raw = script.text(deep=True) or ""
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        for obj in _iter_jsonld(data):
            if _is_job_posting(obj):
                return obj
    return None


def _from_html(url: str, html: str) -> RawPosting:
    job = parse_jobposting_jsonld(html)
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    if job is not None:
        location, country = _jsonld_location(job)
        return RawPosting(
            source="manual",
            external_id="",
            url=url,
            title=str(job.get("title") or "").strip() or _title_from_url(url),
            company=_name(job.get("hiringOrganization")) or host,
            location=location,
            country=country,
            raw_text=html_to_text(str(job.get("description") or ""), unescape_first=True),
            employment_type=_name(job.get("employmentType")),
            posted_at=parse_datetime(job.get("datePosted")),
            deadline=parse_date(job.get("validThrough")),
            extra={"parsed_from": "jsonld"},
        )
    tree = LexborHTMLParser(html)

    def meta(prop: str) -> str | None:
        node = tree.css_first(f'meta[property="{prop}"]') or tree.css_first(f'meta[name="{prop}"]')
        val = node.attributes.get("content") if node is not None else None
        return val.strip() if val else None

    title_node = tree.css_first("title")
    title = meta("og:title") or (title_node.text().strip() if title_node is not None else "")
    company = meta("og:site_name") or host
    for node in tree.css("script, style, noscript, template, nav, footer, header"):
        node.decompose()
    main = tree.css_first("main") or tree.css_first("article") or tree.body
    text = html_to_text(main.html if main is not None else "")
    return RawPosting(
        source="manual",
        external_id="",
        url=url,
        title=title or _title_from_url(url),
        company=company,
        raw_text=text[:MAX_TEXT_CHARS],
        extra={"parsed_from": "html", "description": meta("og:description")},
    )


# ---------------------------------------------------------------------- URL-only fallback
def _title_from_url(url: str) -> str:
    path = unquote(urlsplit(url).path).rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else ""
    slug = re.sub(r"\d{5,}", " ", slug)
    words = [w for w in re.split(r"[-_+\s]+", slug) if w]
    return " ".join(words).strip().capitalize() or "Untitled posting"


def _stub_from_url(url: str) -> RawPosting:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    title, company = _title_from_url(url), host
    if host.endswith("linkedin.com"):
        m = re.search(r"/jobs/view/(.+?)-at-(.+)-\d+/?$", urlsplit(url).path)
        if m:
            title = m.group(1).replace("-", " ").strip().capitalize()
            company = m.group(2).replace("-", " ").strip().title()
    return RawPosting(
        source="manual",
        external_id="",
        url=url,
        title=title,
        company=company,
        extra={"stub": True},
    )


# ---------------------------------------------------------------------- dispatch
def _from_ats(client: PoliteClient, url: str) -> RawPosting | bool | None:
    """RawPosting for a supported ATS URL; None if it is closed/404; False if not an ATS URL."""
    gh = greenhouse.parse_posting_url(url)
    if gh:
        return greenhouse.fetch_single(client, gh[0], gh[1])
    lv = lever.parse_posting_url(url)
    if lv:
        return lever.fetch_single(client, lv[0], lv[1], lv[2])
    ab = ashby.parse_posting_url(url)
    if ab and ab[1]:
        return ashby.fetch_single(client, ab[0], ab[1])
    sr = smartrecruiters.parse_posting_url(url)
    if sr:
        return smartrecruiters.fetch_single(client, sr[0], sr[1])
    return False


def fetch_raw(client: PoliteClient, url: str) -> tuple[RawPosting, str]:
    """Best-effort RawPosting for ``url`` plus a status ("open" | "closed" | "unknown")."""
    host = urlsplit(url).netloc.lower()
    try:
        got = _from_ats(client, url)
        if isinstance(got, RawPosting):
            return got, "open"
        if got is None:  # ATS says 404 / inactive
            return _stub_from_url(url), "closed"
        if host.endswith("linkedin.com") or any(m in host for m in LOGIN_WALLED):
            # LinkedIn's robots.txt disallows our UA; login-walled sites are never fetched.
            return _stub_from_url(url), "unknown"
        html = client.get_text(url, respect_robots=True)
        return _from_html(url, html), "open"
    except SourceError as exc:
        log.info("manual: could not fetch page (%s)", type(exc).__name__)
        return _stub_from_url(url), "unknown"


def add_posting_from_url(session: Session, url: str, client: PoliteClient | None = None) -> Posting:
    """Create (or return the existing) manual Posting for ``url``."""
    url = url.strip()
    canon = canonical_url(url)
    existing = session.exec(select(Posting).where(Posting.canonical_url == canon)).first()
    if existing is not None:
        return existing

    own_client = client is None
    pc = client or PoliteClient.from_settings()
    try:
        raw, status = fetch_raw(pc, url)
    finally:
        if own_client:
            pc.close()

    code = country_code(raw.country, raw.location)
    company_row = session.exec(select(Company).where(Company.name == raw.company)).first()
    digest = sha256(canon)[:16]
    li_id = parse_job_id(url) if urlsplit(url).netloc.lower().endswith("linkedin.com") else None
    posting = Posting(
        uid=f"manual:{digest}",
        source="manual",
        external_id=digest,
        url=url,
        canonical_url=canon,
        title=raw.title,
        company_name=raw.company,
        company_id=company_row.id if company_row is not None else None,
        location=raw.location,
        country=raw.country,
        country_code=code,
        zone=zone_for(code),
        deadline=raw.deadline,
        raw_text=raw.raw_text or None,
        content_hash=content_hash(raw.title, raw.company, raw.location, raw.raw_text),
        dedupe_key=dedupe_key(raw.company, raw.title, code),
        status=status,
        closed_at=utcnow() if status == "closed" else None,
        via=("LinkedIn" if li_id else None),
    )
    session.add(posting)
    session.commit()
    session.refresh(posting)
    return posting
