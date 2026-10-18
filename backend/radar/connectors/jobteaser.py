"""JobTeaser (Luiss career service): offer detail pages through the user's own browser profile.

Local Mac only, never CI. The session lives only in a dedicated Chromium profile
(``RADAR_JOBTEASER_PROFILE``) created by ``make jobteaser-login``; cookies are never read,
exported or logged. The browser layer (:mod:`radar.connectors.jobteaser_browser`, Playwright,
optional ``browser`` extra) is injected, so everything here is tested with fake pages.

Listing and search pages are disallowed by the host's robots.txt, so this connector never opens
them, never paginates and never scrolls. Offers reach Radar from two other paths: JobTeaser
alert emails (``email`` source) and the "Salva in Radar" browser extension, which the user
clicks on a listing page they opened themselves (``POST /api/ingest/jobteaser``, see
:mod:`radar.pipeline.jobteaser_ingest`). Those offers have no description yet; this connector
adds it, one page at a time, with a fixed pause of :data:`PAGE_PAUSE_S` seconds (no
randomization) and at most ``max_details`` pages per day:

1. Nothing pending (or today's cap used): the browser is not started at all.
2. Session check: ``<base>/<locale>`` (the dashboard, allowed by robots.txt). A redirect to
   ``/users/sign_in`` or to an SSO host means the session expired -> :class:`SourcePaused`. A
   captcha/challenge/block page anywhere -> :class:`SourcePaused` too (never solved, never
   retried).
3. Single offer pages (``/<locale>/job-offers/<id>``), each checked against robots.txt with
   :class:`RobotsRules` before it is opened: offers saved with the extension first, then offers
   known only from alert emails or the seed. JSON-LD ``JobPosting`` first, then embedded JSON,
   then the DOM. 404/410 or an "expired" marker = closed: not returned, remembered in a small
   state file (ids and counts only).

Not exhaustive: never closes by absence. Logs carry counts only.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from radar.connectors.base import SourceError
from radar.connectors.email_parsers.jobteaser import normalize_location, offer_key
from radar.connectors.http import PoliteClient, html_to_text, parse_date, parse_datetime
from radar.connectors.robots import RobotsRules
from radar.connectors.status import SourcePaused
from radar.pipeline.normalize import jobteaser_offer_id
from radar.schemas import RawPosting
from radar.timeutil import today as utc_today

if TYPE_CHECKING:
    from radar.config import Settings

log = logging.getLogger(__name__)

SOURCE = "jobteaser"
DEFAULT_BASE_URL = "https://luiss.jobteaser.com"
DEFAULT_LOCALE = "it"
LOGIN_PATH = "/{locale}/users/sign_in"
PAUSE_ACTION = (
    "Esegui `make jobteaser-login`, accedi con l'account Luiss, poi "
    "`radar connectors resume jobteaser`"
)
BLOCK_ACTION = (
    "JobTeaser ha mostrato un controllo anti-bot o un blocco: non viene aggirato. Apri "
    "JobTeaser a mano più tardi, poi `radar connectors resume jobteaser`"
)
OFFER_BLOCK_REASON = "verifica anti-bot sulle pagine offerta"
OFFER_BLOCK_ACTION = (
    "Il login funziona, ma JobTeaser (Cloudflare) chiede una verifica anti-bot quando il "
    "browser automatico apre le pagine delle offerte: Radar non la aggira e non riprova. Le "
    "offerte salvate con «Salva in Radar» e quelle delle email restano in Radar senza "
    "descrizione (non vengono valutate). Per riprovare più avanti: `radar connectors resume "
    "jobteaser`, poi `make jobteaser-test`."
)

# --------------------------------------------------------------------------- page classification

_LOGIN_PATH_RE = re.compile(
    r"/(users/sign[_-]?in|sign[_-]?in|log[_-]?in|signin|session|connexion|auth/|saml|cas/|sso)",
    re.I,
)
_LOGIN_FORM_RE = re.compile(
    r"<form[^>]+action=[\"'][^\"']*(sign[_-]?in|log[_-]?in|session|saml|sso)", re.I
)
_BLOCK_TITLE_RE = re.compile(
    r"security checkup|just a moment|attention required|access denied|un momento|"
    r"verifica di sicurezza|vérification",
    re.I,
)
_BLOCK_MARKERS = (
    "challenge-platform",
    "cf_chl_opt",
    "__cf_chl",
    "cf-turnstile",
    "challenges.cloudflare.com",
    "hcaptcha.com/captcha",
    "g-recaptcha",
    "px-captcha",
    "datadome",
)
CLOSED_MARKERS = (
    "offerta scaduta",
    "offerta non è più disponibile",
    "offerta non e' piu disponibile",
    "questa offerta è scaduta",
    "offer has expired",
    "offer expired",
    "this job offer is no longer available",
    "this offer is no longer available",
    "no longer accepting applications",
    "offre expirée",
    "cette offre n'est plus disponible",
)


@dataclass
class Page:
    """What the browser layer returns for one navigation (kept in memory, never logged)."""

    url: str  # final URL after redirects
    status: int | None
    html: str


class Browser(Protocol):
    def visit(self, url: str) -> Page: ...

    def close(self) -> None: ...


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def is_login_page(page: Page, base_url: str = DEFAULT_BASE_URL) -> bool:
    """Session gone: redirected to the sign-in page or away to an SSO/identity host."""
    parts = urlsplit(page.url)
    host = parts.netloc.lower()
    if host and not (host == "jobteaser.com" or host.endswith(".jobteaser.com")):
        return True
    if _LOGIN_PATH_RE.search(parts.path):
        return True
    html = page.html
    if 'name="user[password]"' in html or 'id="user_password"' in html:
        return True
    return bool(re.search(r"<input[^>]+type=[\"']password", html, re.I)) or bool(
        _LOGIN_FORM_RE.search(html)
    )


def is_block_page(page: Page) -> bool:
    """Captcha, Cloudflare challenge, rate limit or another anti-bot block page."""
    if page.status == 429:
        return True
    lowered = page.html.lower()
    if _BLOCK_TITLE_RE.search(_title(page.html)):
        return True
    if any(marker in lowered for marker in _BLOCK_MARKERS):
        return True
    return page.status == 403


def check_page(page: Page, base_url: str = DEFAULT_BASE_URL, *, offer: bool = False) -> None:
    """Raise SourcePaused for an expired session or a block page (login first: a sign-in page
    may embed a captcha widget, and the fix there is logging in again). ``offer``: the page is
    a single offer opened after a working session check."""
    if is_login_page(page, base_url):
        raise SourcePaused(SOURCE, "sessione scaduta", PAUSE_ACTION)
    if is_block_page(page):
        if offer:
            raise SourcePaused(SOURCE, OFFER_BLOCK_REASON, OFFER_BLOCK_ACTION)
        raise SourcePaused(SOURCE, "captcha o blocco", BLOCK_ACTION)


def is_closed_page(page: Page) -> bool:
    if page.status in (404, 410):
        return True
    text = html_to_text(page.html).lower()
    return any(marker in text for marker in CLOSED_MARKERS)


# --------------------------------------------------------------------------- offer extraction
# (detail pages: JSON-LD, embedded JSON such as ``__NEXT_DATA__``, then the DOM)

_ID_RE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{3,})$", re.I
)
_ID_KEYS = ("id", "uuid", "job_offer_id", "jobOfferId", "offer_id", "offerId")
_TITLE_KEYS = ("title", "name", "position", "position_name", "positionName", "job_title")
_COMPANY_KEYS = (
    "company",
    "organization",
    "organisation",
    "employer",
    "recruiter",
    "company_name",
    "companyName",
    "organization_name",
    "organizationName",
)
_LOCATION_KEYS = (
    "location",
    "locations",
    "city",
    "cities",
    "place",
    "address",
    "location_name",
    "locationName",
    "formatted_location",
)
_CONTRACT_KEYS = (
    "contract_type",
    "contractType",
    "contract",
    "contract_name",
    "contractName",
    "job_type",
    "jobType",
    "employment_type",
    "employmentType",
)
_URL_KEYS = ("url", "path", "href", "link", "permalink", "share_url", "shareUrl", "slug")
_DESC_KEYS = (
    "description",
    "description_html",
    "descriptionHtml",
    "content",
    "body",
    "job_description",
    "jobDescription",
    "mission",
    "profile",
)
_POSTED_KEYS = (
    "published_at",
    "publishedAt",
    "publication_date",
    "publicationDate",
    "created_at",
    "createdAt",
    "start_publication_date",
    "datePosted",
)
_CLOSING_KEYS = (
    "closing_date",
    "closingDate",
    "expires_at",
    "expiresAt",
    "expiration_date",
    "expirationDate",
    "end_date",
    "endDate",
    "application_deadline",
    "applicationDeadline",
    "valid_through",
    "validThrough",
    "deadline",
    "end_publication_date",
)


def _str(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return ""


def _first(obj: dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        if k in obj and obj[k] not in (None, "", [], {}):
            return obj[k]
    return None


def _name_of(value: Any) -> str:
    """'Acme' | {'name': 'Acme'} | [{'name': 'Rome'}, ...] -> text."""
    if isinstance(value, dict):
        for k in ("name", "label", "title", "formatted", "city", "value", "addressLocality"):
            if _str(value.get(k)):
                return _str(value.get(k))
        address = value.get("address")
        return _name_of(address) if isinstance(address, dict) else ""
    if isinstance(value, list):
        names = [n for n in (_name_of(v) for v in value[:4]) if n]
        return ", ".join(dict.fromkeys(names))
    return _str(value)


def _offer_id(obj: dict[str, Any]) -> str | None:
    for k in _URL_KEYS:
        value = obj.get(k)
        if isinstance(value, str) and "job-offers/" in value:
            got = offer_key(value if value.startswith("http") else f"{DEFAULT_BASE_URL}{value}")
            if got:
                return got[0]
    for k in _ID_KEYS:
        value = _str(obj.get(k))
        if value and _ID_RE.match(value):
            return value.lower()
    return None


def offer_from_json(obj: dict[str, Any]) -> dict[str, Any] | None:
    """A JSON object that looks like a JobTeaser offer -> normalized dict, else None."""
    title = _str(_first(obj, _TITLE_KEYS))
    company = _name_of(_first(obj, _COMPANY_KEYS))
    offer_id = _offer_id(obj)
    has_offer_url = any(isinstance(obj.get(k), str) and "job-offers/" in obj[k] for k in _URL_KEYS)
    if not offer_id or not title or not (company or has_offer_url):
        return None
    href = next(
        (obj[k] for k in _URL_KEYS if isinstance(obj.get(k), str) and "job-offers/" in obj[k]),
        None,
    )
    desc = _first(obj, _DESC_KEYS)
    return {
        "id": offer_id,
        "title": title,
        "company": company,
        "location": _name_of(_first(obj, _LOCATION_KEYS)) or None,
        "contract": _name_of(_first(obj, _CONTRACT_KEYS)) or None,
        "href": href,
        "description": html_to_text(desc) if isinstance(desc, str) else "",
        "posted": _str(_first(obj, _POSTED_KEYS)) or None,
        "closing": _str(_first(obj, _CLOSING_KEYS)) or None,
    }


def walk_json(data: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Every dict in a JSON tree (depth-limited)."""
    if depth > 12:
        return
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from walk_json(value, depth + 1)
    elif isinstance(data, list):
        for value in data:
            yield from walk_json(value, depth + 1)


