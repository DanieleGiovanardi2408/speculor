"""Workday career sites via the public CXS JSON endpoint behind ``*.myworkdayjobs.com`` boards.

A board ``https://<tenant>.<wdN>.myworkdayjobs.com/[<locale>/]<site>`` is backed by:

* list: ``POST https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs`` with
  ``{"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "<term>"}``. The response is
  ``{"total", "jobPostings": [{"title", "externalPath", "locationsText", "postedOn",
  "bulletFields"}], "facets"}``. ``total`` is only reliable on the first page (``offset=0``).
* detail: ``GET .../wday/cxs/<tenant>/<site>/job/<externalPath>`` -> ``jobPostingInfo`` with the
  HTML ``jobDescription``, ``startDate``, ``endDate``, country, time type. 404 = removed.

Boards hold hundreds or thousands of jobs, so instead of downloading everything the connector
runs a few ``searchText`` queries (``search_terms``), dedupes by ``externalPath`` and then applies
the usual local ``title_filter`` (Workday's search also matches descriptions).

Politeness: honest user agent, >= 2 s per host, disk cache, back-off on 429/403/5xx, and a
robots.txt check of the ``/wday/cxs/`` path before any request. No cookies, no CSRF token, no
browser emulation: if the endpoint answers 401/403/422 (session or CSRF required), the board
is reported as failing and skipped.

``PoliteClient`` only issues GETs, so the POST helper below reuses its throttle, cache and
counters and mirrors its back-off loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from radar.connectors.base import SourceError
from radar.connectors.http import (
    RETRY_STATUSES,
    FetchResult,
    PoliteClient,
    RobotsDisallowed,
    _retry_after_seconds,
    html_to_text,
    parse_date,
    title_matches,
)
from radar.schemas import RawPosting

log = logging.getLogger(__name__)

HOST_SUFFIX = "myworkdayjobs.com"
MIN_INTERVAL = 2.0
PAGE_SIZE = 20  # Workday rejects larger pages
MAX_PAGES_PER_TERM = 10
DEFAULT_MAX_DETAILS = 40
DEFAULT_SEARCH_TERMS = ("intern", "internship", "graduate", "stage", "tirocinio", "junior")
# statuses that mean "this endpoint wants a session / CSRF token": we stop for that board
SESSION_STATUSES = frozenset({401, 403, 422})

_BOARD_RE = re.compile(
    r"^https?://(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com/"
    r"(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[A-Za-z0-9_-]+)(?P<path>/job/[^?#]+)?",
    re.I,
)
# Workday country descriptors for the "Italia" + "Europa" zones (EU/EEA/CH + UK), used by
# ``countries: europe`` in the config.
EUROPE_COUNTRIES = (
    "Italy", "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czechia",
    "Czech Republic", "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary",
    "Iceland", "Ireland", "Latvia", "Liechtenstein", "Lithuania", "Luxembourg", "Malta",
    "Netherlands", "Norway", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain",
    "Sweden", "Switzerland", "United Kingdom",
)  # fmt: skip
_POSTED_RE = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)


@dataclass(frozen=True)
class Board:
    tenant: str
    wd: str  # data-centre shard, e.g. "wd3"
    site: str

    @property
    def host(self) -> str:
        return f"{self.tenant}.{self.wd}.{HOST_SUFFIX}"

    @property
    def api(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    @property
    def slug(self) -> str:
        return f"{self.tenant}|{self.wd}|{self.site}"

    def posting_url(self, external_path: str) -> str:
        return f"https://{self.host}/{self.site}{external_path}"


def parse_slug(slug: str) -> Board:
    """``"<tenant>|<wdN>|<site>"`` (or a board URL) -> Board. Raises ValueError."""
    text = slug.strip()
    if text.lower().startswith("http"):
        parsed = parse_posting_url(text)
        if parsed is None:
            raise ValueError("workday: not a myworkdayjobs.com board URL")
        return parsed[0]
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 3 or not all(parts) or not re.fullmatch(r"wd\d+", parts[1], re.I):
        raise ValueError("workday: slug must be '<tenant>|<wdN>|<site>'")
    return Board(parts[0].lower(), parts[1].lower(), parts[2])


def board_from_entry(entry: dict[str, Any]) -> Board | None:
    """Board from a companies.yaml entry: ``slug`` or separate ``tenant``/``host``/``site``."""
    slug = entry.get("slug")
    try:
        if slug:
            return parse_slug(str(slug))
        tenant, host, site = entry.get("tenant"), entry.get("host"), entry.get("site")
        wd = re.search(r"\bwd\d+\b", str(host or ""), re.I)
        if tenant and wd and site:
            return parse_slug(f"{tenant}|{wd.group(0)}|{site}")
    except ValueError:
        return None
    return None


def parse_posting_url(url: str) -> tuple[Board, str | None] | None:
    """``(board, externalPath or None)`` for a ``*.myworkdayjobs.com`` URL, else None."""
    m = _BOARD_RE.match(url.strip())
    if not m:
        return None
    board = Board(m.group("tenant").lower(), m.group("wd").lower(), m.group("site"))
    return board, m.group("path")


def _posted_on(text: Any, now: datetime) -> datetime | None:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 5 Days Ago' -> approx datetime.

    '30+ Days Ago' is too vague and gives None.
    """
    m = _POSTED_RE.search(str(text or ""))
    if not m:
        return None
    word = m.group(1).lower()
    if word == "today":
        days = 0
    elif word == "yesterday":
        days = 1
    elif "+" in word:
        return None
    else:
        days = int(m.group(2))
    return (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


def _date_dt(value: Any) -> datetime | None:
    d = parse_date(value)
    return datetime(d.year, d.month, d.day, tzinfo=UTC) if d else None


def _country(info: dict[str, Any]) -> str | None:
    req_loc = info.get("jobRequisitionLocation") or {}
    code = (req_loc.get("country") or {}).get("alpha2Code")
    if code:
        return str(code).upper()
    name = (info.get("country") or {}).get("descriptor")
    return str(name) if name else None


def _to_raw(
    item: dict[str, Any],
    board: Board,
    company: str,
    detail: dict[str, Any] | None,
    now: datetime,
) -> RawPosting:
    path = str(item["externalPath"])
    info = (detail or {}).get("jobPostingInfo") or {}
    locations = [info.get("location"), *(info.get("additionalLocations") or [])]
    location = "; ".join(str(x).strip() for x in locations if x) or (
        str(item.get("locationsText") or "").strip() or None
    )
    bullets = item.get("bulletFields") or []
    posted = _date_dt(info.get("startDate")) or _posted_on(
        info.get("postedOn") or item.get("postedOn"), now
    )
    return RawPosting(
        source="workday",
        external_id=f"{board.tenant}:{path.rsplit('/', 1)[-1]}",
        url=str(info.get("externalUrl") or board.posting_url(path)),
        title=str(info.get("title") or item.get("title") or "").strip(),
        company=company,
        location=location,
        country=_country(info),
        raw_text=html_to_text(info.get("jobDescription")),
        employment_type=(str(info["timeType"]) if info.get("timeType") else None),
        posted_at=posted,
        deadline=parse_date(info.get("endDate")),
        extra={
            "req_id": info.get("jobReqId") or (str(bullets[0]) if bullets else None),
            "posted_on": item.get("postedOn") or info.get("postedOn"),
            "locations_text": item.get("locationsText"),
            "detail_fetched": detail is not None,
            "workday_site": board.slug,
        },
    )


# ---------------------------------------------------------------------- HTTP helpers
def _ensure_interval(client: PoliteClient) -> None:
    current = client.intervals.get(HOST_SUFFIX, 0.0)
    if current < MIN_INTERVAL:
        client.intervals[HOST_SUFFIX] = MIN_INTERVAL


def _check_robots(client: PoliteClient, url: str) -> None:
    if not client.robots_allowed(url):
        raise RobotsDisallowed(
            f"{httpx.URL(url).host}: robots.txt disallows /wday/cxs/ for our user agent"
        )


def post_json(
    client: PoliteClient, url: str, body: dict[str, Any], *, use_cache: bool = True
) -> Any:
    """POST a JSON body with PoliteClient's throttle, cache and back-off. Returns parsed JSON."""
    host = httpx.URL(url).host
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(f"POST {url}\n{payload}".encode()).hexdigest()
    if use_cache:
        cached = client._cache_read(key)
        if cached is not None and cached.status == 200:
            return cached.json()
    attempt = 0
    while True:
        client._throttle(host)
        client.network_requests += 1
        try:
            resp = client._client.post(
                url,
                content=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            attempt += 1
            if attempt > client.max_retries:
                raise SourceError(f"{host}: network error ({type(exc).__name__})") from exc
            client._sleep(min(client.max_backoff, client.backoff_base * 2 ** (attempt - 1)))
            continue
        status = resp.status_code
        if status == 200:
            res = FetchResult(status=200, text=resp.text, url=str(resp.url))
            try:
                data = res.json()
            except ValueError as exc:
                raise SourceError(f"{host}: response is not JSON") from exc
            client._cache_write(key, res)
            return data
        if status in RETRY_STATUSES:
            attempt += 1
            if attempt > client.max_retries:
                hint = " (session/CSRF token required?)" if status == 403 else ""
                raise SourceError(f"{host}: HTTP {status} after {client.max_retries} retries{hint}")
            wait = _retry_after_seconds(resp.headers.get("Retry-After"))
            if wait is None:
                wait = client.backoff_base * 2 ** (attempt - 1)
            if wait > client.max_backoff:
                raise SourceError(f"{host}: HTTP {status}, Retry-After too long ({wait:.0f}s)")
            log.warning("workday: status %s, backing off %.0fs (attempt %d)", status, wait, attempt)
            client._sleep(wait)
            continue
        if status in SESSION_STATUSES:
            raise SourceError(
                f"{host}: HTTP {status}; endpoint wants a session/CSRF token, not fetching"
            )
        raise SourceError(f"{host}: HTTP {status}")


def _flat_facets(facets: Any) -> list[dict[str, Any]]:
    """Facets with nested groups (e.g. ``locationMainGroup``) flattened."""
    out: list[dict[str, Any]] = []
    for f in facets if isinstance(facets, list) else []:
        if not isinstance(f, dict):
            continue
        values = f.get("values") or []
        if values and isinstance(values[0], dict) and "facetParameter" in values[0]:
            out.extend(_flat_facets(values))
        else:
            out.append(f)
    return out


# ---------------------------------------------------------------------- connector
class WorkdayConnector:
    source = "workday"

    def __init__(
        self,
        client: PoliteClient,
        company: str,
        slug: str | Board,
        *,
        title_filter: list[str] | None = None,
        search_terms: list[str] | tuple[str, ...] | None = None,
        applied_facets: dict[str, list[str]] | None = None,
        countries: list[str] | tuple[str, ...] | str | None = None,
        max_details: int = DEFAULT_MAX_DETAILS,
        max_pages_per_term: int = MAX_PAGES_PER_TERM,
        label: str = "workday board",
        now: datetime | None = None,
    ) -> None:
        self.client = client
        self.company = company
        self.board = slug if isinstance(slug, Board) else parse_slug(slug)
        self.title_filter = title_filter
        # [] or [""] = no search text: list the whole board (bounded by max_pages_per_term)
        terms = DEFAULT_SEARCH_TERMS if search_terms is None else tuple(search_terms)
        self.search_terms: tuple[str, ...] = tuple(t.strip() for t in terms) or ("",)
        self.applied_facets = applied_facets or {}
        if isinstance(countries, str):
            countries = EUROPE_COUNTRIES if countries.lower() == "europe" else (countries,)
        # country names to scope the search with the tenant's own country facet (auto-detected)
        self.countries: tuple[str, ...] = tuple(countries or ())
        self.scope = "explicit facets" if self.applied_facets else "global"
        self.max_details = max_details
        self.max_pages_per_term = max_pages_per_term
        self.label = label
        self._now = now
        _ensure_interval(client)

    def _search(self, term: str) -> list[dict[str, Any]]:
        url = f"{self.board.api}/jobs"
        items: list[dict[str, Any]] = []
        total: int | None = None
        offset = 0
        for _ in range(self.max_pages_per_term):
            data = post_json(
                self.client,
                url,
                {
                    "appliedFacets": self.applied_facets,
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": term,
                },
            )
            if not isinstance(data, dict) or not isinstance(data.get("jobPostings"), list):
                raise SourceError("workday: unexpected response format")
            page = [p for p in data["jobPostings"] if isinstance(p, dict) and p.get("externalPath")]
            items.extend(page)
            if total is None:  # only the first page carries a reliable total
                total = int(data.get("total") or 0)
            offset += PAGE_SIZE
            if not page or offset >= total:
                break
        return items

    def _detect_country_facet(self) -> None:
        """Find the tenant's country facet (its name differs per tenant) and apply it.

        One unfiltered request with the first search term; its ``facets`` list is scanned for a
        parameter containing "country" whose values match ``self.countries``. Nothing found ->
        the search stays global.
        """
        data = post_json(
            self.client,
            f"{self.board.api}/jobs",
            {
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": 0,
                "searchText": self.search_terms[0],
            },
        )
        wanted = {c.lower() for c in self.countries}
        for facet in _flat_facets(data.get("facets") if isinstance(data, dict) else None):
            param = str(facet.get("facetParameter") or "")
            if "country" not in param.lower():
                continue
            ids = [
                str(v["id"])
                for v in facet.get("values") or []
                if isinstance(v, dict)
                and v.get("id")
                and str(v.get("descriptor") or "").lower() in wanted
            ]
            if ids:
                self.applied_facets = {param: ids}
                self.scope = "country facet"
                return
        log.info("workday: no country facet on this board; searching globally")

    def list_postings(self) -> list[dict[str, Any]]:
        """Search results for every term, deduped by externalPath (first seen wins)."""
        _check_robots(self.client, f"{self.board.api}/jobs")
        if self.countries and not self.applied_facets:
            self._detect_country_facet()
        seen: dict[str, dict[str, Any]] = {}
        for term in self.search_terms:
            for item in self._search(term):
                seen.setdefault(str(item["externalPath"]), item)
        return list(seen.values())

    def _detail(self, external_path: str) -> dict[str, Any] | None:
        res = self.client.fetch(
            f"{self.board.api}{external_path}",
            ok_statuses={200, 404, 410},
            respect_robots=True,
        )
        if res.status != 200:
            return None
        data = res.json()
        return data if isinstance(data, dict) else None

    def fetch(self) -> list[RawPosting]:
        now = self._now or datetime.now(UTC)
        items = [
            p
            for p in self.list_postings()
            if title_matches(str(p.get("title") or ""), self.title_filter)
        ]
        out: list[RawPosting] = []
        details = 0
        for item in items:
            detail: dict[str, Any] | None = None
            if details < self.max_details:
                details += 1
                try:
                    detail = self._detail(str(item["externalPath"]))
                except (SourceError, ValueError):
                    detail = None
                    details = self.max_details  # stop hammering after a failure
            out.append(_to_raw(item, self.board, self.company, detail, now))
        if len(items) > self.max_details:
            log.info("workday: detail cap reached (%d of %d)", self.max_details, len(items))
        return out


def fetch_single(client: PoliteClient, url: str, company: str = "") -> RawPosting | None:
    """One posting from its myworkdayjobs.com URL; None when Workday says 404/410 or unposted.

    Raises SourceError for non-posting URLs, robots.txt disallow, or any HTTP failure.
    """
    parsed = parse_posting_url(url)
    if parsed is None or not parsed[1]:
        raise SourceError("workday: not a posting URL")
    board, path = parsed
    _ensure_interval(client)
    res = client.fetch(f"{board.api}{path}", ok_statuses={200, 404, 410}, respect_robots=True)
    if res.status in (404, 410):
        return None
    try:
        data = res.json()
    except ValueError as exc:
        raise SourceError("workday: response is not JSON") from exc
    info = data.get("jobPostingInfo") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        raise SourceError("workday: unexpected response format")
    if info.get("posted") is False:
        return None
    name = company or str((data.get("hiringOrganization") or {}).get("name") or board.tenant)
    item = {"externalPath": path, "title": info.get("title"), "postedOn": info.get("postedOn")}
    return _to_raw(item, board, name, data, datetime.now(UTC))


__all__ = [
    "DEFAULT_SEARCH_TERMS",
    "EUROPE_COUNTRIES",
    "Board",
    "WorkdayConnector",
    "board_from_entry",
    "fetch_single",
    "parse_posting_url",
    "parse_slug",
    "post_json",
]
