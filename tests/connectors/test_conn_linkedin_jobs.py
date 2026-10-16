"""linkedin_jobs connector: fake linkedin-cli subprocess (no network, no real binary)."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from radar.connectors.base import SourceError
from radar.connectors.linkedin_jobs import (
    ACTION_AUTH,
    ACTION_BLOCKED,
    LinkedInJobsConnector,
    available,
    parse_plan,
    to_raw,
)
from radar.connectors.status import SourcePaused
from radar.pipeline.closed import is_exhaustive
from radar.pipeline.normalize import canonical_url

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "linkedin_searches.example.yaml"

PLAN = {
    "max_calls": 6,
    "max_details": 2,
    "locations": {
        "rome": {"geo_id": "100000001"},
        "milan": {"geo_id": "100000002"},
        "paris": {"geo_id": "100000003"},
        "lisbon": {"name": "Lisbon, Portugal"},
    },
    "location_groups": {"italy": ["rome", "milan"]},
    "defaults": {"experience": ["internship", "entry"]},
    "searches": [
        {"id": "ds", "keywords": "data scientist", "locations": ["italy", "paris"]},
        {"id": "va", "keywords": "venture capital analyst", "locations": ["milan", "lisbon"]},
        {"id": "off", "keywords": "ignored", "locations": ["rome"], "active": False},
    ],
}


def _card(job_id: int, listed: str = "2026-10-15T08:00:00Z", **over: Any) -> dict[str, Any]:
    row = {
        "job_id": str(job_id),
        "title": f"Stage Data {job_id}",
        "company": "Nebbia Labs",
        "location": "Milano, Lombardia, Italia (Ibrido)",
        "listed_at": listed,
        "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
        "easy_apply": False,
    }
    row.update(over)
    return row


class FakeCli:
    """Answers like linkedin-cli 0.3 with --json; keeps its own offline quota counter."""

    def __init__(
        self,
        results: dict[str, list[dict[str, Any]]] | None = None,
        *,
        fail: dict[int, tuple[int, str]] | None = None,  # n-th network call -> (code, stderr)
        jobs_used: int = 0,
        jobs_limit: int = 30,
        details: dict[str, dict[str, Any]] | None = None,
        quota_supported: bool = True,
    ) -> None:
        self.results = results or {}
        self.fail = fail or {}
        self.jobs_used = jobs_used
        self.jobs_limit = jobs_limit
        self.details = details or {}
        self.quota_supported = quota_supported
        self.calls: list[list[str]] = []  # network-hitting calls only

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert cmd[:2] == ["linkedin", "--json"]
        assert kwargs.get("timeout") and kwargs.get("check") is False
        args = cmd[2:]
        if args == ["quota"]:
            if not self.quota_supported:
                return subprocess.CompletedProcess(cmd, 2, "", "Error: No such command 'quota'.")
            body = {
                "date": "2026-10-16",
                "jobs": {"used": self.jobs_used, "limit": self.jobs_limit},
                "api_total": {"used": self.jobs_used, "limit": 200},
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(body) + "\n", "")
        self.calls.append(args)
        n = len(self.calls)
        if n in self.fail:
            code, err = self.fail[n]
            if code != 4:
                self.jobs_used += 1
            return subprocess.CompletedProcess(cmd, code, "", f"error: {err}\n")
        if args[:2] == ["jobs", "search"]:
            self.jobs_used += 1 if "--geo-id" in args else 2  # location name: +1 typeahead
            rows = self.results.get(args[-1], [])
            return subprocess.CompletedProcess(
                cmd, 0, "".join(json.dumps(r) + "\n" for r in rows), ""
            )
        if args[:2] == ["jobs", "get"]:
            self.jobs_used += 1
            detail = self.details.get(args[2], {"job_id": args[2], "description": "Testo."})
            return subprocess.CompletedProcess(cmd, 0, json.dumps(detail) + "\n", "")
        raise AssertionError(f"unexpected command {args}")

    def searches(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["jobs", "search"]]


class MemoryState:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.state = dict(initial or {})
        self.saves = 0

    def load(self) -> dict[str, Any]:
        return dict(self.state)

    def save(self, state: dict[str, Any]) -> None:
        self.state = dict(state)
        self.saves += 1


def _conn(
    cli: FakeCli,
    plan: dict[str, Any] | None = None,
    state: MemoryState | None = None,
    known: set[str] | None = None,
    day: date = date(2026, 10, 16),
) -> tuple[LinkedInJobsConnector, MemoryState]:
    st = state or MemoryState()
    conn = LinkedInJobsConnector(
        parse_plan(plan or PLAN),
        runner=cli,
        state=st,
        today=lambda: day,
        known=lambda urls: {u for u in urls if known and u in known},
    )
    return conn, st


@pytest.fixture(autouse=True)
def _not_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def test_plan_round_robin_slots_and_groups() -> None:
    plan = parse_plan(PLAN)
    assert [s.id for s in plan.searches] == ["ds", "va"]
    slots = [(s.id, loc.key) for s, loc in plan.slots()]
    assert slots == [("ds", "rome"), ("va", "milan"), ("ds", "milan"), ("va", "lisbon"),
                     ("ds", "paris")]  # fmt: skip
    assert plan.searches[0].experience == ("internship", "entry")


def test_plan_auto_window_follows_cycle_length() -> None:
    plan = parse_plan(PLAN)  # 6 slots-cost / (6 - 2) calls a day -> 2 days -> week
    assert plan.cycle_days() == 2
    assert plan.window(plan.searches[0]) == "week"
    small = parse_plan({**PLAN, "max_calls": 25, "max_details": 0})
    assert small.window(small.searches[0]) == "24h"
    edge = parse_plan({**PLAN, "max_calls": 1, "max_details": 0})  # cost 6 -> 6 days
    assert edge.window(edge.searches[0]) == "week"
    many = [{"id": f"s{i}", "keywords": f"k{i}", "locations": ["italy"]} for i in range(5)]
    big = parse_plan({**PLAN, "max_calls": 1, "max_details": 0, "searches": many})
    assert big.cycle_days() == 10
    assert big.window(big.searches[0]) == "month"
    fixed = parse_plan({**PLAN, "date_posted": "week", "max_calls": 25})
    assert fixed.window(fixed.searches[0]) == "week"


def test_plan_caps_budget_at_25() -> None:
    assert parse_plan({**PLAN, "max_calls": 40}).max_calls == 25
    assert parse_plan({**PLAN, "max_calls": 3, "max_details": 9}).max_details == 2


@pytest.mark.parametrize(
    "patch",
    [
        {"locations": {"x": {"geo_id": "Milan"}}},
        {"locations": {"x": {}}},
        {"searches": [{"keywords": "a", "locations": ["nowhere"]}]},
        {"searches": [{"keywords": "a", "locations": ["rome"], "experience": ["guru"]}]},
        {"date_posted": "yesterday"},
        {"searches": [{"id": "a", "keywords": "a"}, {"id": "a", "keywords": "b"}]},
    ],
)
def test_plan_rejects_bad_config(patch: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_plan({**PLAN, **patch})


def test_example_config_is_valid_and_fits_rotation() -> None:
    import yaml

    plan = parse_plan(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")))
    assert plan.searches and plan.max_calls <= 25
    assert plan.cycle_days() <= 7  # "week" window: a full cycle never misses postings


# ---------------------------------------------------------------------------
# mapping
# ---------------------------------------------------------------------------


def test_to_raw_maps_card_and_canonical_url_matches_email_alerts() -> None:
    raw = to_raw(_card(4000000001, salary="12K €/anno", easy_apply=True), "ds")
    assert raw is not None
    assert raw.source == "linkedin_jobs"
    assert raw.external_id == "4000000001"
    assert raw.url == "https://www.linkedin.com/jobs/view/4000000001/"
    # Same canonical URL as a LinkedIn email alert link for the same job.
    alert = "https://www.linkedin.com/comm/jobs/view/4000000001/?trackingId=x&refId=y"
    assert canonical_url(raw.url) == canonical_url(alert)
    assert raw.location == "Milano, Lombardia, Italia"
    assert raw.extra == {"search": "ds", "workplace": "Ibrido", "easy_apply": True,
                         "salary": "12K €/anno"}  # fmt: skip
    assert raw.posted_at is not None and raw.posted_at.year == 2026


def test_to_raw_skips_bad_rows() -> None:
    assert to_raw({"job_id": "abc", "title": "x"}, "s") is None
    assert to_raw({"job_id": "1"}, "s") is None


def test_connector_is_not_exhaustive() -> None:
    conn, _ = _conn(FakeCli())
    assert conn.exhaustive is False and is_exhaustive(conn) is False


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


def test_fetch_runs_searches_within_budget_and_dedupes() -> None:
    cli = FakeCli(
        {
            "data scientist": [_card(1), _card(2)],
            "venture capital analyst": [_card(2), _card(3, listed="2026-10-16T09:00:00Z")],
        }
    )
    conn, state = _conn(cli, known={"https://www.linkedin.com/jobs/view/1/"})
    raws = conn.fetch()
    assert sorted(r.external_id for r in raws) == ["1", "2", "3"]
    # budget 6, 2 kept for details -> 4 search calls: rome, milan(va), milan(ds), then the
    # lisbon slot (2 calls, location name) no longer fits.
    searches = cli.searches()
    assert len(searches) == 3
    first = searches[0]
    assert first[:4] == ["jobs", "search", "--geo-id", "100000001"]
    assert first[-2:] == ["--", "data scientist"]
    assert first[4:6] == ["--experience", "internship"]
    assert "--date-posted" in first and first[first.index("--date-posted") + 1] == "week"
    # details: newest first, skipping the one already described in the DB
    gets = [c[2] for c in cli.calls if c[:2] == ["jobs", "get"]]
    assert gets == ["3", "2"]
    assert next(r for r in raws if r.external_id == "3").raw_text == "Testo."
    assert cli.jobs_used <= 6
    assert state.state["last_run"] == "2026-10-16"
    assert state.state["cursor"] == 3
    assert state.state["calls"] == cli.jobs_used


def test_fetch_never_exceeds_cli_quota_left() -> None:
    cli = FakeCli({"data scientist": [_card(1)]}, jobs_used=28, jobs_limit=30)
    conn, _ = _conn(cli)
    conn.fetch()
    assert cli.jobs_used <= 30
    assert len(cli.calls) <= 2


def test_fetch_once_a_day() -> None:
    cli = FakeCli({"data scientist": [_card(1)]})
    conn, state = _conn(cli, state=MemoryState({"last_run": "2026-10-16", "cursor": 2}))
    assert conn.fetch() == []
    assert cli.calls == []
    assert state.saves == 0


def test_rotation_continues_next_day() -> None:
    cli = FakeCli()
    state = MemoryState()
    conn, _ = _conn(cli, state=state)
    conn.fetch()
    assert state.state["cursor"] == 3
    conn2, _ = _conn(cli, state=state, day=date(2026, 10, 17))
    cli.calls.clear()
    cli.jobs_used = 0
    conn2.fetch()
    order = [(c[3], c[-1]) for c in cli.searches()]  # --geo-id/--location value
    assert order[0] == ("Lisbon, Portugal", "venture capital analyst")  # slot 3
    assert order[1] == ("100000003", "data scientist")  # slot 4, then wraps around
    assert order[2] == ("100000001", "data scientist")


@pytest.mark.parametrize(
    "stderr,reason",
    [
        ("blocked (checkpoint): LinkedIn security checkpoint", "checkpoint"),
        ("blocked (captcha): LinkedIn is asking for a captcha", "captcha"),
        ("blocked (rate_limited): rate limited by LinkedIn (HTTP 429)", "rate_limited"),
        ("blocked (denied): LinkedIn refused the request (HTTP 999)", "denied"),
    ],
)
def test_block_raises_source_paused_and_stops(stderr: str, reason: str) -> None:
    cli = FakeCli({"data scientist": [_card(1)]}, fail={2: (3, stderr)})
    conn, state = _conn(cli)
    with pytest.raises(SourcePaused) as exc_info:
        conn.fetch()
    assert exc_info.value.source == "linkedin_jobs"
    assert exc_info.value.reason == reason
    assert exc_info.value.action == ACTION_BLOCKED
    assert "linkedin auth login" in ACTION_BLOCKED
    assert "radar connectors resume linkedin_jobs" in ACTION_BLOCKED
    assert len(cli.calls) == 2  # stopped at once, no retry
    assert state.state["cursor"] == 2  # progress kept for tomorrow
    assert state.state["last_run"] == "2026-10-16"


def test_block_looking_error_with_other_exit_code_also_pauses() -> None:
    cli = FakeCli(fail={1: (1, "HTTP 999 on GET https://www.linkedin.com/voyager/api/...")})
    conn, _ = _conn(cli)
    with pytest.raises(SourcePaused) as exc_info:
        conn.fetch()
    assert exc_info.value.reason == "999"


def test_unauthenticated_pauses_with_login_action() -> None:
    cli = FakeCli(fail={1: (2, "not authenticated — run `linkedin auth login` first")})
    conn, _ = _conn(cli)
    with pytest.raises(SourcePaused) as exc_info:
        conn.fetch()
    assert exc_info.value.action == ACTION_AUTH
    assert len(cli.calls) == 1


def test_quota_exit_ends_run_quietly() -> None:
    cli = FakeCli({"data scientist": [_card(1)]}, fail={2: (4, "daily quota exceeded for jobs")})
    conn, _ = _conn(cli)
    raws = conn.fetch()
    assert [r.external_id for r in raws] == ["1"]
    assert len(cli.searches()) == 2


def test_consecutive_errors_stop_and_all_failed_raises() -> None:
    cli = FakeCli(fail={1: (1, "HTTP 400 on GET x"), 2: (1, "HTTP 400 on GET x")})
    conn, _ = _conn(cli)
    with pytest.raises(SourceError, match="all 2 searches failed"):
        conn.fetch()
    assert len(cli.calls) == 2


def test_closed_detail_drops_posting() -> None:
    cli = FakeCli({"data scientist": [_card(7)]}, details={"7": {"job_id": "7", "closed": True}})
    conn, _ = _conn(cli)
    assert all(r.external_id != "7" for r in conn.fetch())


def test_old_cli_without_quota_is_a_source_error() -> None:
    cli = FakeCli(quota_supported=False)
    conn, state = _conn(cli)
    with pytest.raises(SourceError, match=r">= 0\.3"):
        conn.fetch()
    assert cli.calls == []
    assert state.saves == 0


def test_timeout_is_not_retried() -> None:
    calls: list[list[str]] = []

    def runner(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if cmd[2:] == ["quota"]:
            body = {"jobs": {"used": 0, "limit": 30}, "api_total": {"used": 0, "limit": 200}}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(body), "")
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 1)

    conn = LinkedInJobsConnector(
        parse_plan(PLAN), runner=runner, state=MemoryState(), known=lambda u: set()
    )
    with pytest.raises(SourceError, match="timed out"):
        conn.fetch()
    assert len(calls) == 1


def test_refuses_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    cli = FakeCli()
    conn, _ = _conn(cli)
    with pytest.raises(SourceError, match="CI"):
        conn.fetch()
    assert cli.calls == []


# ---------------------------------------------------------------------------
# availability + registry
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, file: bool, binary: bool):  # type: ignore[no-untyped-def]
    from radar.config import get_settings

    cfg = tmp_path / "config"
    cfg.mkdir()
    if file:
        (cfg / "linkedin_searches.yaml").write_text(EXAMPLE.read_text(encoding="utf-8"))
    bin_path = tmp_path / "linkedin"
    if binary:
        bin_path.write_text("#!/bin/sh\nexit 0\n")
        bin_path.chmod(0o755)
    monkeypatch.setenv("RADAR_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("RADAR_LINKEDIN_BIN", str(bin_path))
    get_settings.cache_clear()
    return get_settings()


@pytest.mark.parametrize("file,binary", [(True, True), (True, False), (False, True)])
def test_registry_builds_connector_only_with_file_and_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file: bool, binary: bool
) -> None:
    from radar.connectors.http import PoliteClient
    from radar.connectors.registry import ALL_SOURCES, build_connectors

    assert "linkedin_jobs" in ALL_SOURCES
    settings = _settings(tmp_path, monkeypatch, file=file, binary=binary)
    assert available(settings) is (file and binary)
    with PoliteClient("test-agent", tmp_path / "cache") as client:
        conns = build_connectors(client, {}, settings=settings)
        only = build_connectors(client, {}, sources=["lever"], settings=settings)
    assert [c.source for c in conns] == (["linkedin_jobs"] if file and binary else [])
    assert only == []


def test_registry_skips_broken_searches_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from radar.connectors.http import PoliteClient
    from radar.connectors.registry import build_connectors

    settings = _settings(tmp_path, monkeypatch, file=True, binary=True)
    (settings.config_dir / "linkedin_searches.yaml").write_text("locations: {x: {geo_id: abc}}\n")
    with PoliteClient("test-agent", tmp_path / "cache") as client:
        assert build_connectors(client, {}, settings=settings) == []


def test_settings_default_sources_include_linkedin_jobs_only_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from radar.api.routes.settings import default_sources

    assert "linkedin_jobs" not in default_sources()
    _settings(tmp_path, monkeypatch, file=True, binary=True)
    assert default_sources()[-1] == "linkedin_jobs"


# ---------------------------------------------------------------------------
# pipeline: non-exhaustive + dedupe with email alerts
# ---------------------------------------------------------------------------


def test_pipeline_never_closes_by_absence_and_dedupes_alert_url(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    import yaml
    from sqlmodel import Session, select

    from radar.db import get_engine, init_db
    from radar.models import Posting
    from radar.pipeline.filters import compile_rules
    from radar.pipeline.run import PipelineOptions, run_pipeline
    from radar.schemas import RawPosting

    class _Alert:
        source = "email"
        label = "email alerts"
        exhaustive = False

        def fetch(self) -> list[RawPosting]:
            return [
                RawPosting(
                    source="email",
                    external_id="li:1",
                    url="https://www.linkedin.com/comm/jobs/view/1/?trackingId=x",
                    title="Stage Data 1",
                    company="Nebbia Labs",
                )
            ]

    rules_path = Path(__file__).resolve().parents[2] / "config" / "rules.example.yaml"
    rules = compile_rules(yaml.safe_load(rules_path.read_text("utf-8")))
    engine = init_db(get_engine(f"sqlite:///{tmp_path / 'r.db'}"))
    opts = PipelineOptions(llm=False, check_closed=False)
    state = MemoryState()
    day1 = FakeCli({"data scientist": [_card(1), _card(2)]})
    conn1, _ = _conn(day1, state=state)
    run_pipeline(
        opts,
        connectors=[conn1, _Alert()],  # type: ignore[list-item]
        engine=engine,
        rules=rules,
        reports_dir=tmp_path,
        now=datetime(2026, 10, 16, 6, tzinfo=UTC),
    )
    day2 = FakeCli({"data scientist": [_card(1)]})
    conn2, _ = _conn(day2, state=state, day=date(2026, 10, 17))
    run_pipeline(
        opts,
        connectors=[conn2],  # type: ignore[list-item]
        engine=engine,
        rules=rules,
        reports_dir=tmp_path,
        now=datetime(2026, 10, 17, 6, tzinfo=UTC),
    )
    with Session(engine) as s:
        rows = s.exec(select(Posting).where(Posting.source == "linkedin_jobs")).all()
        assert {p.external_id: p.status for p in rows} == {"1": "open", "2": "open"}
        same = s.exec(
            select(Posting).where(Posting.canonical_url == "https://www.linkedin.com/jobs/view/1")
        ).all()
        assert sorted(p.source for p in same) == ["email", "linkedin_jobs"]
        assert sum(p.duplicate_of is not None for p in same) == 1  # one is the other's duplicate