def offers_from_json(data: Any) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for obj in walk_json(data):
        offer = offer_from_json(obj)
        if offer is not None:
            out.setdefault(offer["id"], offer)
    return list(out.values())


def embedded_json(html: str) -> list[Any]:
    """``__NEXT_DATA__``/``application/json`` script blobs and JSON-LD blocks of a page."""
    tree = LexborHTMLParser(html)
    out: list[Any] = []
    for node in tree.css(
        'script[type="application/json"], script[type="application/ld+json"], script#__NEXT_DATA__'
    ):
        text = node.text(deep=True).strip()
        if not text:
            continue
        try:
            out.append(json.loads(text))
        except ValueError:
            continue
    return out


def offer_href_key(href: str | None, base_url: str = DEFAULT_BASE_URL) -> tuple[str, str] | None:
    """(offer id, offer URL) for an absolute or root-relative offer link, else None."""
    if not href:
        return None
    if href.startswith("/"):
        href = f"{base_url.rstrip('/')}{href}"
    return offer_key(href)


_DATE_LABEL_RE = re.compile(
    r"(scadenza|data di scadenza|candidature entro|candidati entro|closing date|apply before|"
    r"application deadline|deadline|date limite|expires?)\s*:?\s*(.{4,40})",
    re.I,
)
_IT_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
    "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}  # fmt: skip


