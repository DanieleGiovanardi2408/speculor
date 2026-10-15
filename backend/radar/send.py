"""`radar send`: send APPROVED LinkedIn drafts from the user's own computer, one at a time.

Human in the loop by design:

* refuses to run in CI (``CI`` / ``GITHUB_ACTIONS`` set);
* never sends unless the user types an explicit "s" (or "si"/"sì"/"y"/"yes") for that draft;
* sends only by calling the user's own ``linkedin`` CLI (mayai-linkedin-cli) as a subprocess,
  so its throttle, daily quotas and session handling apply unchanged.

linkedin-cli v0.2 commands used (verified from its source)::

    linkedin --json auth status
    linkedin --json messages send [--dry-run] -- RECIPIENT TEXT
    linkedin --json connections send [--dry-run] -- PROFILE_ID
    linkedin --json profile get PUBLIC_ID          # only to resolve a member id

Notes on v0.2 behaviour:

* ``connections send`` cannot attach a note: the note is shown so the user can paste it by hand.
* ``messages send`` needs a numeric member id or ``urn:li:member:N``; a public id / profile URL
  is resolved with ``profile get`` (read-only) after the user has confirmed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from sqlalchemy import Engine
from sqlmodel import select

from radar.config import get_settings
from radar.db import session_scope
from radar.models import Contact, Draft, Posting
from radar.timeutil import utcnow

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
InputFn = Callable[[str], str]
EditFn = Callable[[str], str | None]

YES = frozenset({"s", "si", "sì", "y", "yes"})
MESSAGE_KINDS = frozenset({"linkedin_message", "followup"})
CONNECTION_KIND = "linkedin_connection_note"
CLI_TIMEOUT_S = 180
_QUOTA_RE = re.compile(r"quota|rate.?limit|too many requests|\b429\b", re.IGNORECASE)
_PROFILE_URL_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.IGNORECASE)
_MEMBER_RE = re.compile(r"^(urn:li:member:\d+|\d+)$")


@dataclass
class DraftView:
    id: int
    kind: str
    recipient: str | None
    text: str
    company: str | None
    title: str | None
    contact_name: str | None
    profile_url: str | None


@dataclass
class CliResult:
    ok: bool
    data: dict[str, Any]
    error: str
    quota: bool = False


def _in_ci() -> bool:
    return any(os.environ.get(var) for var in ("CI", "GITHUB_ACTIONS"))


def _parse_ndjson(stdout: str) -> dict[str, Any]:
    """Return the last JSON object printed on stdout (linkedin-cli prints NDJSON)."""
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _run_cli(runner: Runner, args: Sequence[str]) -> CliResult:
    cmd = [get_settings().linkedin_bin, "--json", *args]
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT_S, check=False)
    except FileNotFoundError:
        return CliResult(False, {}, f"linkedin-cli not found ({cmd[0]!r}); set RADAR_LINKEDIN_BIN")
    except subprocess.TimeoutExpired:
        return CliResult(False, {}, "timeout: check on LinkedIn whether it was sent")
    data = _parse_ndjson(proc.stdout)
    stderr = (proc.stderr or "").strip()
    error = stderr.removeprefix("error:").strip() if stderr else ""
    if proc.returncode != 0:
        error = error or f"exit code {proc.returncode}"
        return CliResult(False, data, error, quota=bool(_QUOTA_RE.search(error)))
    return CliResult(True, data, "")


def _public_id(value: str) -> str:
    """Profile URL -> public id; anything else is returned stripped."""
    value = value.strip()
    match = _PROFILE_URL_RE.search(value)
    return match.group(1) if match else value.rstrip("/")


def _load_queue(engine: Engine | None) -> list[DraftView]:
    with session_scope(engine) as s:
        drafts = s.exec(
            select(Draft)
            .where(Draft.status == "approved", Draft.channel == "linkedin")
            .order_by(Draft.approved_at, Draft.created_at, Draft.id)  # type: ignore[arg-type]
        ).all()
        views: list[DraftView] = []
        for d in drafts:
            posting = s.get(Posting, d.posting_id) if d.posting_id else None
            contact = s.get(Contact, d.contact_id) if d.contact_id else None
            assert d.id is not None
            views.append(
                DraftView(
                    id=d.id,
                    kind=d.kind,
                    recipient=d.recipient or (contact.profile_url if contact else None),
                    text=d.text,
                    company=posting.company_name if posting else None,
                    title=posting.title if posting else None,
                    contact_name=contact.name if contact else None,
                    profile_url=contact.profile_url if contact else None,
                )
            )
        return views


def _update_draft(engine: Engine | None, draft_id: int, **fields: Any) -> None:
    with session_scope(engine) as s:
        draft = s.get(Draft, draft_id)
        if draft is None:
            return
        for key, value in fields.items():
            setattr(draft, key, value)
        draft.updated_at = utcnow()
        s.add(draft)


def _short(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _default_edit(text: str) -> str | None:
    import click

    return click.edit(text)


def _default_clipboard(text: str) -> bool:
    for tool in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(tool[0]):
            try:
                subprocess.run(tool, input=text, text=True, check=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                return False
            return True
    return False


def _profile_link(view: DraftView) -> str | None:
    source = view.profile_url or view.recipient
    if not source:
        return None
    if source.startswith("http"):
        return source
    if source.startswith("urn:") or source.isdigit():
        return None
    return f"https://www.linkedin.com/in/{_public_id(source)}/"


def _show(console: Console, view: DraftView, index: int, total: int, dry_run: bool) -> None:
    header = Text()
    header.append(f"{view.company or '—'}", style="bold")
    header.append(f" · {view.title or '—'}\n")
    header.append(f"Contatto: {view.contact_name or '—'}\n")
    header.append(f"Destinatario: {view.recipient or '(mancante)'}\n")
    header.append(f"Tipo: {view.kind} · {len(view.text)} caratteri\n\n")
    header.append(view.text)
    title = f"Bozza #{view.id} ({index}/{total})" + (" · DRY RUN" if dry_run else "")
    console.print(Panel(header, title=title, expand=False))
    if view.kind == CONNECTION_KIND:
        console.print(
            "[yellow]Attenzione:[/yellow] linkedin-cli v0.2 non può allegare una nota alla "
            "richiesta di collegamento. Con [bold]s[/bold] parte la richiesta SENZA nota; "
            "con [bold]c[/bold] copio la nota e ti mostro il link al profilo per inviarla a mano."
        )


def _resolve_member(runner: Runner, recipient: str) -> tuple[str | None, str]:
    """Return (member id or URN, error). Public ids / URLs are resolved via ``profile get``."""
    value = recipient.strip()
    if _MEMBER_RE.match(value):
        return value, ""
    public_id = _public_id(value)
    if _MEMBER_RE.match(public_id):
        return public_id, ""
    res = _run_cli(runner, ["profile", "get", public_id])
    if not res.ok:
        return None, f"profile get failed: {res.error}"
    member = str(res.data.get("member_id") or "")
    if not member:
        return None, "member id not found: put the numeric id or urn:li:member:N as recipient"
    return member, ""


def send_approved(
    dry_run: bool = False,
    *,
    runner: Runner = subprocess.run,
    input_fn: InputFn = input,
    engine: Engine | None = None,
    edit_fn: EditFn = _default_edit,
    clipboard_fn: Callable[[str], bool] = _default_clipboard,
    console: Console | None = None,
) -> dict[str, Any]:
    """Interactively send approved LinkedIn drafts. Returns counters (see ``stats``)."""
    console = console or Console()
    if _in_ci():
        console.print(
            "[red]radar send non gira in CI/automazione.[/red] Si usa solo dal tuo computer, "
            "una bozza alla volta, con conferma esplicita."
        )
        raise SystemExit(2)

    stats: dict[str, Any] = {
        "total": 0,
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "deferred": 0,
        "manual": 0,
        "dry_run": dry_run,
        "stopped": None,
    }

    auth = _run_cli(runner, ["auth", "status"])
    if not (auth.ok and auth.data.get("authenticated") is True):
        if dry_run:
            console.print("[yellow]linkedin-cli non autenticato (ok in dry-run).[/yellow]")
        else:
            reason = auth.error or "not authenticated"
            console.print(
                f"[red]linkedin-cli non autenticato[/red] ({reason}). "
                "Esegui [bold]linkedin auth login[/bold] e riprova."
            )
            stats["stopped"] = "unauthenticated"
            return stats

    queue = _load_queue(engine)
    stats["total"] = len(queue)
    if not queue:
        console.print("Nessuna bozza LinkedIn approvata da inviare.")
        return stats

    prompt = "Inviare? [s]ì / [n]o (salta per ora) / [k] skip definitivo / [e] modifica / [q] esci"
    for index, view in enumerate(queue, start=1):
        while True:
            _show(console, view, index, len(queue), dry_run)
            options = prompt + (
                " / [c] copia e apri profilo" if view.kind == CONNECTION_KIND else ""
            )
            try:
                answer = input_fn(f"{options} (default: n): ").strip().lower()
            except EOFError:
                answer = "q"
            if answer == "e":
                edited = edit_fn(view.text)
                if edited is not None and edited.strip() and edited.strip() != view.text:
                    view.text = edited.strip()
                    if not dry_run:
                        _update_draft(engine, view.id, text=view.text)
                    console.print("Testo aggiornato.")
                continue
            break

        if answer == "q":
            stats["stopped"] = "user"
            console.print("Interrotto.")
            break
        if answer == "k":
            stats["skipped"] += 1
            if not dry_run:
                _update_draft(engine, view.id, status="skipped")
            console.print("Saltata definitivamente.")
            continue
        if answer == "c" and view.kind == CONNECTION_KIND:
            copied = clipboard_fn(view.text)
            link = _profile_link(view) or "(link al profilo non disponibile)"
            console.print(("Nota copiata negli appunti. " if copied else "") + f"Profilo: {link}")
            done = input_fn("Segnare come inviata a mano? [s/N]: ").strip().lower()
            if done in YES and not dry_run:
                _update_draft(
                    engine, view.id, status="sent", sent_at=utcnow(), send_result="manual"
                )
                stats["manual"] += 1
            else:
                stats["deferred"] += 1
            continue
        if answer not in YES:
            stats["deferred"] += 1
            console.print("Non inviata (resta approvata).")
            continue

        if not view.recipient:
            stats["failed"] += 1
            console.print("[red]Destinatario mancante.[/red]")
            if not dry_run:
                _update_draft(engine, view.id, status="failed", send_result="missing recipient")
            continue

        flag = ["--dry-run"] if dry_run else []
        if view.kind in MESSAGE_KINDS:
            target: str | None = view.recipient
            error = ""
            if not dry_run:
                target, error = _resolve_member(runner, view.recipient)
            if target is None:
                res = CliResult(False, {}, error)
            else:
                res = _run_cli(runner, ["messages", "send", *flag, "--", target, view.text])
        elif view.kind == CONNECTION_KIND:
            # v0.2 has no note option: the note text is NEVER part of the command.
            profile_id = _public_id(view.recipient)
            res = _run_cli(runner, ["connections", "send", *flag, "--", profile_id])
        else:
            res = CliResult(False, {}, f"unsupported kind {view.kind!r}")

        if res.ok:
            stats["sent"] += 1
            if dry_run:
                console.print(f"[cyan]dry-run:[/cyan] {_short(res.data)}")
            else:
                _update_draft(
                    engine, view.id, status="sent", sent_at=utcnow(), send_result=_short(res.data)
                )
                console.print("[green]Inviata.[/green]")
            continue

        if res.quota:
            console.print(f"[red]Limite/quota linkedin-cli raggiunto:[/red] {res.error}. Mi fermo.")
            stats["stopped"] = "quota"
            break
        stats["failed"] += 1
        console.print(f"[red]Errore:[/red] {res.error}")
        if not dry_run:
            _update_draft(engine, view.id, status="failed", send_result=_short(res.error))

    console.print(
        f"Fatto: {stats['sent']} inviate, {stats['manual']} a mano, {stats['failed']} fallite, "
        f"{stats['skipped']} saltate, {stats['deferred']} rimandate"
        + (" (dry-run: nessuno stato modificato)" if dry_run else "")
    )
    return stats


if __name__ == "__main__":  # pragma: no cover
    send_approved(dry_run="--dry-run" in sys.argv)
