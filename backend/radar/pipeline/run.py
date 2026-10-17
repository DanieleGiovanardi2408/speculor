"""The daily pipeline: collect -> store/dedupe -> closed checks -> filters -> diff -> rank -> LLM
-> report.

One failing source never stops the run. Console output and stats contain counts and error
types only (no company names, no titles); the Markdown report is private and detailed.
Nothing is ever sent from here.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from sqlalchemy import Engine
from sqlmodel import Session

from radar.connectors.base import Connector
from radar.connectors.status import SourcePaused, pause, paused_sources
from radar.db import get_setting, init_db, set_setting
from radar.models import Posting, Run
from radar.pipeline.closed import (
    DEFAULT_CHECK_CAP,
    Checker,
    board_companies,
    check_open_postings,
    close_missing,
    is_exhaustive,
)
from radar.pipeline.diff import Diff, diff_for_run
from radar.pipeline.filters import Rules, apply_filters, apply_filters_all, load_rules
from radar.pipeline.rank import Embedder, rank_postings
from radar.pipeline.report import write_report
from radar.pipeline.store import company_index, upsert_raw
from radar.timeutil import utcnow

console = Console()
RULES_HASH_SETTING = "filters:rules_hash"


@dataclass
class PipelineOptions:
    sources: list[str] | None = None
    llm: bool = True
    max_score: int = 30
    max_packs: int = 5
    contacts: bool = True  # public contact search for Alta postings (budget-capped)
    check_closed: bool = True
    check_cap: int = DEFAULT_CHECK_CAP
    rank: bool = True  # local embedding similarity before the LLM (needs the `embed` extra)


@dataclass
class PipelineResult:
    run_id: int
    status: str
    stats: dict[str, Any] = field(default_factory=dict)
    report_path: Path | None = None

    def summary_line(self) -> str:
        s = self.stats
        fetched = sum(int(v.get("fetched", 0)) for v in (s.get("sources") or {}).values())
        return (
            f"Run #{self.run_id} {self.status}: fetched {fetched}, new {s.get('new', 0)}, "
            f"changed {s.get('changed', 0)}, closed {s.get('closed', 0)}, "
            f"reopened {s.get('reopened', 0)}, rejected {s.get('rejected', 0)}, "
            f"flagged {s.get('flagged', 0)}, errors {len(s.get('errors') or [])}"
        )


def _empty_stats() -> dict[str, Any]:
    return {
        "sources": {},
        "new": 0,
        "changed": 0,
        "closed": 0,
        "reopened": 0,
        "rejected": 0,
        "flagged": 0,
        "closed_by_absence": 0,
        "closed_by_check": {},
        "filtered": 0,
        "rank": {},
        "llm": {},
        "errors": [],
    }


def error_label(exc: BaseException) -> str:
    """Error type plus HTTP status if known. Never the message (it may contain names/URLs)."""
    status: int | None = None
    seen: BaseException | None = exc
    for _ in range(4):
        if seen is None:
            break
        for attr in ("status_code", "status"):
            value = getattr(seen, attr, None)
            if isinstance(value, int):
                status = value
        response = getattr(seen, "response", None)
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            status = code
        if status is None:
            m = re.search(r"\bHTTP (\d{3})\b", str(seen))
            if m:
                status = int(m.group(1))
        if status is not None:
            break
        seen = seen.__cause__ or seen.__context__
    name = type(exc).__name__
    return f"{name} (HTTP {status})" if status is not None else name


def _source_stats(stats: dict[str, Any], source: str) -> dict[str, int]:
    sources: dict[str, dict[str, int]] = stats["sources"]
    return sources.setdefault(source, {"boards": 0, "fetched": 0, "new": 0, "errors": 0})


def _enabled_sources(session: Session) -> dict[str, bool]:
    stored = get_setting("sources_enabled", None, session=session)
    return {str(k): bool(v) for k, v in stored.items()} if isinstance(stored, dict) else {}


def _select(
    connectors: Sequence[Connector], opts: PipelineOptions, enabled: dict[str, bool]
) -> list[Connector]:
    wanted = set(opts.sources) if opts.sources else None
    return [
        c
        for c in connectors
        if (wanted is None or c.source in wanted) and enabled.get(c.source, True)
    ]


def _collect(
    session: Session,
    connectors: Sequence[Connector],
    run_id: int,
    stats: dict[str, Any],
    now: datetime,
) -> tuple[set[int], set[int]]:
    """Fetch every connector, upsert, close-by-absence. Returns (touched ids, seen ids)."""
    touched: set[int] = set()
    seen: set[int] = set()
    companies = company_index(session)
    paused = paused_sources(session)
    for conn in connectors:
        src = _source_stats(stats, conn.source)
        if conn.source in paused:
            src["paused"] = 1
            continue
        src["boards"] += 1
        try:
            raws = conn.fetch()
        except SourcePaused as exc:
            src["errors"] += 1
            src["paused"] = 1
            paused.add(conn.source)
            pause(session, exc.source, exc.reason, exc.action)
            session.commit()
            stats["errors"].append({"source": conn.source, "error": f"paused: {exc.reason}"})
            continue
        except Exception as exc:
            src["errors"] += 1
            stats["errors"].append({"source": conn.source, "error": error_label(exc)})
            continue
        src["fetched"] += len(raws)
        try:
            uids: set[str] = set()
            for raw in raws:
                outcome = upsert_raw(session, raw, run_id=run_id, now=now, companies=companies)
                pid = outcome.posting.id
                assert pid is not None
                uids.add(outcome.posting.uid)
                primary_id = outcome.primary_id or pid
                seen.update((pid, primary_id))
                if outcome.kind != "unchanged":
                    touched.update((pid, primary_id))
                if outcome.kind == "new" and not outcome.is_duplicate:
                    src["new"] += 1
            if is_exhaustive(conn):
                stats["closed_by_absence"] += close_missing(
                    session,
                    source=conn.source,
                    companies=board_companies(conn, raws),
                    returned_uids=uids,
                    run_id=run_id,
                    now=now,
                )
            session.commit()
        except Exception as exc:
            session.rollback()
            src["errors"] += 1
            stats["errors"].append({"source": conn.source, "error": "store: " + error_label(exc)})
    return touched, seen


def _run_filters(
    session: Session, rules: Rules, touched: set[int], stats: dict[str, Any], today: date | None
) -> None:
    previous = get_setting(RULES_HASH_SETTING, None, session=session)
    if previous != rules.fingerprint:
        counts = apply_filters_all(session, rules, today)
        stats["filtered"] = counts.get("total", 0)
        stats["refiltered_all"] = True
        set_setting(session, RULES_HASH_SETTING, rules.fingerprint)
    else:
        for pid in sorted(touched):
            posting = session.get(Posting, pid)
            if posting is not None:
                apply_filters(session, posting, rules, today)
        stats["filtered"] = len(touched)
    session.commit()


def _run_rank(session: Session, stats: dict[str, Any], embedder: Embedder | None) -> None:
    try:
        stats["rank"] = rank_postings(session, embedder)
    except Exception as exc:
        session.rollback()
        stats["errors"].append({"source": "rank", "error": error_label(exc)})


def _run_llm(session: Session, run_id: int, opts: PipelineOptions, stats: dict[str, Any]) -> None:
    llm: dict[str, Any] = {}
    stats["llm"] = llm
    try:
        from radar.llm.scoring import score_pending
    except ImportError:
        llm["skipped"] = "scoring not available"
    else:
        try:
            llm["scoring"] = score_pending(session, run_id=run_id, limit=opts.max_score)
            session.commit()
        except Exception as exc:
            session.rollback()
            stats["errors"].append({"source": "llm:scoring", "error": error_label(exc)})
    try:
        from radar.llm.packs import build_packs
    except ImportError:
        llm["skipped"] = (llm.get("skipped", "") + "; packs not available").lstrip("; ")
        return
    try:
        llm["packs"] = build_packs(session, run_id=run_id, limit=opts.max_packs)
        session.commit()
    except Exception as exc:
        session.rollback()
        stats["errors"].append({"source": "llm:packs", "error": error_label(exc)})
    if not opts.contacts:
        return
    try:
        from radar.llm.contacts import find_contacts_for_alta

        llm["contacts"] = find_contacts_for_alta(session, run_id=run_id)
        session.commit()
    except Exception as exc:
        session.rollback()
        stats["errors"].append({"source": "llm:contacts", "error": error_label(exc)})


def _print_summary(stats: dict[str, Any], status: str, run_id: int) -> None:
    console.print(
        f"[bold]Radar run #{run_id}[/bold] [{'green' if status == 'ok' else 'yellow'}]{status}[/]"
    )
    for name, s in sorted((stats.get("sources") or {}).items()):
        console.print(
            f"  {name}: boards {s['boards']}, fetched {s['fetched']}, "
            f"new {s['new']}, errors {s['errors']}"
        )
    console.print(
        f"  new {stats['new']} · changed {stats['changed']} · closed {stats['closed']} · "
        f"reopened {stats['reopened']} · rejected {stats['rejected']} · flagged {stats['flagged']}"
    )
    if stats.get("errors"):
        console.print(f"  [yellow]errors: {len(stats['errors'])}[/yellow]")


def run_pipeline(
    opts: PipelineOptions,
    *,
    connectors: Sequence[Connector] | None = None,
    engine: Engine | None = None,
    checker: Checker | None = None,
    rules: Rules | None = None,
    today: date | None = None,
    reports_dir: Path | None = None,
    now: datetime | None = None,
    embedder: Embedder | None = None,
) -> PipelineResult:
    """Run the whole pipeline. ``connectors``/``engine``/``checker``/``rules``/``embedder`` are
    injectable for tests; by default they come from config, the database settings and the
    network."""
    engine = init_db(engine)
    now = now or utcnow()
    stats = _empty_stats()
    with Session(engine) as session:
        run = Run(started_at=now, status="running", stats=stats)
        session.add(run)
        session.commit()
        session.refresh(run)
        assert run.id is not None
        run_id = run.id
        failed = False
        n_connectors = 0
        try:
            enabled = _enabled_sources(session)
            if connectors is not None:
                selected = _select(connectors, opts, enabled)
                n_connectors = len(selected)
                _core(session, selected, run_id, opts, stats, checker, rules, today, now)
            else:
                from radar.connectors.closed import check_open
                from radar.connectors.http import PoliteClient
                from radar.connectors.registry import build_connectors

                with PoliteClient.from_settings() as client:
                    built: list[Connector] = []
                    try:
                        from radar.config import load_yaml_config
                        from radar.seed import sync_companies_config

                        companies_cfg = load_yaml_config("companies")
                        built = build_connectors(client, companies_cfg, opts.sources)
                        # company rows (sector, ATS) before collecting, so postings link to them
                        sync_companies_config(session, companies_cfg)
                        session.commit()
                    except Exception as exc:
                        session.rollback()
                        stats["errors"].append({"source": "config", "error": error_label(exc)})
                    selected = _select(built, opts, enabled)
                    n_connectors = len(selected)

                    def url_checker(url: str) -> bool | None:
                        return check_open(client, url)

                    _core(
                        session,
                        selected,
                        run_id,
                        opts,
                        stats,
                        checker or url_checker,
                        rules,
                        today,
                        now,
                    )
        except Exception as exc:
            session.rollback()
            failed = True
            stats["errors"].append({"source": "pipeline", "error": error_label(exc)})

        if opts.rank and not failed:
            _run_rank(session, stats, embedder)
        if opts.llm and not failed:
            _run_llm(session, run_id, opts, stats)

        source_errors = sum(int(s.get("errors", 0)) for s in stats["sources"].values())
        if failed or (n_connectors > 0 and source_errors >= n_connectors):
            status = "failed"
        elif stats["errors"]:
            status = "partial"
        else:
            status = "ok"

        run = session.get(Run, run_id) or run
        run.status = status
        run.stats = dict(stats)
        run.finished_at = utcnow()
        session.add(run)
        session.commit()

        report_path: Path | None = None
        try:
            diff = diff_for_run(session, run_id)
            report_path = write_report(session, run, diff, reports_dir, today)
            session.commit()
        except Exception as exc:
            session.rollback()
            stats["errors"].append({"source": "report", "error": error_label(exc)})
            run = session.get(Run, run_id) or run
            run.stats = dict(stats)
            session.add(run)
            session.commit()

    _print_summary(stats, status, run_id)
    return PipelineResult(run_id=run_id, status=status, stats=stats, report_path=report_path)


def _core(
    session: Session,
    connectors: Sequence[Connector],
    run_id: int,
    opts: PipelineOptions,
    stats: dict[str, Any],
    checker: Callable[[str], bool | None] | None,
    rules: Rules | None,
    today: date | None,
    now: datetime,
) -> None:
    touched, seen = _collect(session, connectors, run_id, stats, now)

    if opts.check_closed and checker is not None:
        try:
            stats["closed_by_check"] = check_open_postings(
                session, checker, run_id=run_id, cap=opts.check_cap, exclude_ids=seen, now=now
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            stats["errors"].append({"source": "closed-check", "error": error_label(exc)})

    _run_filters(session, rules or load_rules(), touched, stats, today)

    diff: Diff = diff_for_run(session, run_id)
    for kind, n in diff.counts().items():
        stats[kind] = n
    stats["rejected"] = sum(1 for p in diff.new if p.filter_state == "rejected")
    stats["flagged"] = sum(1 for p in diff.new if p.filter_state == "flagged")
    run = session.get(Run, run_id)
    if run is not None:
        run.stats = dict(stats)
        session.add(run)
    session.commit()