def parse_human_date(text: str | None) -> date | None:
    """ISO, dd/mm/yyyy, dd.mm.yyyy or '15 novembre 2026' / 'November 15, 2026' -> date."""
    if not text:
        return None
    iso = parse_date(text[:10]) if re.match(r"^\d{4}-\d{2}-\d{2}", text) else None
    if iso:
        return iso
    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})\s+([a-zà]+)\s+(\d{4})\b", text, re.I) or re.search(
        r"\b([a-z]+)\s+(\d{1,2}),?\s+(\d{4})\b", text, re.I
    )
    if m:
        a, b, year = m.groups()
        day, month = (a, b) if a.isdigit() else (b, a)
        num = _IT_MONTHS.get(month.lower())
        if num:
            try:
                return date(int(year), num, int(day))
            except ValueError:
                return None
    return None


def _jsonld_posting(blobs: list[Any]) -> dict[str, Any] | None:
    for blob in blobs:
        for obj in walk_json(blob):
            kind = obj.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return obj
    return None


def _node_text(node: LexborNode | None) -> str:
    return html_to_text(node.html or "") if node is not None else ""


_DESC_SELECTORS = (
    "[data-testid*='description' i]",
    "[data-testid*='JobOfferContent' i]",
    "[class*='description' i]",
    "[class*='JobOffer' i] article",
    "article",
    "main",
)


