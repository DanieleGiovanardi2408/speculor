"""Before/after snapshots of packs for ``radar packs regenerate`` (written under data/, which is
gitignored; never printed)."""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, col, select

from radar.llm.verify import current_pack_texts
from radar.models import ApplicationPack
from radar.timeutil import utcnow


@dataclass(frozen=True)
class PackSnapshot:
    markdown: str
    unsupported: int


def pack_ids_for(session: Session, posting_ids: Sequence[int], all_packs: bool) -> list[int]:
    """Posting ids to regenerate: the given ones, or every posting that has a pack."""
    if all_packs:
        rows = session.exec(
            select(ApplicationPack.posting_id).order_by(col(ApplicationPack.posting_id))
        ).all()
        return list(rows)
    return list(dict.fromkeys(posting_ids))


def snapshot(session: Session, posting_id: int) -> PackSnapshot | None:
    pack = session.exec(
        select(ApplicationPack).where(ApplicationPack.posting_id == posting_id)
    ).first()
    if pack is None:
        return None
    lines = [f"# Pack for posting {posting_id} ({pack.provider} {pack.model})", ""]
    questions = [a.get("question", "") for a in pack.form_answers or []]
    for tid, text in current_pack_texts(session, pack):
        title = tid
        if tid.startswith("form_answers."):
            n = int(tid.split(".")[1])
            title = f"{tid}: {questions[n - 1] if n <= len(questions) else ''}"
        lines += [f"## {title}", "", text.strip(), ""]
    v = pack.verification if isinstance(pack.verification, dict) else {}
    counts = v.get("counts") or {}
    lines += [
        "## Fact check",
        "",
        f"status: {v.get('status', 'none')}; supported {counts.get('supported', 0)}, "
        f"unsupported {counts.get('unsupported', 0)}, check {counts.get('check', 0)}",
        "",
    ]
    for c in v.get("claims") or []:
        if c.get("verdict") != "supported":
            reason = f" ({c['reason']})" if c.get("reason") else ""
            lines.append(f"- {c.get('verdict')} [{c.get('text_id')}] {c.get('claim')}{reason}")
    return PackSnapshot("\n".join(lines).rstrip() + "\n", int(counts.get("unsupported", 0)))


def prepare_dir(target: Path) -> Path:
    run_dir = target / utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_diff(
    run_dir: Path, posting_id: int, before: PackSnapshot | None, after: PackSnapshot | None
) -> Path:
    """Write before.md / after.md and a unified diff; return the diff path."""
    old = before.markdown if before else ""
    new = after.markdown if after else ""
    (run_dir / f"posting-{posting_id}.before.md").write_text(old, encoding="utf-8")
    (run_dir / f"posting-{posting_id}.after.md").write_text(new, encoding="utf-8")
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"posting-{posting_id}.before.md",
        tofile=f"posting-{posting_id}.after.md",
    )
    path = run_dir / f"posting-{posting_id}.diff"
    path.write_text("".join(diff), encoding="utf-8")
    return path
