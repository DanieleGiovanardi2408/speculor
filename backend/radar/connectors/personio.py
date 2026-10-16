"""Personio career pages via the public XML feed.

``GET https://<company>.jobs.personio.de/xml`` (or ``.jobs.personio.com`` with ``region: com``),
optional ``?language=<lang>``. The feed is the complete list of published positions, so the
board is exhaustive (close-by-absence applies). robots.txt is checked before the request.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from radar.connectors.base import SourceError
from radar.connectors.feeds import child_text, parse_xml
from radar.connectors.http import PoliteClient, html_to_text, parse_datetime, title_matches
from radar.schemas import RawPosting

_URL_RE = re.compile(r"^https?://([a-z0-9-]+)\.jobs\.personio\.(de|com)/job/(\d+)", re.I)


def host_for(slug: str, region: str | None = None) -> str:
    tld = "com" if (region or "").lower() == "com" else "de"
    return f"https://{slug}.jobs.personio.{tld}"


def parse_posting_url(url: str) -> tuple[str, str, str] | None:
    """``(slug, position_id, tld)`` for a Personio-hosted posting URL, else None."""
    m = _URL_RE.match(url.strip())
    return (m.group(1).lower(), m.group(3), m.group(2).lower()) if m else None


def _description(pos: ET.Element) -> str:
    parts: list[str] = []
    for jd in pos.findall("jobDescriptions/jobDescription"):
        heading = child_text(jd, "name")
        body = html_to_text(child_text(jd, "value"))
        block = f"{heading}\n{body}".strip()
        if block:
            parts.append(block)
    return "\n\n".join(parts)


def _to_raw(pos: ET.Element, company: str, base: str, lang: str | None) -> RawPosting:
    pid = child_text(pos, "id")
    offices = [child_text(pos, "office")] + [
        (o.text or "").strip() for o in pos.findall("additionalOffices/office")
    ]
    offices = [o for o in offices if o]
    url = f"{base}/job/{pid}" + (f"?language={lang}" if lang else "")
    return RawPosting(
        source="personio",
        external_id=pid,
        url=url,
        title=child_text(pos, "name"),
        company=company,
        location=", ".join(dict.fromkeys(offices)) or None,
        raw_text=_description(pos),
        department=child_text(pos, "department") or None,
        employment_type=child_text(pos, "employmentType") or None,
        posted_at=parse_datetime(child_text(pos, "createdAt")),
        extra={
            "seniority": child_text(pos, "seniority") or None,
            "schedule": child_text(pos, "schedule") or None,
            "subcompany": child_text(pos, "subcompany") or None,
            "category": child_text(pos, "recruitingCategory") or None,
        },
    )


def fetch_positions(
    client: PoliteClient, slug: str, region: str | None = None, lang: str | None = None
) -> list[ET.Element]:
    """All ``<position>`` elements of the feed. Raises SourceError (incl. robots disallow)."""
    params = {"language": lang} if lang else None
    text = client.get_text(f"{host_for(slug, region)}/xml", params, respect_robots=True)
    root = parse_xml(text, "personio")
    if root.tag != "workzag-jobs":
        raise SourceError("personio: unexpected feed format")
    return [p for p in root.findall("position") if child_text(p, "id")]


class PersonioConnector:
    source = "personio"

    def __init__(
        self,
        client: PoliteClient,
        company: str,
        slug: str,
        *,
        region: str | None = None,
        lang: str | None = None,
        title_filter: list[str] | None = None,
        label: str = "personio board",
    ) -> None:
        self.client = client
        self.company = company
        self.slug = slug
        self.region = region
        self.lang = lang
        self.title_filter = title_filter
        self.label = label

    def fetch(self) -> list[RawPosting]:
        base = host_for(self.slug, self.region)
        return [
            _to_raw(pos, self.company, base, self.lang)
            for pos in fetch_positions(self.client, self.slug, self.region, self.lang)
            if title_matches(child_text(pos, "name"), self.title_filter)
        ]


def fetch_single(client: PoliteClient, url: str) -> RawPosting | None:
    """One posting looked up in the (cached) feed; None when the feed no longer lists it."""
    parsed = parse_posting_url(url)
    if parsed is None:
        return None
    slug, pid, tld = parsed
    for pos in fetch_positions(client, slug, tld):
        if child_text(pos, "id") == pid:
            return _to_raw(pos, slug, host_for(slug, tld), None)
    return None