def parse_detail(page: Page) -> dict[str, Any]:
    """Detail page -> dict(title, company, location, contract, description, posted, closing,
    closed, how). ``how`` = "jsonld" | "json" | "dom"."""
    blobs = embedded_json(page.html)
    out: dict[str, Any] = {
        "title": None,
        "company": None,
        "location": None,
        "contract": None,
        "description": "",
        "posted": None,
        "closing": None,
        "closed": is_closed_page(page),
        "how": "dom",
    }
    ld = _jsonld_posting(blobs)
    if ld is not None:
        out.update(
            title=_str(ld.get("title")) or None,
            company=_name_of(ld.get("hiringOrganization")) or None,
            location=_name_of(ld.get("jobLocation")) or None,
            contract=_name_of(ld.get("employmentType")) or None,
            description=html_to_text(ld["description"])
            if isinstance(ld.get("description"), str)
            else "",
            posted=_str(ld.get("datePosted")) or None,
            closing=_str(ld.get("validThrough")) or None,
            how="jsonld",
        )
    if not out["description"]:
        json_offers = [o for body in blobs for o in offers_from_json(body) if o["description"]]
        if json_offers:
            best = max(json_offers, key=lambda o: len(o["description"]))
            for k in ("title", "company", "location", "contract", "posted", "closing"):
                out[k] = out[k] or best[k]
            out["description"] = best["description"]
            out["how"] = "json"
    tree = LexborHTMLParser(page.html)
    for node in tree.css("script, style, noscript, template, svg, nav, header, footer"):
        node.decompose()
    if not out["description"]:
        for selector in _DESC_SELECTORS:
            text = _node_text(tree.css_first(selector))
            if len(text) > 80:
                out["description"] = text
                break
    if not out["title"]:
        h1 = tree.css_first("h1")
        out["title"] = _str(h1.text(deep=True)) if h1 is not None else None
        out["title"] = out["title"] or None
    if not out["closing"]:
        m = _DATE_LABEL_RE.search(html_to_text(page.html))
        if m and parse_human_date(m.group(2)):
            out["closing"] = m.group(2)
    return out


