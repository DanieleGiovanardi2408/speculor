"""Contact search: 2-3 PUBLIC profiles per Alta posting (Luiss alumni at the company, people in
the same or a similar role, the likely hiring manager), found with Groq's built-in browser search.

Why not ``groq/compound``: Groq decommissioned ``groq/compound`` and ``groq/compound-mini`` on
2026-09-21 (requests return ``model_not_found``). The documented replacement for web research
is the server-side ``browser_search`` tool on ``openai/gpt-oss-120b`` / ``-20b``, used here.

Guarantees (enforced in code, tested):

* Public search results only. This module never logs into anything, never runs the user's
  ``linkedin`` CLI or any subprocess, never fetches pages itself: the only network call is the
  Groq chat completion. Contacts are built ONLY from the URLs and titles returned by the search
  tool (never from opened pages); the prompt forbids opening LinkedIn or login-walled pages and
  any attempt is counted in the stats (``login_page_opens``).
* No invented people: the model's answer is checked against the executed searches. A contact is
  kept only if its URL (or, when the model mangled the slug, a search result whose title is the
  same name) appears in the search results; the URL must be https, a ``linkedin.com/in/`` profile
  or another public page that is not a search engine/proxy, and its slug must plausibly match
  the name. Everything stored is marked ``needs_review`` ("da verificare" in the UI) with the
  search query that found it.
* Only name, title, public URL and a one-line reason are stored (``Contact`` model).
* Budget: an estimated cost per call (tokens + tool calls, from Groq's published prices) is
  stored in ``llm_usage.est_cost_eur``. A call is refused when the month's estimate plus a
  worst-case reserve would exceed ``RADAR_CONTACTS_MONTHLY_CAP_EUR`` (default 5) or when
  ``RADAR_CONTACTS_DAILY_CALL_CAP`` calls were made today (UTC).

Logging is counts-only.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal, Protocol
from urllib.parse import unquote, urlsplit

from sqlmodel import Session, col, select

from radar.config import get_settings
from radar.db import get_setting, set_setting
from radar.llm.provider import (
    GroqProvider,
    LLMError,
    LLMResult,
    MissingKeyError,
    RateLimitExhausted,
    estimate_tokens,
    record_usage,
)
from radar.llm.scoring import extract_json
from radar.models import ApplicationPack, Contact, LLMUsage, Posting, Score
from radar.timeutil import today, utcnow

log = logging.getLogger(__name__)

MAX_CONTACTS = 3
SEARCH_MAX_TOKENS = 2000
RESEARCH_AFTER_DAYS = 14  # an Alta posting searched without results is retried after this
SEARCHED_SETTING = "contacts:searched"  # {posting_id: "YYYY-MM-DD"} of the last attempt
CANDIDATE_FILTER_STATES = ("pass", "flagged", "overridden")

# --------------------------------------------------------------------------- cost model
# Groq published prices (USD), checked 2026-10: tokens per million, built-in tools per call.
# Prompt-cached input tokens are billed at 50%. Unknown models use the most expensive row.
TOKEN_PRICES_USD_PER_M: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
}
TOOL_PRICES_USD: dict[str, float] = {
    "search": 5.0 / 1000,  # browser_search "browser.search" (basic search)
    "open": 1.0 / 1000,  # "browser.open" (visit website)
}
CACHED_INPUT_DISCOUNT = 0.5
USD_TO_EUR = 1.0  # conservative: a dollar is counted as a euro
# Worst case for one call (observed: ~7.6K tokens, 2 searches, 4 opens), reserved before
# calling so a single call can never push the month over the cap.
RESERVE_INPUT_TOKENS = 15000
RESERVE_OUTPUT_TOKENS = SEARCH_MAX_TOKENS
RESERVE_SEARCHES = 6
RESERVE_OPENS = 6


def estimate_cost_eur(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_tokens: int = 0,
    searches: int = 0,
    opens: int = 0,
) -> float:
    price_in, price_out = TOKEN_PRICES_USD_PER_M.get(
        model, max(TOKEN_PRICES_USD_PER_M.values(), key=lambda p: p[0] + p[1])
    )
    cached = min(max(cached_tokens, 0), input_tokens)
    usd = (
        (input_tokens - cached) * price_in
        + cached * price_in * CACHED_INPUT_DISCOUNT
        + output_tokens * price_out
    ) / 1_000_000
    usd += searches * TOOL_PRICES_USD["search"] + opens * TOOL_PRICES_USD["open"]
    return usd * USD_TO_EUR


def call_reserve_eur(model: str) -> float:
    return estimate_cost_eur(
        model,
        RESERVE_INPUT_TOKENS,
        RESERVE_OUTPUT_TOKENS,
        searches=RESERVE_SEARCHES,
        opens=RESERVE_OPENS,
    )


class BudgetExceeded(RuntimeError):
    """The monthly cost cap or the daily call cap would be exceeded: no call is made."""

    def __init__(self, reason: Literal["monthly_cap", "daily_cap"], message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Budget:
    month_cost_eur: float
    monthly_cap_eur: float
    calls_today: int
    daily_call_cap: int
    reserve_eur: float

    @property
    def remaining_eur(self) -> float:
        return max(0.0, self.monthly_cap_eur - self.month_cost_eur)

    def check(self) -> None:
        if self.calls_today >= self.daily_call_cap:
            raise BudgetExceeded(
                "daily_cap",
                f"daily contact-search cap reached ({self.calls_today}/{self.daily_call_cap})",
            )
        if self.month_cost_eur + self.reserve_eur > self.monthly_cap_eur:
            raise BudgetExceeded(
                "monthly_cap",
                f"monthly contact-search cap would be exceeded "
                f"({self.month_cost_eur:.3f} + {self.reserve_eur:.3f} > "
                f"{self.monthly_cap_eur:.2f} EUR)",
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "month_cost_eur": round(self.month_cost_eur, 4),
            "monthly_cap_eur": self.monthly_cap_eur,
            "calls_today": self.calls_today,
            "daily_call_cap": self.daily_call_cap,
        }


def budget_status(session: Session) -> Budget:
    """Usage of purpose "contacts" this UTC month (cost) and today (calls)."""
    settings = get_settings()
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    rows = session.exec(
        select(LLMUsage).where(LLMUsage.purpose == "contacts", col(LLMUsage.at) >= month_start)
    ).all()
    month_cost = sum(r.est_cost_eur for r in rows)
    calls_today = 0
    for r in rows:
        at = r.at if r.at.tzinfo else r.at.replace(tzinfo=now.tzinfo)
        if at >= day_start:
            calls_today += r.requests
    return Budget(
        month_cost_eur=month_cost,
        monthly_cap_eur=settings.contacts_monthly_cap_eur,
        calls_today=calls_today,
        daily_call_cap=settings.contacts_daily_call_cap,
        reserve_eur=call_reserve_eur(settings.contacts_model),
    )


# --------------------------------------------------------------------------- provider


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str


@dataclass(frozen=True)
class ToolCall:
    kind: Literal["search", "open", "other"]
    query: str | None = None  # search query
    url: str | None = None  # opened page
    hits: tuple[SearchHit, ...] = ()  # search results (only for kind == "search")


@dataclass(frozen=True)
class SearchResult(LLMResult):
    cached_tokens: int = 0
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)


class SearchProvider(Protocol):
    name: str
    model: str

    def search(self, system: str, user: str) -> SearchResult: ...


def _tool_kind(name: str, type_: str) -> Literal["search", "open", "other"]:
    """``browser.search`` / ``browser.open`` (GPT-OSS browser_search); ``search`` / ``visit``
    (Compound-style executed_tools)."""
    name, type_ = name.lower(), type_.lower()
    if "search" in name:
        return "search"
    if any(w in name or w in type_ for w in ("open", "visit")):
        return "open"
    return "search" if "search" in type_ else "other"


def _parse_tool(raw: dict[str, Any]) -> ToolCall:
    name = str(raw.get("name") or "")
    kind = _tool_kind(name, str(raw.get("type") or ""))
    args: dict[str, Any] = {}
    raw_args = raw.get("arguments")
    if isinstance(raw_args, str):
        try:
            loaded = json.loads(raw_args)
        except ValueError:
            loaded = {}
        args = loaded if isinstance(loaded, dict) else {}
    elif isinstance(raw_args, dict):
        args = raw_args
    if kind == "search":
        results = (raw.get("search_results") or {}).get("results") or []
        hits = tuple(
            SearchHit(url=str(r.get("url") or ""), title=str(r.get("title") or ""))
            for r in results
            if isinstance(r, dict) and r.get("url")
        )
        query = args.get("query")
        return ToolCall("search", query=str(query) if query else None, hits=hits)
    if kind == "open":
        target = args.get("id") or args.get("url")
        url = target if isinstance(target, str) and target.startswith("http") else None
        if url is None:
            m = re.search(r"https?://[^\s)\]>]+", str(raw.get("output") or ""))
            url = m.group(0) if m else None
        return ToolCall("open", url=url)
    return ToolCall("other")


class GroqSearchProvider(GroqProvider):
    """Groq chat completion with the built-in, server-side ``browser_search`` tool."""

    def _body(
        self,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> dict[str, Any]:
        body = super()._body(system, user, model, max_tokens, temperature, False)
        body["tools"] = [{"type": "browser_search"}]
        body["tool_choice"] = "required"
        body.pop("include_reasoning", None)
        return body

    def _parse(self, data: dict[str, Any], model: str) -> LLMResult:
        base = super()._parse(data, model)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("groq: unexpected response shape") from exc
        tools = tuple(
            _parse_tool(t) for t in (message.get("executed_tools") or []) if isinstance(t, dict)
        )
        usage = data.get("usage") or {}
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        return SearchResult(
            text=base.text,
            input_tokens=base.input_tokens,
            output_tokens=base.output_tokens,
            model=base.model,
            provider=base.provider,
            cached_tokens=cached,
            tool_calls=tools,
        )

    def search(self, system: str, user: str) -> SearchResult:
        result = self.complete(
            system,
            user,
            model=self.model,
            max_tokens=SEARCH_MAX_TOKENS,
            temperature=0.2,
            json_mode=False,  # structured outputs are not supported with browser_search
        )
        if not isinstance(result, SearchResult):  # a 400 json_validate_failed fallback
            return SearchResult(
                result.text, result.input_tokens, result.output_tokens, result.model, self.name
            )
        return result


class FakeSearchProvider:
    """Offline provider for demo mode and tests: fictional people on example.com pages, with
    matching search results so they pass the grounding checks."""

    name = "fake"

    def __init__(self, model: str = "fake-search") -> None:
        self.model = model
        self.calls = 0

    def search(self, system: str, user: str) -> SearchResult:
        self.calls += 1
        company = _field(user, "Company") or "Acme"
        slug = re.sub(r"[^a-z0-9]+", "-", _ascii(company).lower()).strip("-") or "acme"
        people = [
            ("Giulia Esempio", "Data Scientist", "alumni", f'"Luiss" "{company}"'),
            ("Marco Fittizio", "Analytics Lead", "hiring_manager", f'"{company}" manager'),
        ]
        hits = []
        contacts = []
        calls = []
        for name, title, category, query in people:
            url = f"https://example.com/{slug}/people/{name.lower().replace(' ', '-')}"
            hits.append(SearchHit(url, name))
            calls.append(ToolCall("search", query=query, hits=(SearchHit(url, name),)))
            contacts.append(
                {
                    "name": name,
                    "title": f"{title} at {company}",
                    "url": url,
                    "category": category,
                    "reason": "Profilo fittizio per la demo.",
                    "query": query,
                }
            )
        text = json.dumps({"contacts": contacts})
        return SearchResult(
            text=text,
            input_tokens=estimate_tokens(system + user),
            output_tokens=estimate_tokens(text),
            model=self.model,
            provider=self.name,
            tool_calls=tuple(calls),
        )


def get_search_provider() -> SearchProvider:
    settings = get_settings()
    if settings.effective_contacts_provider == "fake":
        return FakeSearchProvider()
    if not settings.groq_api_key:
        raise MissingKeyError("GROQ_API_KEY is not set")
    return GroqSearchProvider(settings.groq_api_key, settings.contacts_model)


# --------------------------------------------------------------------------- prompt

CATEGORY_LABEL = {
    "alumni": "Alumni Luiss",
    "same_role": "Ruolo simile",
    "hiring_manager": "Possibile responsabile",
    "other": "Lavora in azienda",
}

SYSTEM = """You find PUBLIC professional profiles to help a student network before applying.
Use the browser_search tool. Rules:
- Use only search results. Do NOT open linkedin.com pages, pages that need a login, or any
  proxy, cache or reader of such pages. Never log in anywhere.
