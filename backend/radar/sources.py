"""The Fonti screen: one card per source (state, last error in plain Italian with what to do,
last run counts, 7 days of new postings, ATS boards), plus today's quotas.

Read-only. Everything comes from the local database (runs, postings, settings), the private
config files and, for LinkedIn, ``linkedin --json quota`` (offline, no network call). Company
names appear only in the per-board lists of the ATS cards (local web app, never in logs).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Session, col, select

from radar.config import Settings, get_settings, load_yaml_config
from radar.connectors.registry import ATS_SOURCES
from radar.connectors.status import all_status
from radar.db import get_setting
from radar.models import Contact, LLMUsage, Posting, Run
from radar.timeutil import utcnow

LABELS = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters",
    "workday": "Workday",
    "personio": "Personio",
    "workable": "Workable",
    "teamtailor": "Teamtailor",
    "recruitee": "Recruitee",
    "inrecruiting": "inRecruiting",
    "linkedin_jobs": "LinkedIn (ricerche salvate)",
    "jobteaser": "JobTeaser (pagine offerta)",
    "extension": "Estensione «Salva in Radar»",
    "email": "Email di avviso (IMAP)",
    "contacts": "Ricerca contatti",
}
#: Daily token limits of Groq's free tier per model (console.groq.com/docs/rate-limits, Oct 2026).
#: ``RADAR_GROQ_DAILY_TOKENS`` overrides them ("model=tokens,model=tokens").
GROQ_DAILY_TOKENS = {"openai/gpt-oss-20b": 200_000, "openai/gpt-oss-120b": 200_000}
CHECK_BOARDS = "uv run python scripts/check_boards.py --ats {ats}"


def _tz() -> Any:
    try:
        return ZoneInfo("Europe/Rome")
    except ZoneInfoNotFoundError:
        return UTC


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- errors in Italian


def explain(source: str, error: str) -> tuple[str, str]:
    """(what is wrong, what to do) for an error label such as ``SourceError (HTTP 404)``."""
    m = re.search(r"HTTP (\d{3})", error)
    status = int(m.group(1)) if m else None
    ats = source in ATS_SOURCES
    if error.startswith("RobotsDisallowed"):
        return (
            "Il robots.txt del sito non permette di leggere questa pagina: Radar non la legge.",
            "Niente da fare: resta esclusa finché il sito non cambia robots.txt. Per toglierla "
            "dalla lista metti `active: false` in config/companies.yaml.",
        )
    if status == 404 and ats:
        return (
            "La bacheca non esiste più (HTTP 404): l'azienda ha cambiato indirizzo o sistema.",
            f"Controlla con `{CHECK_BOARDS.format(ats=source)}` e correggi lo slug in "
            "config/companies.yaml (o metti `active: false`).",
        )
    if status == 422 and source == "workday":
        return (
            "Workday ha rifiutato la ricerca senza una sessione (HTTP 422): Radar non la aggira.",
            f"Controlla il nome del sito con `{CHECK_BOARDS.format(ats=source)}`; se è giusto, "
            "metti `active: false` per questa azienda.",
        )
    if status in (401, 403, 429, 999):
        return (
            f"Il sito ha rifiutato o limitato le richieste (HTTP {status}).",
            "Niente da fare subito: Radar riprova domani alla solita velocità. Se succede più "
            f"giorni di fila, controlla con `{CHECK_BOARDS.format(ats=source)}`."
            if ats
            else "Niente da fare subito: Radar riprova domani alla solita velocità.",
        )
    if (status and status >= 500) or any(
        k in error for k in ("Timeout", "ConnectError", "RemoteProtocolError", "ReadError")
    ):
        return (
            "Il sito non ha risposto (errore temporaneo del server o della rete).",
            "Niente da fare: Radar riprova al prossimo giro.",
        )
    if error.startswith("store:"):
        return (
            "Gli annunci sono stati letti ma non salvati (errore del database).",
            "Esegui `make run-pipeline` e guarda l'errore nel terminale; se si ripete, "
            "`radar db secure` e poi di nuovo.",
        )
    return (
        f"Errore non previsto ({error}).",
        f"Riprova solo questa fonte con `uv run radar run --sources {source} --no-llm` e "
        "guarda il report dell'esecuzione.",
    )


# --------------------------------------------------------------------------- runs


def _runs(session: Session, limit: int = 30) -> list[Run]:
    return list(
        session.exec(
            select(Run).order_by(col(Run.started_at).desc(), col(Run.id).desc()).limit(limit)
        )
    )


def _run_info(run: Run | None) -> dict[str, Any] | None:
    if run is None:
        return None
    stats = run.stats or {}
    wall = (
        (_aware(run.finished_at) - _aware(run.started_at)).total_seconds()
        if run.finished_at
        else None
    )
    return {
        "id": run.id,
        "started_at": _aware(run.started_at).isoformat(),
        "finished_at": _aware(run.finished_at).isoformat() if run.finished_at else None,
        "status": run.status,
        "wall_seconds": round(wall) if wall is not None else None,
        "awake_seconds": stats.get("awake_seconds"),
        "timings": stats.get("timings") or {},
        "report": bool(run.report_md),
    }


def _last_run_for(runs: list[Run], source: str) -> Run | None:
    for run in runs:
        if source in ((run.stats or {}).get("sources") or {}):
            return run
    return None


def _errors_for(run: Run | None, source: str) -> list[dict[str, Any]]:
    if run is None:
        return []
    return [e for e in (run.stats or {}).get("errors") or [] if e.get("source") == source]


# --------------------------------------------------------------------------- charts


def _days(today: date) -> list[date]:
    return [today - timedelta(days=i) for i in range(6, -1, -1)]


def _daily_new(session: Session, today: date) -> dict[str, Counter[date]]:
    tz = _tz()
    since = datetime.combine(today - timedelta(days=7), datetime.min.time(), tzinfo=UTC)
    out: dict[str, Counter[date]] = {}
    for source, first_seen in session.exec(
        select(Posting.source, Posting.first_seen).where(col(Posting.first_seen) >= since)
    ):
        out.setdefault(source, Counter())[_aware(first_seen).astimezone(tz).date()] += 1
    return out


def _series(counter: Counter[date] | None, today: date) -> list[dict[str, Any]]:
    return [{"day": d.isoformat(), "n": (counter or Counter())[d]} for d in _days(today)]


# --------------------------------------------------------------------------- quotas


def groq_limits(settings: Settings) -> dict[str, int]:
    limits = dict(GROQ_DAILY_TOKENS)
    raw = (settings.groq_daily_tokens or "").strip()
    for part in raw.split(","):
        model, _, value = part.partition("=")
        if model.strip() and value.strip().isdigit():
            limits[model.strip()] = int(value)
    return limits


def groq_today(session: Session, settings: Settings) -> list[dict[str, Any]]:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    used: Counter[str] = Counter()
    for u in session.exec(
        select(LLMUsage).where(LLMUsage.provider == "groq", col(LLMUsage.at) >= start)
    ):
        used[u.model] += u.input_tokens + u.output_tokens
    limits = groq_limits(settings)
    models = sorted(set(used) | {settings.scoring_model, settings.draft_model} & set(limits))
    return [{"model": m, "used": used[m], "limit": limits.get(m)} for m in models]


def linkedin_quota(settings: Settings) -> dict[str, Any] | None:
    """Calls used today from linkedin-cli's own offline counter; None when unavailable."""
    from radar.connectors import linkedin_jobs

    if not linkedin_jobs.available(settings):
        return None
    try:
        proc = subprocess.run(
            [settings.linkedin_bin, "--json", "quota"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        data = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, dict):
        return None
    return {
        "used": int(jobs.get("used", 0)),
        "limit": linkedin_jobs.MAX_CALLS_CEILING,
        "cli_limit": int(jobs.get("limit", 0)),
    }


def contacts_quota(session: Session) -> dict[str, Any]:
    from radar.llm.contacts import budget_status

    b = budget_status(session)
    return {
        "month_cost_eur": round(b.month_cost_eur, 3),
        "monthly_cap_eur": b.monthly_cap_eur,
        "calls_today": b.calls_today,
        "daily_call_cap": b.daily_call_cap,
    }


# --------------------------------------------------------------------------- per source


def _companies_cfg() -> dict[str, Any]:
    try:
        cfg = load_yaml_config("companies")
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _ats_boards(cfg: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for entry in cfg.get("companies") or []:
        if isinstance(entry, dict) and entry.get("active", True) and entry.get("slug"):
            ats = str(entry.get("ats") or "none").lower()
            if ats in ATS_SOURCES:
                out.setdefault(ats, []).append(str(entry.get("name") or entry["slug"]))
    return out


def _board_summary(run: Run | None, source: str, configured: list[str]) -> dict[str, Any]:
    entries = (
        [b for b in (run.stats or {}).get("boards") or [] if b.get("source") == source]
        if run
        else []
    )
    if not entries:  # runs before per-board stats: counts only
        errors = _errors_for(run, source)
        return {
            "configured": len(configured),
            "working": None,
            "empty": [],
            "failing": [
                {"company": e.get("company"), "problem": explain(source, str(e.get("error")))[0]}
                for e in errors
            ],
            "detail": False,
        }
    failing = [b for b in entries if b.get("error")]
    empty = [b for b in entries if not b.get("error") and not b.get("fetched")]
    return {
        "configured": len(configured),
        "working": len(entries) - len(failing),
        "empty": sorted(str(b.get("company") or b.get("board")) for b in empty),
        "failing": [
            {
                "company": b.get("company") or b.get("board"),
                "problem": explain(source, str(b["error"]))[0],
                "action": explain(source, str(b["error"]))[1],
            }
            for b in failing
        ],
        "detail": True,
    }


def _card(
    key: str,
    *,
    state: str,
    run: Run | None,
    chart: list[dict[str, Any]] | None,
    problem: str | None = None,
    action: str | None = None,
    paused_since: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    src = ((run.stats or {}).get("sources") or {}).get(key, {}) if run else {}
    return {
        "key": key,
        "label": LABELS.get(key, key),
        "state": state,
        "problem": problem,
        "action": action,
        "paused_since": paused_since,
        "resumable": state == "paused",
        "last_run": _run_info(run),
        "fetched": src.get("fetched"),
        "new": src.get("new"),
        "closed": src.get("closed"),
        "seconds": src.get("seconds"),
        "chart": chart,
        **extra,
    }


def _state_from(
    key: str, run: Run | None, paused: dict[str, Any], disabled: str | None
) -> tuple[str, str | None, str | None, str | None]:
    """(state, problem, action, paused_since)."""
    if key in paused:
        p = paused[key]
        return "paused", str(p.get("reason") or ""), str(p.get("action") or ""), p.get("since")
    if disabled:
        problem, _, action = disabled.partition("|")
        return "disabled", problem, action or None, None
    errors = _errors_for(run, key)
    if errors:
        last = errors[-1]
        problem, action = explain(key, str(last.get("error", "")))
        problem = str(last.get("problem") or problem)
        action = str(last.get("action") or action)
        if len(errors) > 1 and key in ATS_SOURCES:
            problem = f"{len(errors)} bacheche con errori. Ultimo: {problem}"
        return "errors", problem, action, None
    return "ok", None, None, None


def overview(session: Session, now: datetime | None = None) -> dict[str, Any]:
    settings = get_settings()
    now = now or utcnow()
    today = _aware(now).astimezone(_tz()).date()
    runs = _runs(session)
    paused = all_status(session)
    enabled = get_setting("sources_enabled", None, session=session)
    enabled = enabled if isinstance(enabled, dict) else {}
    cfg = _companies_cfg()
    boards = _ats_boards(cfg)
    daily = _daily_new(session, today)
    cards: list[dict[str, Any]] = []

    for ats in ATS_SOURCES:
        run = _last_run_for(runs, ats)
        configured = boards.get(ats, [])
        disabled = None
        if enabled.get(ats) is False:
            disabled = "Spenta in Impostazioni.|Riaccendila in Impostazioni → Fonti."
        elif ats == "smartrecruiters":
            disabled = (
                "Il robots.txt di SmartRecruiters non permette la lettura al nostro user agent.|"
                "Niente da fare: resta spenta (vedi docs/SOURCES.md)."
            )
        elif not configured:
            disabled = (
                "Nessuna bacheca attiva in config/companies.yaml.|"
                f"Aggiungi aziende con `ats: {ats}` in config/companies.yaml."
            )
        state, problem, action, since = _state_from(ats, run, paused, disabled)
        cards.append(
            _card(
                ats,
                state=state,
                run=run,
                chart=_series(daily.get(ats), today),
                problem=problem,
                action=action,
                paused_since=since,
                kind="ats",
                boards=_board_summary(run, ats, configured) if configured else None,
            )
        )

    from radar.connectors import jobteaser as jt
    from radar.connectors import linkedin_jobs

    # LinkedIn saved searches
    run = _last_run_for(runs, "linkedin_jobs")
    disabled = None
    if not linkedin_jobs.searches_path(settings).is_file():
        disabled = (
            "Manca config/linkedin_searches.yaml.|"
            "Copia config/linkedin_searches.example.yaml in config/linkedin_searches.yaml."
        )
    elif not linkedin_jobs.available(settings):
        disabled = "linkedin-cli non trovato (RADAR_LINKEDIN_BIN).|" + linkedin_jobs.ACTION_INSTALL
    state, problem, action, since = _state_from("linkedin_jobs", run, paused, disabled)
    li_state = get_setting(linkedin_jobs.STATE_KEY, None, session=session)
    cards.append(
        _card(
            "linkedin_jobs",
            state=state,
            run=run,
            chart=_series(daily.get("linkedin_jobs"), today),
            problem=problem,
            action=action,
            paused_since=since,
            kind="session",
            last_day=li_state.get("last_run") if isinstance(li_state, dict) else None,
        )
    )

    # JobTeaser offer pages (descriptions, no new postings)
    run = _last_run_for(runs, "jobteaser")
    disabled = None
    if not jt.profile_ready(settings):
        disabled = (
            "Nessun profilo browser JobTeaser.|Esegui `make jobteaser-login` e accedi con "
            "l'account Luiss."
        )
    state, problem, action, since = _state_from("jobteaser", run, paused, disabled)
    cards.append(
        _card(
            "jobteaser",
            state=state,
            run=run,
            chart=None,
            problem=problem,
            action=action,
            paused_since=since,
            kind="session",
        )
    )

    # Extension
    from radar.extension_token import token_configured
    from radar.pipeline.jobteaser_ingest import last_pass, reminder

    last = last_pass(session)
    ext_state = "ok" if token_configured(session) else "disabled"
    cards.append(
        _card(
            "extension",
            state=ext_state,
            run=None,
            chart=_series(daily.get("jobteaser"), today),
            problem=None
            if ext_state == "ok"
            else "L'estensione non è ancora collegata a Radar (nessun token).",
            action=None
            if ext_state == "ok"
            else "Esegui `uv run radar extension token` e incolla il token nelle opzioni "
            "dell'estensione (docs/RUNNING.md).",
            kind="local",
            last_pass=last,
            remind=reminder(last, now),
        )
    )

    # Email alerts
    run = _last_run_for(runs, "email")
    disabled = None
    if not (settings.imap_user or "").strip():
        disabled = (
            "Casella email non configurata.|Imposta RADAR_IMAP_USER in .env, poi "
            "`uv run radar imap login` (Outlook) o `uv run radar imap set-password` (Gmail)."
        )
    state, problem, action, since = _state_from("email", run, paused, disabled)
    cards.append(
        _card(
            "email",
            state=state,
            run=run,
            chart=_series(daily.get("email"), today),
            problem=problem,
            action=action,
            paused_since=since,
            kind="session",
        )
    )

    # Contact search (LLM): last run's stats instead of fetched/new
    run_c = next((r for r in runs if ((r.stats or {}).get("llm") or {}).get("contacts")), None)
    contacts_stats = ((run_c.stats or {}).get("llm") or {}).get("contacts") if run_c else None
    tz = _tz()
    since_c = datetime.combine(today - timedelta(days=7), datetime.min.time(), tzinfo=UTC)
    found: Counter[date] = Counter()
    for created in session.exec(
        select(Contact.created_at).where(col(Contact.created_at) >= since_c)
    ):
        found[_aware(created).astimezone(tz).date()] += 1
    contacts_problem = None
    contacts_state = "ok"
    if contacts_stats and contacts_stats.get("skipped") == "missing_key":
        contacts_state, contacts_problem = "disabled", "Manca la chiave GROQ_API_KEY."
    elif contacts_stats and contacts_stats.get("errors"):
        contacts_state, contacts_problem = "errors", "Alcune ricerche di contatti sono fallite."
    cards.append(
        _card(
            "contacts",
            state=contacts_state,
            run=run_c,
            chart=_series(found, today),
            problem=contacts_problem,
            action="Imposta GROQ_API_KEY in .env." if contacts_state == "disabled" else None,
            kind="llm",
            contacts=contacts_stats,
        )
    )

    latest = runs[0] if runs else None
    return {
        "generated_at": _aware(now).isoformat(),
        "last_run": _run_info(latest),
        "sources": cards,
        "quotas": {
            "linkedin": linkedin_quota(settings),
            "groq": groq_today(session, settings),
            "contacts": contacts_quota(session),
        },
        "attention": sum(1 for c in cards if c["state"] in ("paused", "errors")),
    }
