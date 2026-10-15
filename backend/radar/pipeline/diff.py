"""Daily diff: which postings are new / changed / closed / reopened (from change events)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session, col, select

from radar.models import ChangeEvent, Posting

KINDS = ("new", "changed", "closed", "reopened")


@dataclass
class Diff:
    new: list[Posting] = field(default_factory=list)
    changed: list[Posting] = field(default_factory=list)
    closed: list[Posting] = field(default_factory=list)
    reopened: list[Posting] = field(default_factory=list)
    #: posting id -> merged {field: [old, new]} for changed postings
    changes: dict[int, dict[str, Any]] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {k: len(getattr(self, k)) for k in KINDS}

    def is_empty(self) -> bool:
        return not any(self.counts().values())


def _build(session: Session, events: Sequence[ChangeEvent]) -> Diff:
    diff = Diff()
    seen: dict[str, set[int]] = {k: set() for k in KINDS}
    cache: dict[int, Posting | None] = {}
    for ev in events:
        if ev.kind not in seen:
            continue
        if ev.posting_id not in cache:
            cache[ev.posting_id] = session.get(Posting, ev.posting_id)
        posting = cache[ev.posting_id]
        if posting is None or posting.duplicate_of is not None:
            continue
        if ev.kind == "changed":
            merged = diff.changes.setdefault(ev.posting_id, {})
            for key, value in (ev.changes or {}).items():
                if key in merged and isinstance(merged[key], list) and isinstance(value, list):
                    merged[key] = [merged[key][0], value[-1]]
                else:
                    merged[key] = value
        if ev.posting_id in seen[ev.kind]:
            continue
        seen[ev.kind].add(ev.posting_id)
        getattr(diff, ev.kind).append(posting)
    # a posting both new and changed in the same window is just new
    diff.changed = [p for p in diff.changed if p.id not in seen["new"]]
    return diff


def diff_for_run(session: Session, run_id: int) -> Diff:
    events = session.exec(
        select(ChangeEvent).where(ChangeEvent.run_id == run_id).order_by(col(ChangeEvent.id))
    ).all()
    return _build(session, events)


def diff_since(session: Session, since: datetime) -> Diff:
    events = session.exec(
        select(ChangeEvent).where(col(ChangeEvent.at) >= since).order_by(col(ChangeEvent.id))
    ).all()
    return _build(session, events)