- Never invent people, titles or URLs. Copy each profile URL exactly as it appears in the search
  results. If you find fewer than 3 suitable people, return fewer; if none, return an empty list.
- Only people who appear to work at the company now.
Respond with JSON only, no prose, no code fences:
{"contacts": [{"name": "...", "title": "...", "url": "https://...",
"category": "alumni|same_role|hiring_manager", "reason": "one line, at most 20 words",
"query": "the search query that found this person"}]}"""


def _role_keywords(title: str) -> str:
    t = re.sub(
        r"\b(intern(ship)?|stage|stagiaire|tirocinio|trainee|working student)\b",
        "",
        title,
        flags=re.I,
    )
    t = re.sub(r"[\(\)\[\]|/,:–—-]+", " ", t)
    return " ".join(t.split()[:4]) or title


def search_user_prompt(posting: Posting) -> str:
    company = posting.company_name
    role = _role_keywords(posting.title)
    area = posting.area or role
    return (
        f"Company: {company}\n"
        f"Role: {posting.title}\n"
        f"Location: {posting.location or ''}\n\n"
        "Run these searches (at most 4 in total):\n"
        f'1. "Luiss" "{company}" site:linkedin.com/in\n'
        f'2. "{company}" "{role}" site:linkedin.com/in\n'
        f'3. "{company}" (head OR manager OR lead) "{area}" site:linkedin.com/in\n'
        f"Return up to {MAX_CONTACTS} people: preferably one Luiss alumnus or alumna at "
        f"{company}, one person in the same or a similar role, and the likely hiring manager."
    )


# --------------------------------------------------------------------------- validation


@dataclass(frozen=True)
class RawContact:
    name: str
    title: str
    url: str
    category: str
    reason: str
    query: str


def parse_contacts(text: str) -> list[RawContact]:
    """Lenient parse of the model's JSON. Raises ValueError when there is no JSON at all."""
    t = text.strip()
    data: Any
    if t.startswith("["):
        try:
            data = {"contacts": json.loads(t)}
        except ValueError:
            data = extract_json(t)
    else:
        data = extract_json(t)
    items = data.get("contacts")
    if not isinstance(items, list):
        raise ValueError("missing 'contacts' list")
    out: list[RawContact] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        def s(key: str, d: dict[str, Any] = item) -> str:
            v = d.get(key)
            return " ".join(str(v).split()) if isinstance(v, str | int | float) else ""

        out.append(
            RawContact(
                name=s("name"),
                title=s("title") or s("role"),
                url=s("url") or s("profile_url"),
                category=s("category").lower() or "other",
                reason=s("reason") or s("why"),
                query=s("query"),
            )
        )
    return out


