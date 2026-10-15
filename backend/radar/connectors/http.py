"""Polite HTTP client shared by all connectors.

Rules enforced here (see docs/SOURCES.md):

* honest identification: every request carries ``settings.user_agent``; no cookies, no proxies;
* per-host rate limiting (minimum interval between two requests to the same host);
* on-disk response cache keyed by (url, params) with a TTL;
* back-off on 429/403/999/5xx honouring ``Retry-After``; after ``max_retries`` -> SourceError;
* ``robots_allowed(url)`` with a per-host cache; ``get_text`` checks it by default (HTML pages).

Logs are counts-only: never log company names or posting titles.
"""

from __future__ import annotations

import email.utils
import hashlib
import html
import json
import logging
import re
import time
import urllib.robotparser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlencode, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser

from radar.connectors.base import SourceError

log = logging.getLogger(__name__)
# httpx logs every request URL at INFO; URLs carry board slugs (company names), which would
# break the counts-only logging rule.
logging.getLogger("httpx").setLevel(logging.WARNING)

DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_HOST_INTERVALS: dict[str, float] = {
    "linkedin.com": 5.0,
    "api.smartrecruiters.com": 1.0,
    "api.lever.co": 1.0,  # robots.txt: Crawl-delay: 1
    "api.eu.lever.co": 1.0,
}
RETRY_STATUSES = frozenset({403, 429, 999, 500, 502, 503, 504})
_DISALLOW_ALL = ["User-agent: *", "Disallow: /"]
Params = Mapping[str, str | int | float | bool | None] | None


@dataclass(frozen=True)
class FetchResult:
    status: int
    text: str
    url: str
    from_cache: bool = False

    def json(self) -> Any:
        return json.loads(self.text)


class RobotsDisallowed(SourceError):
    """robots.txt disallows the URL for our user agent."""


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _clean_params(params: Params) -> list[tuple[str, str]]:
    if not params:
        return []
    out: list[tuple[str, str]] = []
    for k, v in sorted(params.items()):
        if v is None:
            continue
        out.append((k, str(v).lower() if isinstance(v, bool) else str(v)))
    return out


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


