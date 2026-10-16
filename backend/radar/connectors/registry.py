"""Build connector instances from the companies config (``config/companies.yaml``).

Structure (see ``config/companies.example.yaml``)::

    title_filter: [intern, graduate, ...]   # optional, applied to ATS titles
    companies:
      - {name: Acme, ats: greenhouse, slug: acme, region: null, active: true, seed: false}
      - {name: Acme Corp, ats: workday, slug: "acme|wd3|Acme_Careers"}  # tenant|wdN|site
    linkedin:
      enabled: true          # optional, default true
      max_details: 5         # optional
      queries:
        - {keywords: "AI engineer intern", location: "Italy"}
    email:                   # optional; the "email" source needs RADAR_IMAP_* env vars
      enabled: true          # optional, default true
      # Only LinkedIn and JobTeaser alert senders are ever processed (no generic senders).
    jobteaser:               # optional; needs the browser profile from `make jobteaser-login`
      enabled: true          # searches: config/jobteaser_searches.yaml
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from radar.connectors.ashby import AshbyConnector
from radar.connectors.base import Connector
from radar.connectors.email_alerts import EmailAlertsConnector
from radar.connectors.greenhouse import GreenhouseConnector
from radar.connectors.http import PoliteClient
from radar.connectors.jobteaser import from_settings as jobteaser_from_settings
from radar.connectors.lever import LeverConnector
from radar.connectors.linkedin_guest import LinkedInGuestConnector
from radar.connectors.smartrecruiters import SmartRecruitersConnector
from radar.connectors.workday import WorkdayConnector, board_from_entry

if TYPE_CHECKING:
    from radar.config import Settings

ATS_SOURCES = ("greenhouse", "lever", "ashby", "smartrecruiters", "workday")
ALL_SOURCES = (*ATS_SOURCES, "linkedin", "email", "jobteaser")


def _title_filter(value: Any) -> list[str] | None:
    if not value:
        return None
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if str(v).strip()]


def build_connectors(
    client: PoliteClient,
    companies_cfg: dict[str, Any],
    sources: list[str] | None = None,
    settings: Settings | None = None,
) -> list[Connector]:
    """One connector per active company with a supported ATS, plus one LinkedIn connector,
    plus the email-alerts connector when the RADAR_IMAP_* settings are set, plus the JobTeaser
    connector when its browser profile exists (``RADAR_JOBTEASER_PROFILE``)."""
    wanted = set(sources) if sources else set(ALL_SOURCES)
    global_filter = _title_filter(companies_cfg.get("title_filter"))
    counters: Counter[str] = Counter()
    out: list[Connector] = []
    for entry in companies_cfg.get("companies") or []:
        if not isinstance(entry, dict) or not entry.get("active", True):
            continue
        ats = str(entry.get("ats") or "none").lower()
        slug = entry.get("slug")
        board = board_from_entry(entry) if ats == "workday" else None
        if board is not None:
            slug = board.slug
        elif ats == "workday":
            slug = None  # malformed workday entry: skipped
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
        elif ats == "workday" and board is not None:
            terms = entry.get("search_terms")
            facets = entry.get("applied_facets")
            countries = entry.get("countries")
            conn = WorkdayConnector(
                client,
                name,
                board,
                title_filter=tf,
                search_terms=[str(t) for t in terms] if isinstance(terms, list) else None,
                applied_facets=facets if isinstance(facets, dict) else None,
                countries=countries if isinstance(countries, str | list) else None,
                max_details=int(entry.get("max_details", 40)),
                label=label,
            )
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

    em = companies_cfg.get("email") or {}
    if "email" in wanted and isinstance(em, dict) and em.get("enabled", True):
        if settings is None:
            from radar.config import get_settings

            settings = get_settings()
        # Deliberately no extra senders: only LinkedIn and JobTeaser alerts are read.
        email_conn = EmailAlertsConnector.from_settings(settings)
        if email_conn is not None:
            out.append(email_conn)

    jt = companies_cfg.get("jobteaser") or {}
    if "jobteaser" in wanted and isinstance(jt, dict) and jt.get("enabled", True):
        if settings is None:
            from radar.config import get_settings

            settings = get_settings()
        jt_conn = jobteaser_from_settings(client, settings)
        if jt_conn is not None:
            out.append(jt_conn)
    return out