_BLOCKED_HOSTS = (
    "google.",
    "bing.com",
    "duckduckgo.com",
    "exa.ai",
    "jina.ai",
    "webcache.",
    "archive.org",
    "archive.ph",
    "translate.goog",
)
_LINKEDIN_PROFILE = re.compile(r"^/in/([^/?#]+)/?", re.I)


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _name_tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _ascii(name).lower()) if len(t) >= 2]


def normalize_profile_url(url: str) -> str | None:
    """Canonical https URL if ``url`` looks like a public person page, else None.

    LinkedIn: only ``/in/<slug>`` profiles (jobs, company, posts pages are not people), returned
    as ``https://www.linkedin.com/in/<slug>``. Other hosts: https, a non-empty path, and not a
    search engine, proxy, cache or reader.
    """
    url = url.strip()
    if not url.lower().startswith("https://"):
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host or "." not in host or parts.username or parts.password:
        return None
    if any(b in host for b in _BLOCKED_HOSTS):
        return None
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        m = _LINKEDIN_PROFILE.match(parts.path)
        if not m:
            return None
        slug = unquote(m.group(1)).strip().lower()
        if not slug or not re.fullmatch(r"[\w\-.%]+", slug):
            return None
        return f"https://www.linkedin.com/in/{slug}"
    path = parts.path.rstrip("/")
    if not path or path == "/" or "linkedin" in path.lower():
        return None
    return f"https://{host}{path}"


