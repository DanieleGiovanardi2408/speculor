"""Source "linkedin_jobs": the user's saved LinkedIn job searches, through linkedin-cli.

Runs on the user's own computer only, with the user's own LinkedIn session, by calling the
``linkedin`` CLI (mayai-linkedin-cli >= 0.3, ``RADAR_LINKEDIN_BIN``) as a subprocess, so the
CLI's throttle, daily quotas (``jobs`` <= 30/day) and block detection apply unchanged.

* Searches come from ``config/linkedin_searches.yaml`` (gitignored; see the ``.example``).
* Once a day: a second run on the same local date returns ``[]`` without any call.
* Budget: at most ``max_calls`` (default and ceiling 25) CLI calls that hit LinkedIn per day,
  measured with ``linkedin --json quota`` (offline) before and after every call, and never
  more than what the CLI's own ``jobs`` / ``api_total`` quotas still allow.
* Rotation: every (search, location) pair is a slot. Slots are ordered round-robin (first
  location of every search, then the second, ...) and each day continues from where the
  previous day stopped (cursor stored in the ``linkedin_jobs:state`` setting). With
  ``date_posted: auto`` the window is chosen from the cycle length: one day -> ``24h``,
  up to seven days -> ``week``, longer -> ``month``, so a full cycle never misses postings.
* Optional: description/closed state for the few newest postings not yet in the DB.
* Not exhaustive (``exhaustive = False``): a posting missing from a search is never closed.
* Sequential, no retries. CLI exit 3 (checkpoint, captcha, HTTP 429/999) or 2 (not logged in)
  raises :class:`SourcePaused`; exit 4 (quota) ends today's run quietly.
* Counts-only logging; refuses to run in CI.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

from radar.connectors.base import SourceError
from radar.connectors.status import SourcePaused
from radar.schemas import RawPosting

if TYPE_CHECKING:
    from radar.config import Settings

log = logging.getLogger(__name__)

SOURCE = "linkedin_jobs"
SEARCHES_FILE = "linkedin_searches.yaml"
STATE_KEY = "linkedin_jobs:state"
JOB_URL = "https://www.linkedin.com/jobs/view/{id}/"

MAX_CALLS_CEILING = 25  # the CLI allows 30/day; keep a margin for the user's own use
DEFAULT_MAX_DETAILS = 3
DEFAULT_MAX_SEED_DETAILS = 4  # `jobs get` for seed postings (tracker export) without a description
CLI_TIMEOUT_S = 300  # a 25-card page with the CLI's jitter takes seconds, not minutes
MAX_CONSECUTIVE_ERRORS = 2

EXIT_AUTH = 2
EXIT_BLOCKED = 3
EXIT_QUOTA = 4

EXPERIENCE = frozenset({"internship", "entry", "associate", "mid-senior", "director", "executive"})
JOB_TYPES = frozenset(
    {"full-time", "part-time", "contract", "temporary", "internship", "volunteer", "other"}
)
WORKPLACE = frozenset({"onsite", "remote", "hybrid"})
DATE_POSTED = frozenset({"24h", "week", "month", "any", "auto"})

ACTION_BLOCKED = (
    "Apri linkedin.com nel browser e completa la verifica che ti chiede, poi esegui "
    "`linkedin auth login` e `radar connectors resume linkedin_jobs`. Per oggi niente altre "
    "ricerche automatiche."
)
INSTALL_CMD = "uv tool install --force git+https://github.com/mayai-it/linkedin-cli@main"
ACTION_INSTALL = (
    f"Installa linkedin-cli: `{INSTALL_CMD}`, poi in .env imposta RADAR_LINKEDIN_BIN al "
    "percorso stampato da `which linkedin` (percorso assoluto: launchd non legge il PATH della "
    "shell)."
)
ACTION_UPGRADE = (
    f"Aggiorna linkedin-cli: `{INSTALL_CMD}` (non `uv tool install .` da una cartella su un "
    "altro branch), poi controlla che `linkedin --version` dica 0.3.0 o più."
)
ACTION_AUTH = (
    "La sessione di linkedin-cli è scaduta: esegui `linkedin auth login`, poi "
    "`radar connectors resume linkedin_jobs`."
)

_BLOCK_RE = re.compile(
    r"blocked \((\w+)\)|checkpoint|captcha|rate.?limited|\b429\b|\b999\b", re.IGNORECASE
)
_WORKPLACE_SUFFIX = re.compile(r"\s*\(([^()]+)\)\s*$")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Location:
    key: str
    geo_id: str | None = None  # numeric LinkedIn geoId (preferred: exactly one call)
    name: str | None = None  # resolved by the CLI (cached there): +1 call the first time

    @property
    def cost(self) -> int:
        return 1 if self.geo_id else 2


@dataclass(frozen=True)
class Search:
    id: str
    keywords: str
    locations: tuple[Location, ...]
    experience: tuple[str, ...] = ()
    job_types: tuple[str, ...] = ()
    workplace: tuple[str, ...] = ()
    date_posted: str | None = None  # None = plan default


@dataclass
class SearchPlan:
    searches: list[Search]
    max_calls: int = MAX_CALLS_CEILING
    max_details: int = DEFAULT_MAX_DETAILS
    max_seed_details: int = DEFAULT_MAX_SEED_DETAILS
    date_posted: str = "auto"
    sort: str = "recent"
    limit: int = 25

    def slots(self) -> list[tuple[Search, Location]]:
        """All (search, location) pairs, round-robin: each search's 1st location, then 2nd..."""
        rounds = max((len(s.locations) for s in self.searches), default=0)
        return [
            (s, s.locations[r])
            for r in range(rounds)
            for s in self.searches
            if r < len(s.locations)
        ]

    @property
    def search_calls_per_day(self) -> int:
        return max(1, self.max_calls - self.max_details - self.max_seed_details)

    def cycle_days(self) -> int:
        """Days needed to run every slot once (upper bound, geoId slots cost one call)."""
        cost = sum(loc.cost for _, loc in self.slots())
        return max(1, math.ceil(cost / self.search_calls_per_day))

    def window(self, search: Search) -> str:
        chosen = search.date_posted or self.date_posted
        if chosen != "auto":
            return chosen
        days = self.cycle_days()
        return "24h" if days <= 1 else "week" if days <= 7 else "month"


