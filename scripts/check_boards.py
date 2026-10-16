#!/usr/bin/env python3
"""LOCAL ONLY: probe every ATS board in a companies YAML and print counts.

One board at a time through ``PoliteClient`` (robots.txt, per-host rate limit, cache). For each
active board with a supported ATS it prints: ats, slug, ok/error, jobs (whole board, title
filter off) and kept (jobs whose title passes the effective title filter). Then a summary per
sector and per ATS, and the failures with their reason. Nothing is written to the database.

Usage::

    uv run python scripts/check_boards.py [config/companies.yaml]
        [--ats personio --ats workable] [--sector fintech] [--exclude OLD.yaml] [--json OUT]

``--exclude`` skips boards (same ats + slug) already present in another YAML, e.g. a backup
taken before adding new entries. The output names slugs: keep it on your machine.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from radar.connectors.base import SourceError
from radar.connectors.http import PoliteClient, RobotsDisallowed, title_matches
from radar.connectors.registry import ATS_SOURCES, _title_filter, build_connectors

REPO_ROOT = Path(__file__).resolve().parents[1]


def _key(entry: dict[str, Any]) -> tuple[str, str]:
    return (str(entry.get("ats") or "").lower(), str(entry.get("slug") or ""))


def _boards(cfg: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    skip: set[tuple[str, str]] = set()
    for path in args.exclude or []:
        other = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        skip |= {_key(e) for e in other.get("companies") or [] if isinstance(e, dict)}
    out = []
    for e in cfg.get("companies") or []:
        if not isinstance(e, dict) or not e.get("active", True) or not e.get("slug"):
            continue
        ats, _ = _key(e)
        if ats not in ATS_SOURCES or (args.ats and ats not in args.ats):
            continue
        if args.sector and e.get("sector") not in args.sector:
            continue
        if _key(e) in skip:
            continue
        out.append(e)
    return out


def probe(client: PoliteClient, entry: dict[str, Any], global_filter: Any) -> dict[str, Any]:
    ats, slug = _key(entry)
    tf = _title_filter(entry["title_filter"]) if "title_filter" in entry else global_filter
    # whole board (filter off), no per-posting detail requests
    probe_entry = {**entry, "title_filter": False, "max_details": 0, "active": True}
    row: dict[str, Any] = {
        "ats": ats,
        "slug": slug,
        "name": entry.get("name"),
        "sector": entry.get("sector") or "-",
    }
    conns = build_connectors(client, {"companies": [probe_entry]}, sources=[ats])
    if not conns:
        return {**row, "status": "error", "reason": "malformed entry", "jobs": 0, "kept": 0}
    try:
        raws = conns[0].fetch()
    except RobotsDisallowed:
        return {**row, "status": "robots", "reason": "robots.txt disallows", "jobs": 0, "kept": 0}
    except SourceError as exc:
        return {**row, "status": "error", "reason": str(exc), "jobs": 0, "kept": 0}
    except Exception as exc:  # report and carry on with the next board
        return {**row, "status": "error", "reason": type(exc).__name__, "jobs": 0, "kept": 0}
    kept = sum(1 for r in raws if title_matches(r.title, tf))
    return {**row, "status": "ok", "reason": "", "jobs": len(raws), "kept": kept}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", default=str(REPO_ROOT / "config" / "companies.yaml"))
    ap.add_argument("--ats", action="append", help="only these ATS types (repeatable)")
    ap.add_argument("--sector", action="append", help="only these sectors (repeatable)")
    ap.add_argument("--exclude", action="append", help="skip boards listed in this YAML")
    ap.add_argument("--json", help="also write the rows to this JSON file")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.path).read_text(encoding="utf-8")) or {}
    global_filter = _title_filter(cfg.get("title_filter"))
    boards = _boards(cfg, args)
    rows: list[dict[str, Any]] = []
    with PoliteClient.from_settings() as client:
        print(f"{'ats':<14} {'slug':<42} {'status':<7} {'jobs':>5} {'kept':>5}")
        for entry in boards:
            row = probe(client, entry, global_filter)
            rows.append(row)
            slug = row["slug"] if len(row["slug"]) <= 42 else row["slug"][:39] + "..."
            print(
                f"{row['ats']:<14} {slug:<42} {row['status']:<7} {row['jobs']:>5} {row['kept']:>5}"
                + (f"  {row['reason']}" if row["reason"] else ""),
                flush=True,
            )
        requests = client.network_requests

    ok = [r for r in rows if r["status"] == "ok"]
    print(
        f"\nboards: {len(rows)}  ok: {len(ok)}  failed: {len(rows) - len(ok)}  requests: {requests}"
    )
    for field in ("sector", "ats"):
        agg: dict[str, Counter[str]] = defaultdict(Counter)
        for r in rows:
            c = agg[r[field]]
            c["boards"] += 1
            c["ok"] += r["status"] == "ok"
            c["empty"] += r["status"] == "ok" and r["jobs"] == 0
            c["jobs"] += r["jobs"]
            c["kept"] += r["kept"]
        print(f"\nper {field}:")
        print(f"  {field:<18} {'boards':>6} {'ok':>4} {'empty':>5} {'jobs':>6} {'kept':>5}")
        for name, c in sorted(agg.items()):
            print(
                f"  {name:<18} {c['boards']:>6} {c['ok']:>4} {c['empty']:>5} "
                f"{c['jobs']:>6} {c['kept']:>5}"
            )
    failures = [r for r in rows if r["status"] != "ok"]
    if failures:
        print("\nfailures:")
        for r in failures:
            print(f"  {r['ats']:<14} {r['slug']:<42} {r['reason']}")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
