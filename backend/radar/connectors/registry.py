"""Build connector instances from the companies config (``config/companies.yaml``).

Structure (see ``config/companies.example.yaml``)::

    title_filter: [intern, graduate, ...]   # optional, applied to ATS titles
    companies:
      - {name: Acme, ats: greenhouse, slug: acme, region: null, active: true, seed: false}
    linkedin:
      enabled: true          # optional, default true
      max_details: 5         # optional
      queries:
        - {keywords: "AI engineer intern", location: "Italy"}
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from radar.connectors.ashby import AshbyConnector
from radar.connectors.base import Connector
from radar.connectors.greenhouse import GreenhouseConnector
from radar.connectors.http import PoliteClient
from radar.connectors.lever import LeverConnector
from radar.connectors.linkedin_guest import LinkedInGuestConnector
from radar.connectors.smartrecruiters import SmartRecruitersConnector

ATS_SOURCES = ("greenhouse", "lever", "ashby", "smartrecruiters")
ALL_SOURCES = (*ATS_SOURCES, "linkedin")


def _title_filter(value: Any) -> list[str] | None:
    if not value:
        return None
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if str(v).strip()]


def build_connectors(
    client: PoliteClient, companies_cfg: dict[str, Any], sources: list[str] | None = None
) -> list[Connector]:
    """One connector per active company with a supported ATS, plus one LinkedIn connector."""
    wanted = set(sources) if sources else set(ALL_SOURCES)
    global_filter = _title_filter(companies_cfg.get("title_filter"))
    counters: Counter[str] = Counter()
    out: list[Connector] = []
    for entry in companies_cfg.get("companies") or []:
        if not isinstance(entry, dict) or not entry.get("active", True):
            continue
        ats = str(entry.get("ats") or "none").lower()
        slug = entry.get("slug")
        if ats not in ATS_SOURCES or ats not in wanted or not slug:
            continue
        name = str(entry.get("name") or slug)
        # per-company override: `title_filter: false` disables it, a list replaces it
        tf = _title_filter(entry["title_filter"]) if "title_filter" in entry else global_filter
        counters[ats] += 1
        label = f"{ats} board #{counters[ats]}"
        conn: Connector
        if ats == "greenhouse":
            conn = GreenhouseConnector(client, name, str(slug), title_filter=tf, label=label)
        elif ats == "lever":
            conn = LeverConnector(
                client, name, str(slug), region=entry.get("region"), title_filter=tf, label=label
            )
        elif ats == "ashby":
            conn = AshbyConnector(client, name, str(slug), title_filter=tf, label=label)
        else:
            conn = SmartRecruitersConnector(client, name, str(slug), title_filter=tf, label=label)
        out.append(conn)

    li = companies_cfg.get("linkedin") or {}
    if "linkedin" in wanted and isinstance(li, dict) and li.get("enabled", True):
        queries = [
            {"keywords": str(q.get("keywords", "")), "location": str(q.get("location", ""))}
            for q in li.get("queries") or []
            if isinstance(q, dict) and q.get("keywords")
        ]
        if queries:
            out.append(
                LinkedInGuestConnector(client, queries, max_details=int(li.get("max_details", 5)))
            )
    return out