def _str_list(value: Any, allowed: frozenset[str], what: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    items = [value] if isinstance(value, str) else list(value)
    out = tuple(str(v).strip().lower() for v in items if str(v).strip())
    bad = [v for v in out if v not in allowed]
    if bad:
        raise ValueError(f"linkedin_searches: unknown {what} {bad}")
    return out


def parse_plan(data: dict[str, Any]) -> SearchPlan:
    """Validate ``linkedin_searches.yaml`` (see the example file for the format)."""
    locations: dict[str, Location] = {}
    for key, raw in (data.get("locations") or {}).items():
        entry = raw if isinstance(raw, dict) else {"geo_id": raw}
        geo = str(entry.get("geo_id") or "").strip() or None
        if geo is not None and not geo.isdigit():
            raise ValueError(f"linkedin_searches: geo_id of {key!r} must be numeric")
        name = str(entry.get("name") or "").strip() or None
        if geo is None and name is None:
            raise ValueError(f"linkedin_searches: location {key!r} needs geo_id or name")
        locations[str(key)] = Location(str(key), geo, name)
    groups = {str(k): [str(x) for x in v] for k, v in (data.get("location_groups") or {}).items()}

    def expand(refs: Iterable[str]) -> tuple[Location, ...]:
        out: list[Location] = []
        for ref in refs:
            for key in groups.get(ref, [ref]):
                if key not in locations:
                    raise ValueError(f"linkedin_searches: unknown location {key!r}")
                if locations[key] not in out:
                    out.append(locations[key])
        return tuple(out)

    defaults = data.get("defaults") or {}
    searches: list[Search] = []
    seen: set[str] = set()
    for raw in data.get("searches") or []:
        if not isinstance(raw, dict) or not raw.get("keywords") or raw.get("active") is False:
            continue
        sid = str(raw.get("id") or raw["keywords"]).strip()
        if sid in seen:
            raise ValueError(f"linkedin_searches: duplicate search id {sid!r}")
        seen.add(sid)
        refs = raw.get("locations") or defaults.get("locations") or []
        date_posted = raw.get("date_posted")
        if date_posted is not None and str(date_posted) not in DATE_POSTED:
            raise ValueError(f"linkedin_searches: bad date_posted {date_posted!r}")
        searches.append(
            Search(
                id=sid,
                keywords=str(raw["keywords"]).strip(),
                locations=expand([refs] if isinstance(refs, str) else refs),
                experience=_str_list(
                    raw.get("experience", defaults.get("experience")), EXPERIENCE, "experience"
                ),
                job_types=_str_list(
                    raw.get("job_types", defaults.get("job_types")), JOB_TYPES, "job type"
                ),
                workplace=_str_list(
                    raw.get("workplace", defaults.get("workplace")), WORKPLACE, "workplace"
                ),
                date_posted=str(date_posted) if date_posted is not None else None,
            )
        )
    plan_window = str(data.get("date_posted", "auto"))
    if plan_window not in DATE_POSTED:
        raise ValueError(f"linkedin_searches: bad date_posted {plan_window!r}")
    max_calls = max(1, min(int(data.get("max_calls", MAX_CALLS_CEILING)), MAX_CALLS_CEILING))
    max_details = max(0, min(int(data.get("max_details", DEFAULT_MAX_DETAILS)), max_calls - 1))
    max_seed_details = max(
        0,
        min(
            int(data.get("max_seed_details", DEFAULT_MAX_SEED_DETAILS)),
            max_calls - 1 - max_details,
        ),
    )
    sort = str(data.get("sort", "recent"))
    if sort not in ("recent", "relevant"):
        raise ValueError(f"linkedin_searches: bad sort {sort!r}")
    return SearchPlan(
        searches=[s for s in searches if s.locations],
        max_calls=max_calls,
        max_details=max_details,
        max_seed_details=max_seed_details,
        date_posted=plan_window,
        sort=sort,
        limit=max(1, min(int(data.get("limit", 25)), 25)),  # 25 = one call per search
    )


def searches_path(settings: Settings) -> Path:
    return settings.config_dir / SEARCHES_FILE


def _in_ci() -> bool:
    return any(os.environ.get(var) for var in ("CI", "GITHUB_ACTIONS"))


def available(settings: Settings) -> bool:
    """True when the private searches file exists, the CLI binary is on disk and not in CI."""
    return (
        not _in_ci()
        and searches_path(settings).is_file()
        and shutil.which(settings.linkedin_bin) is not None
    )


# ---------------------------------------------------------------------------
# state + DB lookups (injectable for tests)
# ---------------------------------------------------------------------------


class StateStore(Protocol):
    def load(self) -> dict[str, Any]: ...

    def save(self, state: dict[str, Any]) -> None: ...


class SettingsStateStore:
    """``linkedin_jobs:state`` row in the settings table."""

    def load(self) -> dict[str, Any]:
        from radar.db import get_setting

        value = get_setting(STATE_KEY, None)
        return dict(value) if isinstance(value, dict) else {}

    def save(self, state: dict[str, Any]) -> None:
        from radar.db import session_scope, set_setting

        with session_scope() as s:
            set_setting(s, STATE_KEY, state)


def known_with_description(urls: Sequence[str]) -> set[str]:
    """Canonical URLs (any source) already stored with a description: no detail call needed."""
    if not urls:
        return set()
    from sqlmodel import col, select

    from radar.db import session_scope
    from radar.models import Posting
    from radar.pipeline.normalize import canonical_url

    wanted = {canonical_url(u): u for u in urls}
    with session_scope() as s:
        rows = s.exec(
            select(Posting.canonical_url, Posting.raw_text).where(
                col(Posting.canonical_url).in_(list(wanted))
            )
        ).all()
    return {wanted[c] for c, text in rows if c in wanted and (text or "").strip()}


# ---------------------------------------------------------------------------
# CLI calls
# ---------------------------------------------------------------------------


@dataclass
class CliOutcome:
    code: int
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


def _parse_rows(stdout: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_raw(row: dict[str, Any], search_id: str) -> RawPosting | None:
    job_id = str(row.get("job_id") or "").strip()
    title = str(row.get("title") or "").strip()
    if not job_id.isdigit() or not title:
        return None
    location = str(row.get("location") or "").strip() or None
    extra: dict[str, Any] = {"search": search_id}
    if location:
        m = _WORKPLACE_SUFFIX.search(location)
        if m:  # "Milano, Lombardia, Italia (Ibrido)" -> location + workplace
            extra["workplace"] = m.group(1)
            location = location[: m.start()].strip() or None
    for key in ("easy_apply", "salary", "reposted"):
        if row.get(key) not in (None, ""):
            extra[key] = row[key]
    return RawPosting(
        source=SOURCE,
        external_id=job_id,
        url=JOB_URL.format(id=job_id),
        title=title,
        company=str(row.get("company") or "").strip(),
        location=location,
        posted_at=_iso_datetime(row.get("listed_at")),
        extra=extra,
    )


class LinkedInJobsConnector:
    source = SOURCE
    label = "linkedin saved searches"
    #: a search lists only some postings: never close by absence (see pipeline/closed.py)
    exhaustive = False

    def __init__(
        self,
        plan: SearchPlan,
        *,
        binary: str = "linkedin",
        runner: Runner = subprocess.run,
        state: StateStore | None = None,
        today: Callable[[], date] = date.today,
        known: Callable[[Sequence[str]], set[str]] = known_with_description,
        seeds: Callable[[], list[str]] = list,
    ) -> None:
        self.plan = plan
        self.binary = binary
        self._runner = runner
        self._state = state or SettingsStateStore()
        self._today = today
        self._known = known
        self._seeds = seeds

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> LinkedInJobsConnector | None:
        if not available(settings):
            return None
        data = yaml.safe_load(searches_path(settings).read_text(encoding="utf-8")) or {}
        plan = parse_plan(data if isinstance(data, dict) else {})
        if not plan.searches:
            return None
        kwargs.setdefault("seeds", seed_job_ids)
        return cls(plan, binary=settings.linkedin_bin, **kwargs)

    # ---- subprocess -----------------------------------------------------------------------

    def _cli(self, args: Sequence[str]) -> CliOutcome:
        cmd = [self.binary, "--json", *args]
        try:
            proc = self._runner(
                cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT_S, check=False
            )
        except (FileNotFoundError, PermissionError):
            raise SourceError(
                "linkedin_jobs: linkedin-cli not found (RADAR_LINKEDIN_BIN)",
                problem="linkedin-cli non è installato nel percorso indicato da "
                "RADAR_LINKEDIN_BIN (o non è eseguibile).",
                action=ACTION_INSTALL,
            ) from None
        except subprocess.TimeoutExpired:
            # Never retried: the call may have reached LinkedIn.
            raise SourceError(
                "linkedin_jobs: linkedin-cli timed out",
                problem=f"linkedin-cli non ha risposto entro {CLI_TIMEOUT_S // 60} minuti "
                f"(comando `{args[0] if args else ''}`).",
                action="Prova `linkedin --json quota` nel terminale: se resta bloccato, "
                "controlla la connessione e riprova domani (nessun nuovo tentativo oggi).",
            ) from None
        stderr = (proc.stderr or "").strip()
        return CliOutcome(proc.returncode, _parse_rows(proc.stdout), stderr)

    def version(self) -> str | None:
        """``linkedin --version`` ("linkedin, version 0.3.0" -> "0.3.0"); None if unknown."""
        try:
            proc = self._runner(
                [self.binary, "--version"], capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", proc.stdout or "")
        return m.group(1) if m else None

    def _jobs_used(self) -> tuple[int, int]:
        """(calls used today, calls still allowed) from the CLI's own offline quota file."""
        res = self._cli(["quota"])
        if res.code != 0 and "no such command" in res.error.lower():
            res = CliOutcome(0)  # linkedin-cli < 0.3: no `quota` command at all
        if res.code != 0:
            raise SourceError(
                f"linkedin_jobs: `linkedin quota` failed (exit {res.code})",
                problem=f"Il comando `linkedin --json quota` è fallito (codice {res.code}), "
                "quindi Radar non sa quante chiamate restano oggi e non ne fa nessuna.",
                action="Esegui `linkedin --json quota` nel terminale e leggi l'errore; se "
                f"linkedin-cli è vecchio o rotto reinstallalo con `{INSTALL_CMD}`.",
            )
        data = res.rows[-1] if res.rows else {}
        jobs = data.get("jobs")
        api = data.get("api_total") or {}
        if not isinstance(jobs, dict):
            found = self.version()
            raise SourceError(
                "linkedin_jobs: linkedin-cli >= 0.3 required (no jobs quota)",
                problem=f"È installato linkedin-cli {found or 'di versione sconosciuta'}, ma "
                "serve la 0.3 o successiva (comandi `jobs` e quota `jobs`).",
                action=ACTION_UPGRADE,
            )
        used = int(jobs.get("used", 0))
        left = min(
            int(jobs.get("limit", 0)) - used,
            int(api.get("limit", 0)) - int(api.get("used", 0)) if api else 10**6,
        )
        return used, max(0, left)

    def _raise_if_paused(self, res: CliOutcome) -> None:
        """Exit 3, or a block-looking error from any other failure: stop and wait for the user."""
        m = _BLOCK_RE.search(res.error) if res.code != 0 else None
        if res.code == EXIT_BLOCKED or (m and res.code not in (EXIT_AUTH, EXIT_QUOTA)):
            reason = (m.group(1) or m.group(0)) if m else "blocked"
            raise SourcePaused(SOURCE, reason.lower(), ACTION_BLOCKED)
        if res.code == EXIT_AUTH:
            raise SourcePaused(SOURCE, "not authenticated", ACTION_AUTH)

    # ---- fetch ----------------------------------------------------------------------------

    def fetch(self) -> list[RawPosting]:
        if _in_ci():
            raise SourceError("linkedin_jobs: never runs in CI")
        today = self._today().isoformat()
        state = self._state.load()
        if state.get("last_run") == today:
            log.info("linkedin_jobs: already ran today, skipped")
            return []
        slots = self.plan.slots()
        if not slots:
            return []

        start_used, allowed = self._jobs_used()
        budget = min(self.plan.max_calls, allowed)
        reserved = self.plan.max_details + self.plan.max_seed_details
        search_budget = budget - min(reserved, budget // 2)
        cursor = int(state.get("cursor", 0) or 0) % len(slots)
        # Record the run before the first call: a crash must not cause a second run today.
        state.update(last_run=today, calls=0, searches=0)
        self._state.save(state)

        found: dict[str, RawPosting] = {}
        used = searches = errors = consecutive = details = 0
        last_code = 0
        try:
            for step in range(len(slots)):  # at most one full cycle per day
                search, loc = slots[(cursor + step) % len(slots)]
                if used + loc.cost > search_budget:
                    break
                res = self._cli(self._search_args(search, loc))
                used = self._jobs_used()[0] - start_used
                state.update(cursor=(cursor + step + 1) % len(slots), calls=used)
                self._raise_if_paused(res)
                if res.code == EXIT_QUOTA:
                    break
                if res.code != 0:
                    errors += 1
                    last_code = res.code
                    consecutive += 1
                    if consecutive >= MAX_CONSECUTIVE_ERRORS:
                        break
                    continue
                consecutive = 0
                searches += 1
                for row in res.rows:
                    raw = to_raw(row, search.id)
                    if raw is not None:
                        found.setdefault(raw.external_id, raw)

            details, used = self._fetch_details(found, used, budget, start_used, state)
            seeds, used = self._fetch_seed_details(found, used, budget, start_used, state)
            details += seeds
        finally:
            state.update(searches=searches)
            self._state.save(state)

        log.info(
            "linkedin_jobs: %d searches, %d errors, %d calls, %d postings, %d details",
            searches,
            errors,
            used,
            len(found),
            details,
        )
        if errors and not searches:
            raise SourceError(
                f"linkedin_jobs: all {errors} searches failed (last exit {last_code})",
                problem=f"Tutte le {errors} ricerche LinkedIn di oggi sono fallite (ultimo "
                f"codice di uscita di linkedin-cli: {last_code}).",
                action="Prova a mano `linkedin --json jobs search --limit 1 -- data` e leggi "
                "l'errore. Radar riprova da solo domani.",
            )
        return list(found.values())

    def _search_args(self, search: Search, loc: Location) -> list[str]:
        args = ["jobs", "search"]
        if loc.geo_id:
            args += ["--geo-id", loc.geo_id]
        elif loc.name:
            args += ["--location", loc.name]
        for value in search.experience:
            args += ["--experience", value]
        for value in search.job_types:
            args += ["--job-type", value]
        for value in search.workplace:
            args += ["--workplace", value]
        args += [
            "--date-posted",
            self.plan.window(search),
            "--sort",
            self.plan.sort,
            "--limit",
            str(self.plan.limit),
            "--",
            search.keywords,
        ]
        return args

    def _fetch_details(
        self,
        found: dict[str, RawPosting],
        used: int,
        budget: int,
        start_used: int,
        state: dict[str, Any],
    ) -> tuple[int, int]:
        """Description + closed state for the newest postings not yet described in the DB."""
        if self.plan.max_details <= 0 or not found:
            return 0, used
        newest = sorted(
            found.values(),
            key=lambda r: r.posted_at.timestamp() if r.posted_at else 0.0,
            reverse=True,
        )
        known = self._known([r.url for r in newest])
        done = 0
        for raw in newest:
            if done >= self.plan.max_details or used + 1 > budget:
                break
            if raw.url in known:
                continue
            res = self._cli(["jobs", "get", raw.external_id])
            used = self._jobs_used()[0] - start_used
            state.update(calls=used)
            self._raise_if_paused(res)
            if res.code == EXIT_QUOTA:
                break
            done += 1
            if res.code != 0 or not res.rows:
                continue
            detail = res.rows[-1]
            if detail.get("closed") is True:
                found.pop(raw.external_id, None)  # listed by search, already closed
                continue
            raw.raw_text = str(detail.get("description") or "")
            raw.employment_type = str(detail.get("employment_type") or "") or None
            if detail.get("apply_url"):
                raw.extra["apply_url"] = detail["apply_url"]
        return done, used

    def _fetch_seed_details(
        self,
        found: dict[str, RawPosting],
        used: int,
        budget: int,
        start_used: int,
        state: dict[str, Any],
    ) -> tuple[int, int]:
        """`jobs get` for seed postings (tracker export) still without a description, within
        what is left of today's budget. They are returned as linkedin_jobs postings; dedupe
        then gives their description to the seed posting."""
        if self.plan.max_seed_details <= 0:
            return 0, used
        done = 0
        for job_id in self._seeds():
            if done >= self.plan.max_seed_details or used + 1 > budget:
                break
            if job_id in found:
                continue
            res = self._cli(["jobs", "get", job_id])
            used = self._jobs_used()[0] - start_used
            state.update(calls=used)
            self._raise_if_paused(res)
            if res.code == EXIT_QUOTA:
                break
            done += 1
            if res.code != 0 or not res.rows or res.rows[-1].get("closed") is True:
                continue
            raw = detail_to_raw(res.rows[-1])
            if raw is not None:
                found[job_id] = raw
        return done, used


def seed_job_ids() -> list[str]:
    """LinkedIn job ids of open seed postings still without a description (oldest check
    first): their description comes from `jobs get` (pipeline/describe.py never opens
    linkedin.com)."""
    from radar.connectors.linkedin_guest import parse_job_id
    from radar.db import session_scope
    from radar.pipeline.describe import missing_descriptions

    with session_scope() as s:
        urls = [p.url for p in missing_descriptions(s, channel="linkedin")]
    ids = [parse_job_id(u) for u in urls]
    return [i for i in dict.fromkeys(ids) if i]


def detail_to_raw(detail: dict[str, Any]) -> RawPosting | None:
    """`jobs get` output -> RawPosting (None without id, title or description)."""
    job_id = str(detail.get("job_id") or "").strip()
    title = str(detail.get("title") or "").strip()
    description = str(detail.get("description") or "").strip()
    if not job_id.isdigit() or not title or not description:
        return None
    raw = RawPosting(
        source=SOURCE,
        external_id=job_id,
        url=JOB_URL.format(id=job_id),
        title=title,
        company=str(detail.get("company") or "").strip(),
        location=str(detail.get("location") or "").strip() or None,
        raw_text=description,
        employment_type=str(detail.get("employment_type") or "") or None,
        posted_at=_iso_datetime(detail.get("listed_at")),
        extra={"search": "seed"},
    )
    if detail.get("apply_url"):
        raw.extra["apply_url"] = detail["apply_url"]
    return raw


__all__ = [
    "LinkedInJobsConnector",
    "Location",
    "Search",
    "SearchPlan",
    "available",
    "parse_plan",
    "searches_path",
    "to_raw",
]