class PoliteClient:
    """Rate-limited, caching, robots-aware wrapper around ``httpx.Client``."""

    def __init__(
        self,
        user_agent: str,
        cache_dir: Path | str,
        cache_ttl_hours: float = 12.0,
        min_interval_per_host: Mapping[str, float] | None = None,
        *,
        default_interval: float = DEFAULT_MIN_INTERVAL,
        max_retries: int = 3,
        backoff_base: float = 5.0,
        max_backoff: float = 120.0,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("user_agent is required (honest identification)")
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.cache_ttl_seconds = cache_ttl_hours * 3600.0
        self.intervals: dict[str, float] = {
            **DEFAULT_HOST_INTERVALS,
            **(min_interval_per_host or {}),
        }
        self.default_interval = default_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_backoff = max_backoff
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.network_requests = 0  # counter, handy for tests and run stats
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "en,it;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def from_settings(cls, **overrides: Any) -> PoliteClient:
        from radar.config import get_settings

        s = get_settings()
        kwargs: dict[str, Any] = {
            "user_agent": s.user_agent,
            "cache_dir": s.cache_dir / "http",
            "cache_ttl_hours": s.http_cache_ttl_hours,
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------ public API
    def get_json(
        self,
        url: str,
        params: Params = None,
        *,
        use_cache: bool = True,
        respect_robots: bool = False,
    ) -> Any:
        res = self.fetch(url, params, use_cache=use_cache, respect_robots=respect_robots)
        try:
            return res.json()
        except ValueError as exc:
            raise SourceError(f"{_host(url)}: response is not JSON") from exc

    def get_text(
        self,
        url: str,
        params: Params = None,
        *,
        use_cache: bool = True,
        respect_robots: bool = True,
    ) -> str:
        return self.fetch(url, params, use_cache=use_cache, respect_robots=respect_robots).text

    def fetch(
        self,
        url: str,
        params: Params = None,
        *,
        use_cache: bool = True,
        respect_robots: bool = False,
        ok_statuses: frozenset[int] | set[int] = frozenset({200}),
    ) -> FetchResult:
        """GET ``url``. Returns statuses in ``ok_statuses``; anything else raises SourceError."""
        clean = _clean_params(params)
        full_url = f"{url}?{urlencode(clean)}" if clean else url
        if respect_robots and not self.robots_allowed(full_url):
            raise RobotsDisallowed(
                f"{_host(url)}: robots.txt disallows this path for our user agent"
            )
        key = self._cache_key(full_url)
        if use_cache:
            cached = self._cache_read(key)
            if cached is not None and cached.status in ok_statuses:
                return cached
        res = self._request(full_url, ok_statuses)
        if res.status in ok_statuses and res.status in (200, 404, 410):
            self._cache_write(key, res)
        return res

    def robots_allowed(self, url: str) -> bool:
        """robots.txt check for our user agent; cached per host. Unreachable (5xx) -> disallow."""
        parts = urlsplit(url)
        host = parts.netloc.lower()
        parser = self._robots.get(host)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{parts.scheme or 'https'}://{host}/robots.txt"
            try:
                res = self.fetch(robots_url, ok_statuses=frozenset(range(200, 600)))
            except SourceError:
                parser.parse(_DISALLOW_ALL)
            else:
                if res.status in (401, 403) or res.status >= 500:
                    parser.parse(_DISALLOW_ALL)
                elif res.status >= 400:
                    parser.parse([])  # no robots.txt: everything allowed
                else:
                    parser.parse(res.text.splitlines())
            self._robots[host] = parser
        return parser.can_fetch(self._robots_agent(), url)

    # ------------------------------------------------------------------ internals
    def _robots_agent(self) -> str:
        return self.user_agent.split("/", 1)[0].split()[0]

    def _interval_for(self, host: str) -> float:
        best: tuple[int, float] | None = None
        for suffix, value in self.intervals.items():
            if (host == suffix or host.endswith("." + suffix)) and (
                best is None or len(suffix) > best[0]
            ):
                best = (len(suffix), value)
        return best[1] if best else self.default_interval

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self._interval_for(host) - (time.monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = time.monotonic()

    def _request(self, url: str, ok_statuses: frozenset[int] | set[int]) -> FetchResult:
        host = _host(url)
        attempt = 0
        while True:
            self._throttle(host)
            self.network_requests += 1
            try:
                resp = self._client.get(url)
            except httpx.HTTPError as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise SourceError(f"{host}: network error ({type(exc).__name__})") from exc
                self._sleep(min(self.max_backoff, self.backoff_base * 2 ** (attempt - 1)))
                continue
            status = resp.status_code
            if status in ok_statuses:
                return FetchResult(status=status, text=resp.text, url=str(resp.url))
            if status in RETRY_STATUSES:
                attempt += 1
                if attempt > self.max_retries:
                    raise SourceError(f"{host}: HTTP {status} after {self.max_retries} retries")
                wait = _retry_after_seconds(resp.headers.get("Retry-After"))
                if wait is None:
                    wait = self.backoff_base * 2 ** (attempt - 1)
                if wait > self.max_backoff:
                    raise SourceError(f"{host}: HTTP {status}, Retry-After too long ({wait:.0f}s)")
                log.warning(
                    "http: status %s, backing off %.0fs (attempt %d)", status, wait, attempt
                )
                self._sleep(wait)
                continue
            raise SourceError(f"{host}: HTTP {status}")

    def _cache_key(self, full_url: str) -> str:
        return hashlib.sha256(full_url.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def _cache_read(self, key: str) -> FetchResult | None:
        path = self._cache_path(key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if time.time() - float(data.get("fetched_at", 0)) > self.cache_ttl_seconds:
            return None
        return FetchResult(
            status=int(data["status"]),
            text=str(data["text"]),
            url=str(data["url"]),
            from_cache=True,
        )

    def _cache_write(self, key: str, res: FetchResult) -> None:
        path = self._cache_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "fetched_at": time.time(),
                        "status": res.status,
                        "url": res.url,
                        "text": res.text,
                    }
                ),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            log.warning("http: could not write cache entry")


# ---------------------------------------------------------------------- text helpers
_BLOCK_TAGS = (
    "p|div|br|li|ul|ol|h[1-6]|tr|table|section|article|header|footer|blockquote|pre|hr|dd|dt"
)
_BLOCK_RE = re.compile(rf"<\s*/?\s*(?:{_BLOCK_TAGS})\b[^>]*>", re.I)
_LI_RE = re.compile(r"<\s*li\b[^>]*>", re.I)


def html_to_text(fragment: str | None, *, unescape_first: bool = False) -> str:
    """Convert an HTML fragment to readable plain text (paragraphs and bullets preserved)."""
    if not fragment:
        return ""
    if unescape_first:  # Greenhouse returns HTML-escaped HTML
        fragment = html.unescape(fragment)
    fragment = _LI_RE.sub(lambda m: m.group(0) + "\n- ", fragment)
    fragment = _BLOCK_RE.sub(lambda m: "\n" + m.group(0) + "\n", fragment)
    tree = LexborHTMLParser(f"<html><body>{fragment}</body></html>")
    for node in tree.css("script, style, noscript, template"):
        node.decompose()
    body = tree.body
    raw = body.text(deep=True, separator="") if body is not None else ""
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in raw.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln in ("", "-"):
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(ln)
    return "\n".join(out).strip()


DEFAULT_TITLE_FILTER = (
    "intern",
    "internship",
    "stage",
    "stagiaire",
    "tirocinio",
    "graduate",
    "junior",
    "trainee",
    "thesis",
    "werkstudent",
    "working student",
    "entry level",
    "new grad",
    "apprentice",
    "analyst",
)


def title_matches(title: str, keywords: list[str] | tuple[str, ...] | None) -> bool:
    """Case-insensitive word-prefix match: 'intern' matches 'Intern', 'Internship', 'interns'."""
    if not keywords:
        return True
    t = title.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(k.lower().strip())}", t) for k in keywords)


def parse_datetime(value: Any) -> datetime | None:
    """ISO-8601 string or epoch (s or ms) -> aware datetime (UTC if no offset)."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, int | float):
            seconds = value / 1000.0 if value > 1e11 else float(value)
            return datetime.fromtimestamp(seconds, tz=UTC)
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (ValueError, OverflowError, OSError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_date(value: Any) -> date | None:
    """Date from ISO date/datetime strings; None if unparseable."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        dt = parse_datetime(text)
        return dt.date() if dt else None
