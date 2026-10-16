"""Teamtailor career sites via the public RSS feed.

``GET https://<company>.teamtailor.com/jobs.rss`` (or ``https://<custom host>/jobs.rss`` when
the slug contains a dot). The feed lists every published job with its HTML description,
locations and department (checked against the career-site sitemap), so the board is
exhaustive. robots.txt is checked first.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from radar.connectors.base import SourceError
from radar.connectors.feeds import child_text, parse_rfc822, parse_xml
from radar.connectors.http import PoliteClient, html_to_text, title_matches
from radar.schemas import RawPosting

NS = "{https://teamtailor.com/locations}"
_JOB_ID_RE = re.compile(r"/jobs/(\d+)")


def feed_url(slug: str) -> str:
    host = slug if "." in slug else f"{slug}.teamtailor.com"
    return f"https://{host}/jobs.rss"


def _locations(item: ET.Element) -> tuple[str | None, str | None]:
    names: list[str] = []
    country: str | None = None
    for loc in item.findall(f"{NS}locations/{NS}location"):
        city = child_text(loc, f"{NS}city") or child_text(loc, f"{NS}name")
        ctry = child_text(loc, f"{NS}country")
        country = country or ctry or None
        label = ", ".join(x for x in (city, ctry) if x)
        if label:
            names.append(label)
    return ("; ".join(dict.fromkeys(names)) or None, country)


def _external_id(item: ET.Element) -> str:
    m = _JOB_ID_RE.search(child_text(item, "link"))
    return m.group(1) if m else child_text(item, "guid")


def _to_raw(item: ET.Element, company: str) -> RawPosting:
    location, country = _locations(item)
    return RawPosting(
        source="teamtailor",
        external_id=_external_id(item),
        url=child_text(item, "link"),
        title=child_text(item, "title"),
        company=company,
        location=location,
        country=country,
        raw_text=html_to_text(child_text(item, "description")),
        department=child_text(item, f"{NS}department") or None,
        posted_at=parse_rfc822(child_text(item, "pubDate")),
        extra={
            "remote_status": child_text(item, "remoteStatus") or None,
            "role": child_text(item, f"{NS}role") or None,
            "guid": child_text(item, "guid") or None,
        },
    )


def fetch_items(client: PoliteClient, slug: str) -> list[ET.Element]:
    root = parse_xml(client.get_text(feed_url(slug), respect_robots=True), "teamtailor")
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise SourceError("teamtailor: unexpected feed format")
    return [it for it in channel.findall("item") if _external_id(it)]


class TeamtailorConnector:
    source = "teamtailor"

    def __init__(
        self,
        client: PoliteClient,
        company: str,
        slug: str,
        *,
        title_filter: list[str] | None = None,
        label: str = "teamtailor board",
    ) -> None:
        self.client = client
        self.company = company
        self.slug = slug
        self.title_filter = title_filter
        self.label = label

    def fetch(self) -> list[RawPosting]:
        return [
            _to_raw(it, self.company)
            for it in fetch_items(self.client, self.slug)
            if title_matches(child_text(it, "title"), self.title_filter)
        ]