# --------------------------------------------------------------------------- state file


class DetailState:
    """Small local state file (ids and counts only, no content): offers whose detail page said
    closed/404, offers whose page had no readable description (not retried), and how many
    detail pages were opened today (the daily cap holds across the daily run and
    ``radar jobteaser details``)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.closed: set[str] = set()
        self.no_description: set[str] = set()
        self.day: str | None = None
        self.opened = 0
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.closed = {str(x) for x in data.get("closed_ids", [])}
                self.no_description = {str(x) for x in data.get("no_description_ids", [])}
                self.day = str(data["day"]) if data.get("day") else None
                self.opened = int(data.get("opened", 0))
            except (ValueError, OSError, AttributeError, TypeError):
                self.closed, self.no_description, self.day, self.opened = set(), set(), None, 0

    def skip(self, offer_id: str) -> bool:
        return offer_id in self.closed or offer_id in self.no_description

    def opened_on(self, day: date) -> int:
        return self.opened if self.day == day.isoformat() else 0

    def count_open(self, day: date) -> None:
        self.opened = self.opened_on(day) + 1
        self.day = day.isoformat()

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "closed_ids": sorted(self.closed)[-5000:],
            "no_description_ids": sorted(self.no_description)[-5000:],
            "day": self.day,
            "opened": self.opened,
        }
        self.path.write_text(json.dumps(data), encoding="utf-8")


# --------------------------------------------------------------------------- connector

#: A stored offer that still lacks a description: {"id", "url", "title", "company",
#: "location", "list"} ("list" = "extension" for offers saved with the browser extension,
#: "alert" for offers known only from alert emails or the seed).
Candidate = dict[str, Any]
CandidatesProvider = Callable[[bool], list[Candidate]]

#: A stored description shorter than this (chars) counts as missing (alert snippets).
MIN_DESCRIPTION = 200
#: Fixed pause between two page loads (seconds). Deliberately constant: no randomization.
PAGE_PAUSE_S = 5.0
#: Detail pages opened per day, at most.
DEFAULT_MAX_DETAILS = 40


def db_detail_candidates(include_alerts: bool = True) -> list[Candidate]:
    """Open JobTeaser offers whose description is still missing, from the configured database:
    offers saved with the extension first, then (``include_alerts``) offers known only from
    alert emails or the seed; newest first within each group."""
    from sqlmodel import Session, col, select

    from radar.config import get_settings
    from radar.db import get_engine
    from radar.models import Posting

    with Session(get_engine(get_settings().database_url)) as session:
        postings = session.exec(
            select(Posting)
            .where(col(Posting.url).contains("jobteaser.com"))
            .order_by(col(Posting.first_seen).desc())
        ).all()
        rows = [
            (p.source, p.url, p.status, p.raw_text, p.title, p.company_name, p.location)
            for p in postings
        ]
    described: set[str] = set()
    own: dict[str, Candidate] = {}
    other: dict[str, Candidate] = {}
    for source, url, status, raw_text, title, company, location in rows:
        offer_id = jobteaser_offer_id(url or "")
        if not offer_id:
            continue
        if source == SOURCE and len((raw_text or "").strip()) >= MIN_DESCRIPTION:
            described.add(offer_id)
        if status != "open":
            continue
        cand = {
            "id": offer_id,
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "list": "extension" if source == SOURCE else "alert",
        }
        (own if source == SOURCE else other).setdefault(offer_id, cand)
    out = [c for k, c in own.items() if k not in described]
    if include_alerts:
        out += [c for k, c in other.items() if k not in described and k not in own]
    return out


class JobTeaserConnector:
    """Opens only the dashboard (session check) and single offer pages, both allowed by the
    host's robots.txt, one at a time with a fixed pause. Listing pages are never opened: the
    user saves them from the browser with the "Salva in Radar" extension."""

    source = SOURCE
    label = "jobteaser (career service)"
    exhaustive = False

    def __init__(
        self,
        browser_factory: Callable[[], Browser],
        client: PoliteClient | None,
        cfg: dict[str, Any] | None = None,
        *,
        candidates: CandidatesProvider = db_detail_candidates,
        state_path: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        robots_text: str | None = None,
        today: Callable[[], date] = utc_today,
    ) -> None:
        cfg = cfg or {}
        self.browser_factory = browser_factory
        self.client = client
        self.base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.locale = str(cfg.get("locale") or DEFAULT_LOCALE)
        self.max_details = int(cfg.get("max_details", DEFAULT_MAX_DETAILS))
        self.enrich_alerts = bool(cfg.get("enrich_alerts", True))
        self.candidates = candidates
        self.state = DetailState(state_path)
        self.sleep = sleep
        self.pause = PAGE_PAUSE_S
        self.today = today
        self._robots_text = robots_text
        self._robots: RobotsRules | None = None
        self._visits = 0
        self.stats: dict[str, int] = {}

    # -- helpers
    def robots(self) -> RobotsRules:
        if self._robots is None:
            text = self._robots_text
            if text is None:
                text = ""
                if self.client is not None:
                    res = self.client.fetch(
                        f"{self.base_url}/robots.txt", ok_statuses=frozenset(range(200, 600))
                    )
                    if res.status in (401, 403) or res.status >= 500:
                        text = "User-agent: *\nDisallow: /"
                    elif res.status < 400:
                        text = res.text
            self._robots = RobotsRules.parse(text)
        return self._robots

    def _visit(self, browser: Browser, url: str, *, offer: bool = False) -> Page:
        if self._visits:
            self.sleep(self.pause)
        self._visits += 1
        page = browser.visit(url)
        check_page(page, self.base_url, offer=offer)
        return page

    def _count(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n

    def dashboard_url(self) -> str:
        return f"{self.base_url}/{self.locale}"

    def offer_url(self, offer_id: str, href: str | None = None) -> str:
        if href:
            full = href if href.startswith("http") else f"{self.base_url}{href}"
            got = offer_key(full)
            if got and got[0] == offer_id:
                return got[1]
        return f"{self.base_url}/{self.locale}/job-offers/{offer_id}"

    # -- main
    def session_ok(self, browser: Browser) -> Page:
        """Open the dashboard; raises SourcePaused if the session expired or we're blocked."""
        url = self.dashboard_url()
        if not self.robots().allowed(url):
            raise SourceError("jobteaser: robots.txt disallows the dashboard; nothing opened")
        return self._visit(browser, url)

    def pending(self) -> list[Candidate]:
        return [c for c in self.candidates(self.enrich_alerts) if not self.state.skip(c["id"])]

    def fetch(self) -> list[RawPosting]:
        self.stats = {}
        self._visits = 0
        todo = self.pending()
        day = self.today()
        budget = max(0, self.max_details - self.state.opened_on(day))
        self._count("detail_pending", len(todo))
        out: list[RawPosting] = []
        if not todo or budget == 0:
            # nothing to read (or today's cap is used up): the browser is not even started
            self._count("detail_skipped_cap", len(todo))
            self._log(out)
            return out
        robots = self.robots()
        browser = self.browser_factory()
        done = 0
        try:
            self.session_ok(browser)
            for cand in todo:
                if self.state.opened_on(day) >= self.max_details:
                    break
                done += 1
                oid = cand["id"]
                url = self.offer_url(oid, cand.get("url"))
                if not robots.allowed(url):
                    self._count("detail_robots_disallowed")
                    continue
                self.state.count_open(day)
                page = self._visit(browser, url, offer=True)
                detail = parse_detail(page)
                self._count(f"detail_{detail['how']}")
                if detail["closed"]:
                    self.state.closed.add(oid)
                    self._count("detail_closed")
                    continue
                if not detail["description"]:
                    self.state.no_description.add(oid)
                    self._count("detail_no_description")
                raw = to_raw(oid, self.offer_url(oid, page.url), cand, detail)
                if raw is not None:
                    out.append(raw)
            self._count("detail_skipped_cap", len(todo) - done)
        finally:
            self.state.save()
            browser.close()
        self._log(out)
        return out

    def _log(self, out: list[RawPosting]) -> None:
        log.info(
            "jobteaser: %d pages, %d offers returned, stats=%s",
            self._visits,
            len(out),
            json.dumps(self.stats, sort_keys=True),
        )