def url_slug(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    if "/in/" in path:
        return path.split("/in/", 1)[1]
    return path.rsplit("/", 1)[-1]


def slug_matches_name(slug: str, name: str) -> bool:
    """Deterministic plausibility check: a name token of 3+ letters appears in the URL slug
    (``mferri`` / ``marta-ferri-123`` both match "Marta Ferri")."""
    s = re.sub(r"[^a-z0-9]", "", _ascii(unquote(slug)).lower())
    tokens = [t for t in _name_tokens(name) if len(t) >= 3]
    return bool(s and tokens and any(t in s for t in tokens))


def _title_is_name(title: str, name: str) -> bool:
    """A search hit titled with this person's name ("Marta Ferri - Acme | LinkedIn")."""
    want = _name_tokens(name)
    head = re.split(r"\s[-|–—·]\s|\|", title, maxsplit=1)[0]
    have = _name_tokens(head)
    return bool(want) and len(want) >= 2 and set(want) <= set(have)


@dataclass
class Accepted:
    name: str
    title: str
    url: str
    category: str
    reason: str
    query: str
    corrected: bool = False


@dataclass
class Review:
    accepted: list[Accepted] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    corrected: int = 0

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1


def _hit_index(tool_calls: Iterable[ToolCall]) -> dict[str, tuple[SearchHit, str]]:
    """Normalized URL -> (hit, query) for every search result (first query wins)."""
    index: dict[str, tuple[SearchHit, str]] = {}
    for call in tool_calls:
        if call.kind != "search":
            continue
        for hit in call.hits:
            norm = normalize_profile_url(hit.url)
            if norm and norm not in index:
                index[norm] = (hit, call.query or "")
    return index


def review_contacts(raw: list[RawContact], tool_calls: Iterable[ToolCall]) -> Review:
    """Keep only well-formed contacts grounded in the executed searches (see module doc)."""
    review = Review()
    index = _hit_index(tool_calls)
    seen: set[str] = set()
    for c in raw:
        name = c.name.strip()
        if len(_name_tokens(name)) < 2:
            review.drop("no_name")
            continue
        if not c.url:
            review.drop("no_url")
            continue
        url = normalize_profile_url(c.url)
        if url is None:
            review.drop("not_a_profile")
            continue
        query = ""
        corrected = False
        if url in index:
            query = index[url][1]
        else:
            # The model often rewrites slugs ("mferri" -> "marta-ferri"): use the
            # search hit with the same name instead, or drop the person.
            match = next(
                (u for u, (hit, _q) in index.items() if _title_is_name(hit.title, name)), None
            )
            if match is None:
                review.drop("not_in_search_results")
                continue
            url, query, corrected = match, index[match][1], True
        if not slug_matches_name(url_slug(url), name):
            review.drop("slug_mismatch")
            continue
        if url in seen:
            review.drop("duplicate")
            continue
        seen.add(url)
        category = c.category if c.category in CATEGORY_LABEL else "other"
        if category == "alumni" and "luiss" not in query.lower():
            category = "other"  # an alumni claim needs a search that mentioned Luiss
        review.corrected += int(corrected)
        review.accepted.append(
            Accepted(
                name=name[:120],
                title=c.title[:160],
                url=url,
                category=category,
                reason=c.reason[:200],
                query=(query or c.query)[:300],
                corrected=corrected,
            )
        )
    return review


def _login_page_opens(tool_calls: Iterable[ToolCall]) -> int:
    return sum(
        1
        for t in tool_calls
        if t.kind == "open" and t.url is not None and "linkedin.com" in t.url.lower()
    )


# --------------------------------------------------------------------------- storage


def _norm_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _ascii(name).lower())


