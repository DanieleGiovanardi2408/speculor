"""`radar` command line. Heavy modules are imported lazily inside each command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from radar.config import REPO_ROOT, get_settings

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="Radar: internship hunting, human in the loop."
)
db_app = typer.Typer(no_args_is_help=True, help="Database utilities.")
config_app = typer.Typer(
    no_args_is_help=True, help="Store gitignored config files in the database."
)
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
connectors_app = typer.Typer(no_args_is_help=True, help="Connector pause state.")
app.add_typer(connectors_app, name="connectors")
imap_app = typer.Typer(
    no_args_is_help=True, help="Email-alert mailbox: login (Outlook), app password (Gmail), check."
)
app.add_typer(imap_app, name="imap")
contacts_app = typer.Typer(
    no_args_is_help=True, help="Public contact search (budget-capped, results need review)."
)
app.add_typer(contacts_app, name="contacts")
console = Console()


@app.command("init-db")
def init_db_cmd() -> None:
    """Create all tables."""
    from radar.db import init_db

    init_db()
    console.print("[green]Database ready.[/green]")


@app.command()
def seed(
    path: Annotated[Path, typer.Argument()] = REPO_ROOT / "private" / "radar_seed.json",
) -> None:
    """Import the manual tracker export (radar_seed.json)."""
    from radar.db import session_scope
    from radar.seed import import_seed

    if not path.exists():
        console.print(f"[red]Seed file not found:[/red] {path}")
        raise typer.Exit(1)
    with session_scope() as s:
        counts = import_seed(s, path)
    console.print(f"Imported: {counts}")


@app.command()
def demo() -> None:
    """Load fictional demo data (for local mode and screenshots)."""
    from radar.db import session_scope
    from radar.demo import load_demo

    with session_scope() as s:
        counts = load_demo(s)
    console.print(f"Demo data loaded: {counts}")


@app.command()
def run(
    sources: Annotated[str | None, typer.Option(help="Comma-separated sources to run")] = None,
    llm: Annotated[bool, typer.Option("--llm/--no-llm", help="Run LLM scoring and packs")] = True,
    max_score: Annotated[int, typer.Option(help="Max postings to score this run")] = 60,
    max_packs: Annotated[int, typer.Option(help="Max application packs this run")] = 5,
    check_closed: Annotated[bool, typer.Option("--check-closed/--no-check-closed")] = True,
    rank: Annotated[
        bool, typer.Option("--rank/--no-rank", help="Pre-rank by local embedding similarity")
    ] = True,
    contacts: Annotated[
        bool, typer.Option("--contacts/--no-contacts", help="Public contact search (capped)")
    ] = True,
) -> None:
    """Run the daily pipeline: collect, dedupe, check closed, filter, diff, score, packs,
    contacts, report."""
    from radar.pipeline.run import PipelineOptions, run_pipeline

    opts = PipelineOptions(
        sources=[x.strip() for x in sources.split(",")] if sources else None,
        llm=llm,
        max_score=max_score,
        max_packs=max_packs,
        check_closed=check_closed,
        rank=rank,
        contacts=contacts,
    )
    result = run_pipeline(opts)
    console.print(result.summary_line())
    if result.report_path:
        console.print(f"Report: {result.report_path}")


@app.command()
def score(limit: Annotated[int, typer.Option()] = 60) -> None:
    """Score unscored postings that passed the filters."""
    from radar.db import session_scope
    from radar.llm.scoring import score_pending

    with session_scope() as s:
        stats = score_pending(s, run_id=None, limit=limit)
    console.print(stats)


@app.command()
def rank(top: Annotated[int, typer.Option(help="How many top titles to print")] = 10) -> None:
    """Compute or refresh local similarity to your profile (needs the `embed` extra)."""
    from radar.db import session_scope
    from radar.pipeline.rank import rank_postings, top_postings

    with session_scope() as s:
        stats = rank_postings(s)
        if "skipped" in stats:
            console.print(f"[yellow]Ranking skipped:[/yellow] {stats['skipped']}")
            return
        console.print(
            f"Ranked {stats['ranked']} postings: {stats['embedded']} embedded, "
            f"{stats['cached']} from cache, {stats['updated']} updated ({stats['model']})"
        )
        for p in top_postings(s, top):
            console.print(f"  {p.similarity:.2f}  {p.title}")


@app.command()
def rescore(
    flagged_language: Annotated[
        bool,
        typer.Option(
            "--flagged-language",
            help="Postings with a language finding (posting_language / language_required)",
        ),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only count, change nothing")] = False,
    limit: Annotated[int | None, typer.Option(help="Re-score at most N postings")] = None,
) -> None:
    """Delete stale LLM scores and score again (run `radar refilter` first)."""
    from radar.db import session_scope
    from radar.llm.scoring import rescore_flagged_language

    if not flagged_language:
        console.print("Specify what to re-score, e.g. --flagged-language.")
        raise typer.Exit(2)
    with session_scope() as s:
        stats = rescore_flagged_language(s, run_id=None, dry_run=dry_run, limit=limit)
    console.print(stats)


@app.command()
def packs(
    limit: Annotated[int, typer.Option()] = 5,
    posting_id: Annotated[int | None, typer.Option(help="Build the pack for one posting")] = None,
) -> None:
    """Build application packs for Alta postings that do not have one yet."""
    from radar.db import session_scope
    from radar.llm.packs import build_packs

    with session_scope() as s:
        stats = build_packs(s, run_id=None, limit=limit, posting_id=posting_id)
    console.print(stats)


@app.command()
def refilter() -> None:
    """Re-apply config/rules.yaml to every posting (keeps manual overrides)."""
    from radar.db import session_scope
    from radar.pipeline.filters import apply_filters_all

    with session_scope() as s:
        stats = apply_filters_all(s)
    console.print(stats)


@app.command("add-url")
def add_url(url: str) -> None:
    """Manually add a posting by URL (fetch + parse)."""
    from radar.db import session_scope
    from radar.pipeline.manual import add_posting_from_url

    with session_scope() as s:
        posting = add_posting_from_url(s, url)
    console.print(f"Added posting #{posting.id}")


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option()] = False,
) -> None:
    """Start the local API."""
    import uvicorn

    uvicorn.run("radar.api.main:app", host=host, port=port, reload=reload)


@app.command()
def send(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show drafts, send nothing")] = False,
) -> None:
    """Send APPROVED LinkedIn drafts, one by one, each only after you confirm. Local only."""
    from radar.send import send_approved

    result = send_approved(dry_run=dry_run)
    if result.get("stopped") in {"unauthenticated", "quota"}:
        raise typer.Exit(1)


@connectors_app.command("status")
def connectors_status() -> None:
    """Show paused sources and what to do."""
    from radar.connectors.status import all_status
    from radar.db import session_scope

    with session_scope() as s:
        status = all_status(s)
    if not status:
        console.print("Nessuna fonte in pausa.")
    for source, info in status.items():
        console.print(f"[bold]{source}[/]: {info.get('reason')} -> {info.get('action')}")


@connectors_app.command("resume")
def connectors_resume(source: str) -> None:
    """Resume a paused source (after you fixed the cause, e.g. logged in again)."""
    from radar.connectors.status import resume
    from radar.db import session_scope

    with session_scope() as s:
        ok = resume(s, source)
    console.print("Ripresa." if ok else "Fonte non in pausa.")


def _imap_user() -> str:
    user = (get_settings().imap_user or "").strip()
    if not user:
        console.print("Imposta prima RADAR_IMAP_USER nel file .env.")
        raise typer.Exit(2)
    return user


def _resume_email() -> None:
    """The user just fixed the credentials: lift a pause of the "email" source, if any."""
    try:
        from radar.connectors.status import resume
        from radar.db import session_scope

        with session_scope() as s:
            if resume(s, "email"):
                console.print("Fonte email riattivata.")
    except Exception:  # no DB yet: nothing to resume
        pass


@imap_app.command("status")
def imap_status() -> None:
    """Show the IMAP setup (mode, host, folder, which secrets exist; never their values)."""
    from radar.connectors.imap_auth import (
        default_host,
        effective_mode,
        has_oauth_token,
        stored_password,
    )

    s = get_settings()
    user = (s.imap_user or "").strip()
    mode = effective_mode(s.imap_auth, user)
    console.print(f"utente impostato: {'sì' if user else 'no'}")
    console.print(f"modalità: {mode} (RADAR_IMAP_AUTH={s.imap_auth})")
    console.print(f"host: {s.imap_host or default_host(user) or '(manca RADAR_IMAP_HOST)'}")
    console.print(f"cartella: {s.imap_folder}")
    if not user:
        return
    if mode == "oauth2":
        console.print(f"client id: {'impostato' if s.imap_oauth_client_id else 'MANCANTE'}")
        console.print(f"tenant: {s.imap_oauth_tenant}")
        console.print(f"token nel portachiavi: {'sì' if has_oauth_token(user) else 'no'}")
    else:
        env = bool(s.imap_password and s.imap_password.get_secret_value())
        console.print(f"password in .env: {'sì' if env else 'no'}")
        console.print(f"password nel portachiavi: {'sì' if stored_password(user) else 'no'}")


@imap_app.command("login")
def imap_login() -> None:
    """Outlook.com (OAuth2): sign in once with the device code; the token goes to the keychain."""
    from radar.connectors.imap_auth import MsalTokens

    s = get_settings()
    user = _imap_user()
    client_id = (s.imap_oauth_client_id or "").strip()
    if not client_id:
        console.print("Imposta RADAR_IMAP_OAUTH_CLIENT_ID (vedi docs/RUNNING.md, Email alerts).")
        raise typer.Exit(2)
    ok = MsalTokens(client_id, user, s.imap_oauth_tenant).device_login(console.print)
    if not ok:
        raise typer.Exit(1)
    console.print("Token salvato nel portachiavi. Prova con `radar imap check`.")
    _resume_email()


@imap_app.command("set-password")
def imap_set_password() -> None:
    """Gmail (or other password mode): store the app password in the macOS keychain."""
    from radar.connectors.imap_auth import store_password

    user = _imap_user()
    secret = typer.prompt("Password per app", hide_input=True).replace(" ", "").strip()
    if not secret:
        raise typer.Exit(2)
    store_password(user, secret)
    console.print("Password salvata nel portachiavi (puoi togliere RADAR_IMAP_PASSWORD da .env).")
    _resume_email()


@imap_app.command("logout")
def imap_logout() -> None:
    """Delete the stored IMAP password and OAuth token from the keychain."""
    from radar.connectors.imap_auth import forget

    removed = forget(_imap_user())
    console.print(f"Rimossi: {', '.join(removed)}." if removed else "Niente da rimuovere.")


@imap_app.command("check")
def imap_check() -> None:
    """Connect read-only and count alert emails in the folder (counts only, no content)."""
    from datetime import date, timedelta

    from radar.connectors.base import SourceError
    from radar.connectors.email_alerts import EmailAlertsConnector, alert_ids
    from radar.connectors.status import SourcePaused

    s = get_settings()
    conn = EmailAlertsConnector.from_settings(s)
    if conn is None:
        console.print("Configurazione IMAP incompleta: esegui `radar imap status`.")
        raise typer.Exit(2)
    since = date.today() - timedelta(days=conn.since_days)
    try:
        with conn.mailbox() as box:
            found = alert_ids(box, since)
    except SourcePaused as exc:
        console.print(f"{exc.reason}: {exc.action}")
        raise typer.Exit(1) from None
    except SourceError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from None
    kinds = {k: sum(1 for _, x in found if x == k) for k in ("linkedin", "jobteaser")}
    console.print(
        f"OK: cartella aperta in sola lettura; ultimi {conn.since_days} giorni: "
        f"{kinds['linkedin']} alert LinkedIn, {kinds['jobteaser']} JobTeaser."
    )


@db_app.command("copy")
def db_copy(
    source: Annotated[str, typer.Option(help="Source database URL")],
    target: Annotated[str, typer.Option(help="Target database URL")],
    replace: Annotated[bool, typer.Option(help="Delete target rows first")] = False,
) -> None:
    """Copy all tables (e.g. local SQLite -> Supabase). Prints row counts only."""
    from sqlalchemy import create_engine

    from radar.db import get_engine
    from radar.dbcopy import copy_database

    counts = copy_database(create_engine(source), get_engine(target), replace=replace)
    console.print({k: v for k, v in counts.items() if v})


@db_app.command("secure")
def db_secure() -> None:
    """Enable row level security on every table of RADAR_DATABASE_URL (Supabase)."""
    from radar.db import get_engine
    from radar.dbcopy import enable_rls

    names = enable_rls(get_engine())
    console.print(f"RLS enabled on {len(names)} tables")


@config_app.command("push")
def config_push() -> None:
    """Copy config/{profile,companies,rules}.yaml into the settings table."""
    import yaml

    from radar.db import session_scope, set_setting

    cfg_dir = get_settings().config_dir
    with session_scope() as s:
        for name in ("profile", "companies", "rules"):
            path = cfg_dir / f"{name}.yaml"
            if path.exists():
                set_setting(s, f"config:{name}", yaml.safe_load(path.read_text("utf-8")))
                console.print(f"stored {name}")


@config_app.command("pull")
def config_pull() -> None:
    """Write config files from the settings table (never overwrites existing files)."""
    import yaml

    from radar.db import get_setting

    cfg_dir = get_settings().config_dir
    for name in ("profile", "companies", "rules"):
        path = cfg_dir / f"{name}.yaml"
        value = get_setting(f"config:{name}")
        if value is not None and not path.exists():
            path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), "utf-8")
            console.print(f"wrote {name}")


@contacts_app.command("find")
def contacts_find(
    posting_id: Annotated[int | None, typer.Option(help="Search contacts for one posting")] = None,
    alta: Annotated[
        bool, typer.Option("--alta", help="Alta postings without contacts (daily limit)")
    ] = False,
    drafts: Annotated[
        bool, typer.Option(help="Also draft a connection note and a message per new contact")
    ] = False,
) -> None:
    """Find 2-3 public profiles per posting. Stored as "da verificare"; nothing is sent."""
    from sqlmodel import col, select

    from radar.db import session_scope
    from radar.llm import contacts as finder
    from radar.models import Contact

    if (posting_id is None) == (not alta):
        console.print("[red]Use either --posting-id ID or --alta.[/red]")
        raise typer.Exit(2)
    with session_scope() as s:
        start = max((c.id or 0 for c in s.exec(select(Contact)).all()), default=0)
        try:
            if posting_id is not None:
                stats = finder.find_contacts(s, posting_id)
            else:
                stats = finder.find_contacts_for_alta(s)
        except finder.BudgetExceeded as exc:
            console.print(f"[yellow]Stopped: {exc}[/yellow]")
            raise typer.Exit(1) from exc
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        console.print(stats)
        if drafts:
            from radar.llm.packs import draft_outreach_for_contact

            new = s.exec(select(Contact).where(col(Contact.id) > start)).all()
            made = 0
            for c in new:
                if c.id is not None and c.posting_id is not None:
                    made += len(draft_outreach_for_contact(s, c.posting_id, c.id))
                    s.commit()
            console.print(f"{made} drafts created (review them in Approvazioni).")
    console.print("Review the contacts in the web app: they are marked 'da verificare'.")


@contacts_app.command("budget")
def contacts_budget() -> None:
    """Estimated contact-search spend this month and calls today."""
    from radar.db import session_scope
    from radar.llm.contacts import budget_status

    with session_scope() as s:
        console.print(budget_status(s).as_dict())


if __name__ == "__main__":
    app()