def to_raw(
    offer_id: str, url: str, known: dict[str, Any] | None, detail: dict[str, Any]
) -> RawPosting | None:
    """Detail page (+ what is already stored, as fallback) -> RawPosting (None without a
    title). The fallback keeps a field the detail page lacks from being blanked on upsert."""
    known = known or {}
    title = detail.get("title") or known.get("title") or ""
    if not title:
        return None
    posted = detail.get("posted")
    return RawPosting(
        source=SOURCE,
        external_id=offer_id,
        url=url,
        title=title,
        company=detail.get("company") or known.get("company") or "",
        location=normalize_location(detail.get("location") or known.get("location")),
        employment_type=detail.get("contract"),
        raw_text=detail.get("description") or "",
        posted_at=parse_datetime(posted) if posted else None,
        deadline=parse_human_date(detail.get("closing")),
        extra={
            "via": SOURCE,
            "jobteaser_host": urlsplit(url).netloc,
            "list": known.get("list", "alert"),
            "detail_fetched": True,
        },
    )


def profile_dir(settings: Settings) -> Path:
    return settings.jobteaser_profile.expanduser()


def profile_ready(settings: Settings) -> bool:
    """The "jobteaser" source exists only once `make jobteaser-login` created the profile."""
    return profile_dir(settings).is_dir()


