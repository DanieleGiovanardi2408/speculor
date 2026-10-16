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
) -> None:
    """Run the daily pipeline: collect, dedupe, check closed, filter, diff, score, packs, report."""
    from radar.pipeline.run import PipelineOptions, run_pipeline

    opts = PipelineOptions(
        sources=[x.strip() for x in sources.split(",")] if sources else None,
        llm=llm,
        max_score=max_score,
        max_packs=max_packs,
        check_closed=check_closed,
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
    """Copy config/{profile,companies,rules}.yaml into the settings table (for CI)."""
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


if __name__ == "__main__":
    app()