def company_contacts(session: Session, posting: Posting) -> list[Contact]:
    """Contacts already stored for the posting's company (any posting of it)."""
    stmt = select(Contact, Posting).join(Posting, col(Contact.posting_id) == col(Posting.id))
    key = _norm_company(posting.company_name)
    out: list[Contact] = []
    for contact, p in session.exec(stmt).all():
        same = (
            posting.company_id is not None and contact.company_id == posting.company_id
        ) or _norm_company(p.company_name) == key
        if same:
            out.append(contact)
    return out


def _searched(session: Session) -> dict[str, str]:
    value = get_setting(SEARCHED_SETTING, {}, session=session)
    return dict(value) if isinstance(value, dict) else {}


def _mark_searched(session: Session, posting_id: int) -> None:
    searched = _searched(session)
    cutoff = (today() - timedelta(days=60)).isoformat()
    searched = {k: v for k, v in searched.items() if v >= cutoff}
    searched[str(posting_id)] = today().isoformat()
    set_setting(session, SEARCHED_SETTING, searched)


def _record(session: Session, run_id: int | None, result: SearchResult) -> float:
    searches = sum(1 for t in result.tool_calls if t.kind == "search")
    opens = sum(1 for t in result.tool_calls if t.kind == "open")
    cost = (
        0.0
        if result.provider == FakeSearchProvider.name
        else estimate_cost_eur(
            result.model,
            result.input_tokens,
            result.output_tokens,
            cached_tokens=result.cached_tokens,
            searches=searches,
            opens=opens,
        )
    )
    row = record_usage(session, run_id, "contacts", result)
    row.tool_calls += len(result.tool_calls)
    row.est_cost_eur += cost
    session.add(row)
    session.flush()
    return cost