def load_config() -> dict[str, Any]:
    """``config/jobteaser_searches.yaml`` (gitignored) or the committed example."""
    from radar.config import load_yaml_config

    try:
        return load_yaml_config("jobteaser_searches")
    except FileNotFoundError:
        return {}


def from_settings(
    client: PoliteClient, settings: Settings, cfg: dict[str, Any] | None = None
) -> JobTeaserConnector | None:
    """The connector if the browser profile exists, else None (never in CI)."""
    if not profile_ready(settings):
        return None
    profile = profile_dir(settings)

    def browser_factory() -> Browser:
        from radar.connectors.jobteaser_browser import PlaywrightBrowser

        return PlaywrightBrowser(profile)

    return JobTeaserConnector(
        browser_factory,
        client,
        cfg if cfg is not None else load_config(),
        state_path=settings.data_dir / "jobteaser_state.json",
    )


def smoke_test(conn: JobTeaserConnector, browser: Browser) -> dict[str, Any]:
    """`make jobteaser-test`: session check and one offer page still missing a description.
    Counts and parse modes only (no titles, companies or text). Listing pages are never
    opened."""
    report: dict[str, Any] = {"session": "ok"}
    conn.session_ok(browser)
    pending = conn.pending()
    report["detail_pending"] = len(pending)
    if pending:
        url = conn.offer_url(pending[0]["id"], pending[0].get("url"))
        if conn.robots().allowed(url):
            detail = parse_detail(conn._visit(browser, url, offer=True))
            report.update(
                detail_parse=detail["how"],
                detail_closed=detail["closed"],
                detail_description_chars=len(detail["description"]),
                detail_has_closing_date=parse_human_date(detail["closing"]) is not None,
                detail_has_company=bool(detail["company"]),
            )
        else:
            report["detail"] = "robots.txt disallows the detail URL: not opened"
    else:
        report["detail"] = "no offer without a description in the database"
    report["pages_opened"] = conn._visits
    return report
