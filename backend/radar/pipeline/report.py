"""Markdown daily report (private: may contain titles and company names).

Written to ``data/reports/YYYY-MM-DD.md`` and stored in ``Run.report_md``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from radar.models import Application, FilterResult, LLMUsage, Posting, Run, Score
from radar.pipeline.diff import Diff
from radar.timeutil import today as utc_today

PRIORITY_ORDER = ("Alta", "Media", "Bassa")
FOLLOWUP_DAYS = 14
DEADLINE_DAYS = 14


def _md(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"([\\`*_\[\]<>|])", r"\\\1", text)


def _quote(text: str | None) -> str:
    return "“" + _md(text) + "”" if text else ""


def latest_scores(session: Session, ids: Iterable[int]) -> dict[int, Score]:
    wanted = list(set(ids))
    if not wanted:
        return {}
    out: dict[int, Score] = {}
    rows = session.exec(
        select(Score)
        .where(col(Score.posting_id).in_(wanted))
        .order_by(col(Score.created_at), col(Score.id))
    ).all()
    for score in rows:
        out[score.posting_id] = score  # later rows win
    return out


def filter_hits(session: Session, ids: Iterable[int]) -> dict[int, list[FilterResult]]:
    wanted = list(set(ids))
    out: dict[int, list[FilterResult]] = defaultdict(list)
    if not wanted:
        return out
    for row in session.exec(
        select(FilterResult)
        .where(col(FilterResult.posting_id).in_(wanted))
        .order_by(col(FilterResult.id))
    ).all():
        out[row.posting_id].append(row)
    return out


def _headline(p: Posting, *, deadline: bool = True) -> str:
    where = f" ({_md(p.location)})" if p.location else ""
    link = f" · [link]({p.url})" if p.url.startswith("http") else ""
    due = f" · deadline {p.deadline.isoformat()}" if p.deadline and deadline else ""
    return f"**{_md(p.title)}** — {_md(p.company_name)}{where}{due}{link}"


def _hit_line(hit: FilterResult) -> str:
    evidence = f" — {_quote(hit.evidence)}" if hit.evidence else ""
    over = " (overridden)" if hit.overridden else ""
    return f"{_md(hit.reason)} (`{hit.rule_id}`){over}{evidence}"


def _source_table(stats: dict[str, Any]) -> list[str]:
    sources: dict[str, Any] = stats.get("sources") or {}
    if not sources:
        return ["_No sources ran._"]
    lines = ["| Source | Boards | Fetched | New | Errors |", "|---|---:|---:|---:|---:|"]
    for name in sorted(sources):
        s = sources[name]
        lines.append(
            f"| {_md(name)} | {s.get('boards', 0)} | {s.get('fetched', 0)} "
            f"| {s.get('new', 0)} | {s.get('errors', 0)} |"
        )
    return lines


def _section(title: str, body: Sequence[str], empty: str = "_None._") -> list[str]:
    return [f"## {title}", "", *(body or [empty]), ""]


def render_report(session: Session, run: Run, diff: Diff, today: date | None = None) -> str:
    today = today or utc_today()
    stats: dict[str, Any] = run.stats or {}
    new_ids = [p.id for p in diff.new if p.id is not None]
    scores = latest_scores(session, new_ids)
    hits = filter_hits(session, new_ids)

    passed = [p for p in diff.new if p.filter_state in ("pass", "overridden")]
    flagged = [p for p in diff.new if p.filter_state == "flagged"]
    rejected = [p for p in diff.new if p.filter_state == "rejected"]

    out: list[str] = [f"# Radar daily report — {today.isoformat()}", ""]
    started = run.started_at.strftime("%H:%M") if run.started_at else "?"
    out += [f"Run #{run.id} · status **{run.status}** · started {started} UTC", ""]
    c = diff.counts()
    out += [
        f"New **{c['new']}** (passed {len(passed)}, flagged {len(flagged)}, "
        f"rejected {len(rejected)})"
        f" · changed {c['changed']} · closed {c['closed']} · reopened {c['reopened']}",
        "",
    ]
    out += _section("Sources", _source_table(stats))

    # New, passed: grouped by priority when scored
    body: list[str] = []
    groups: dict[str, list[Posting]] = defaultdict(list)
    for p in passed:
        score = scores.get(p.id or -1)
        groups[score.priority if score else "Not scored yet"].append(p)
    for prio in (*PRIORITY_ORDER, "Not scored yet"):
        if not groups.get(prio):
            continue
        body += [f"### {prio}", ""]
        for p in groups[prio]:
            body.append(f"- {_headline(p)}")
            score = scores.get(p.id or -1)
            if score is not None:
                if score.why_fits:
                    body.append(f"  - Why: {_md(score.why_fits)}")
                if score.what_doesnt_fit:
                    body.append(f"  - What doesn't fit: {_md(score.what_doesnt_fit)}")
            for hit in hits.get(p.id or -1, []):
                body.append(f"  - Note: {_hit_line(hit)}")
        body.append("")
    out += _section("New postings that passed the filters", body)

    body = []
    for p in flagged:
        body.append(f"- {_headline(p)}")
        score = scores.get(p.id or -1)
        if score is not None:
            body.append(f"  - Priority: {score.priority}. Why: {_md(score.why_fits)}")
        body += [f"  - {_hit_line(h)}" for h in hits.get(p.id or -1, []) if h.action == "flag"]
    out += _section("New flagged", body)

    body = []
    for p in rejected:
        body.append(f"- {_headline(p)}")
        body += [f"  - {_hit_line(h)}" for h in hits.get(p.id or -1, []) if h.action == "reject"]
    out += _section("New rejected", body)

    if diff.changed:
        body = []
        for p in diff.changed:
            fields = ", ".join(sorted((diff.changes.get(p.id or -1) or {}).keys())) or "content"
            body.append(f"- {_headline(p)} — changed: {_md(fields)}")
        out += _section("Changed", body)
    if diff.reopened:
        out += _section("Reopened", [f"- {_headline(p)}" for p in diff.reopened])

    out += _section("Closed since yesterday", [f"- {_headline(p)}" for p in diff.closed])

    horizon = today + timedelta(days=DEADLINE_DAYS)
    upcoming = session.exec(
        select(Posting)
        .where(Posting.status == "open")
        .where(col(Posting.duplicate_of).is_(None))
        .where(col(Posting.filter_state) != "rejected")
        .where(Posting.user_status == "todo")
        .where(col(Posting.deadline).is_not(None))
        .where(col(Posting.deadline) >= today)
        .where(col(Posting.deadline) <= horizon)
        .order_by(col(Posting.deadline))
    ).all()
    out += _section(
        f"Upcoming deadlines (next {DEADLINE_DAYS} days)",
        [
            f"- {p.deadline.isoformat() if p.deadline else ''}: {_headline(p, deadline=False)}"
            for p in upcoming
        ],
    )

    cutoff = today - timedelta(days=FOLLOWUP_DAYS)
    followups = session.exec(
        select(Application)
        .where(Application.stage == "Inviata")
        .where(Application.responded == False)  # noqa: E712
        .where(col(Application.date_sent) <= cutoff)
        .order_by(col(Application.date_sent))
    ).all()
    out += _section(
        "Follow-ups due",
        [
            f"- {_md(a.role)} — {_md(a.company)}: sent {a.date_sent.isoformat()} "
            f"via {_md(a.channel)}"
            f" ({(today - a.date_sent).days} days ago)"
            for a in followups
        ],
    )

    out += _section("LLM usage", _llm_lines(session, run, stats))

    errors: list[dict[str, Any]] = list(stats.get("errors") or [])
    out += _section(
        "Errors",
        [f"- {_md(str(e.get('source', '?')))}: {_md(str(e.get('error', '?')))}" for e in errors],
    )
    return "\n".join(out).rstrip() + "\n"


def _llm_lines(session: Session, run: Run, stats: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    rows = session.exec(select(LLMUsage).where(LLMUsage.run_id == run.id)).all() if run.id else []
    agg: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        a = agg[(r.provider, r.model, r.purpose)]
        a[0] += r.requests
        a[1] += r.input_tokens
        a[2] += r.output_tokens
    if agg:
        lines += [
            "| Provider | Model | Purpose | Requests | Input tokens | Output tokens |",
            "|---|---|---|---:|---:|---:|",
        ]
        for (prov, model, purpose), (req, tin, tout) in sorted(agg.items()):
            lines.append(
                f"| {_md(prov)} | {_md(model)} | {_md(purpose)} | {req} | {tin} | {tout} |"
            )
    llm = stats.get("llm") or {}
    if isinstance(llm, dict) and llm:
        for key in ("scoring", "packs"):
            part = llm.get(key)
            if isinstance(part, dict):
                summary = ", ".join(
                    f"{k} {v}" for k, v in part.items() if isinstance(v, int | float | str)
                )
                lines.append(f"- {key}: {_md(summary)}")
        if llm.get("skipped"):
            lines.append(f"- skipped: {_md(str(llm['skipped']))}")
    return lines


def write_report(
    session: Session,
    run: Run,
    diff: Diff,
    reports_dir: Path | None = None,
    today: date | None = None,
) -> Path:
    """Render, store in ``run.report_md`` and write ``<reports_dir>/YYYY-MM-DD.md``."""
    from radar.config import get_settings

    today = today or utc_today()
    md = render_report(session, run, diff, today)
    run.report_md = md
    session.add(run)
    directory = reports_dir or get_settings().reports_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{today.isoformat()}.md"
    path.write_text(md, encoding="utf-8")
    return path