def find_contacts(
    session: Session,
    posting_id: int,
    *,
    run_id: int | None = None,
    provider: SearchProvider | None = None,
) -> dict[str, Any]:
    """Search public profiles for one posting and store up to 3 contacts (needs_review).

    Raises ValueError (unknown posting), BudgetExceeded, MissingKeyError, RateLimitExhausted,
    LLMError. Commits usage and contacts.
    """
    posting = session.get(Posting, posting_id)
    if posting is None:
        raise ValueError(f"posting {posting_id} not found")
    budget = budget_status(session)
    budget.check()
    provider = provider or get_search_provider()

    try:
        result = provider.search(SYSTEM, search_user_prompt(posting))
    except RateLimitExhausted:
        raise
    except LLMError:
        # Count the failed attempt toward the daily call cap (no tokens, no cost known).
        record_usage(
            session, run_id, "contacts", LLMResult("", 0, 0, provider.model, provider.name)
        )
        session.commit()
        raise
    cost = _record(session, run_id, result)
    _mark_searched(session, posting_id)

    stats: dict[str, Any] = {
        "searches": sum(1 for t in result.tool_calls if t.kind == "search"),
        "page_opens": sum(1 for t in result.tool_calls if t.kind == "open"),
        "login_page_opens": _login_page_opens(result.tool_calls),
        "est_cost_eur": round(cost, 5),
        "found": 0,
        "stored": 0,
    }
    try:
        raw = parse_contacts(result.text)
    except ValueError:
        stats["invalid_output"] = True
        session.commit()
        return stats
    review = review_contacts(raw, result.tool_calls)
    stats["found"] = len(raw)
    stats["dropped"] = review.dropped
    stats["url_corrected"] = review.corrected

    existing = company_contacts(session, posting)
    known_urls = {normalize_profile_url(c.profile_url or "") for c in existing} - {None}
    known_names = {" ".join(_name_tokens(c.name)) for c in existing}
    for a in review.accepted[:MAX_CONTACTS]:
        if a.url in known_urls or " ".join(_name_tokens(a.name)) in known_names:
            stats["duplicates"] = stats.get("duplicates", 0) + 1
            continue
        session.add(
            Contact(
                posting_id=posting.id,
                company_id=posting.company_id,
                name=a.name,
                role=a.title or None,
                profile_url=a.url,
                why=f"{CATEGORY_LABEL[a.category]}: {a.reason}" if a.reason else None,
                source="search",
                source_query=a.query or None,
                needs_review=True,
            )
        )
        known_urls.add(a.url)
        stats["stored"] += 1
    if stats["login_page_opens"]:
        log.warning(
            "contacts: search tool tried %d login-walled page(s)", stats["login_page_opens"]
        )
    session.commit()
    return stats


# --------------------------------------------------------------------------- daily batch


def _latest_priority(session: Session, posting_ids: list[int]) -> dict[int, str]:
    latest: dict[int, str] = {}
    if not posting_ids:
        return latest
    for s in session.exec(
        select(Score)
        .where(col(Score.posting_id).in_(posting_ids))
        .order_by(col(Score.created_at), col(Score.id))
    ).all():
        latest[s.posting_id] = s.priority
    return latest


def contact_candidates(session: Session, limit: int) -> list[Posting]:
    """Open Alta postings with a pack, no contacts yet and no search in the last 14 days."""
    if limit <= 0:
        return []
    postings = list(
        session.exec(
            select(Posting)
            .join(ApplicationPack, col(ApplicationPack.posting_id) == col(Posting.id))
            .where(
                Posting.status == "open",
                col(Posting.filter_state).in_(CANDIDATE_FILTER_STATES),
                col(Posting.duplicate_of).is_(None),
                Posting.user_status == "todo",
            )
        ).all()
    )
    ids = [p.id for p in postings if p.id is not None]
    priority = _latest_priority(session, ids)
    with_contacts = (
        set(session.exec(select(Contact.posting_id).where(col(Contact.posting_id).in_(ids))).all())
        if ids
        else set()
    )
    searched = _searched(session)
    retry_before = (today() - timedelta(days=RESEARCH_AFTER_DAYS)).isoformat()
    now = today()
    out = [
        p
        for p in postings
        if p.id is not None
        and priority.get(p.id) == "Alta"
        and p.id not in with_contacts
        and searched.get(str(p.id), "") < retry_before
        and (p.deadline is None or p.deadline >= now)
    ]
    out.sort(key=lambda p: (p.deadline is None, p.deadline or date.max, -p.first_seen.timestamp()))
    return out[:limit]


def searched_today(session: Session) -> int:
    day = today().isoformat()
    return sum(1 for v in _searched(session).values() if v == day)


def find_contacts_for_alta(
    session: Session,
    *,
    run_id: int | None = None,
    limit: int | None = None,
    provider: SearchProvider | None = None,
) -> dict[str, Any]:
    """Daily batch: at most ``RADAR_CONTACTS_DAILY_POSTINGS`` postings per day (minus those
    already searched today), stopping cleanly on a budget cap, rate limit or missing key."""
    settings = get_settings()
    per_day = settings.contacts_daily_postings if limit is None else limit
    room = max(0, per_day - searched_today(session)) if limit is None else per_day
    postings = contact_candidates(session, room)
    stats: dict[str, Any] = {"candidates": len(postings), "searched": 0, "stored": 0, "errors": 0}
    if not postings:
        return stats
    if provider is None:
        try:
            provider = get_search_provider()
        except MissingKeyError:
            stats["skipped"] = "missing_key"
            return stats
    for posting in postings:
        assert posting.id is not None
        try:
            one = find_contacts(session, posting.id, run_id=run_id, provider=provider)
        except BudgetExceeded as exc:
            stats["stopped"] = exc.reason
            break
        except RateLimitExhausted:
            stats["stopped"] = "rate_limit"
            break
        except LLMError:
            stats["errors"] += 1
            continue
        stats["searched"] += 1
        stats["stored"] += int(one.get("stored", 0))
        for key in ("login_page_opens", "url_corrected"):
            if one.get(key):
                stats[key] = stats.get(key, 0) + int(one[key])
    stats["budget"] = budget_status(session).as_dict()
    log.info(
        "contacts: %d candidates, %d searched, %d stored%s",
        stats["candidates"],
        stats["searched"],
        stats["stored"],
        f", stopped ({stats['stopped']})" if "stopped" in stats else "",
    )
    return stats


def _field(text: str, label: str) -> str:
    m = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""
