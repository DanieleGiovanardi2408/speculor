"""post_graduation: graduate programmes / full-time roles starting after graduation (July 2027)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pipeline_fakes import TODAY, raw
from sqlmodel import Session, select

from radar.models import FilterResult, Run
from radar.pipeline.filters import (
    POST_GRADUATION_ID,
    Hit,
    PostingText,
    Rules,
    apply_filters,
    compile_rules,
    evaluate,
    state_for,
)
from radar.pipeline.store import upsert_raw

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)


def hits_for(
    rules: Rules, title: str, text: str | None = None, type_: str | None = None
) -> list[Hit]:
    posting = PostingText(title=title, raw_text=text, location="Milan, Italy", type=type_)
    return evaluate(posting, rules, TODAY)


def post_grad(hits: list[Hit]) -> Hit | None:
    return next((h for h in hits if h.rule_id == POST_GRADUATION_ID), None)


@pytest.mark.parametrize(
    ("title", "text", "type_", "reason_part"),
    [
        ("Graduate Programme 2027, September intake", "Rotations in Milan.", None, "Sep 2027"),
        ("Graduate Programme 2027", "September 2027 intake, three rotations.", None, "Sep 2027"),
        ("Full-time Data Scientist, start after graduation", "Python and SQL.", None, "graduates"),
        ("Graduate Trader", "Trade options on our market-making desk.", None, "no start date"),
        ("Graduate Trader", "Trade options.", "Graduate", "no start date"),
        ("Programma per neolaureati, inizio settembre 2027", "Sede: Milano.", None, "Sep 2027"),
        ("Analista dati", "Inserimento in azienda dopo la laurea.", "Lavoro", "graduates"),
        ("Data Scientist", "Start date: October 2027. Permanent contract.", "Lavoro", "Oct 2027"),
        ("Graduate Software Engineer (2028 start)", "Join us.", None, "2028"),
        ("Graduate Quantitative Researcher (2027 Start)", "Research.", "Graduate", "2027"),
        ("Junior Analyst", "This role is for recent graduates.", "Lavoro", "graduates"),
        ("Auditor Data Scientist Junior", "Richiede laurea gia conseguita.", "Lavoro", "graduates"),
    ],
)
def test_post_graduation_flagged(
    rules: Rules, title: str, text: str, type_: str | None, reason_part: str
) -> None:
    hits = hits_for(rules, title, text, type_)
    hit = post_grad(hits)
    assert hit is not None, hits
    assert hit.action == "flag"
    assert hit.reason.startswith("post-graduation")
    assert reason_part in hit.reason
    assert hit.evidence and len(hit.evidence) <= 200
    # not an exclusion: the posting stays visible (flagged), in its own track
    assert state_for(hits) == "flagged"


@pytest.mark.parametrize(
    ("title", "text", "type_"),
    [
        ("Data Science Intern (6 months, from Feb 2027)", "Recent graduates welcome.", None),
        ("Stage curriculare per laureandi", "Aperto a laureandi e neolaureati.", "Stage"),
        ("Graduate Intern", "For recent graduates, start after graduation.", None),
        ("Thesis internship for graduating students", "After graduation you may join us.", None),
        ("Full-time internship starting February 2027", "Recent graduates welcome.", None),
        ("Tirocinio extracurriculare neolaureati", "Inizio settembre 2027.", None),
        ("Werkstudent Data Science", "Nach dem Studium Übernahme möglich.", None),
        ("Summer Analyst 2027", "Recent graduates.", None),
    ],
)
def test_internships_never_post_graduation(
    rules: Rules, title: str, text: str, type_: str | None
) -> None:
    assert post_grad(hits_for(rules, title, text, type_)) is None


def test_seed_stage_type_is_an_internship(rules: Rules) -> None:
    hits = hits_for(rules, "Junior Data Analyst", "Rivolto a neolaureati.", "Stage")
    assert post_grad(hits) is None


@pytest.mark.parametrize(
    ("title", "text", "type_"),
    [
        # graduate programme with an in-window start (Feb-Apr 2027) stays an internship option
        ("Graduate Programme", "Start: March 2027, while you finish your MSc.", "Graduate"),
        (
            "Graduate Programme - Candidatura Spontanea",
            "Buono per partire tra febbraio e aprile.",
            None,
        ),
        # flexible start could be February
        ("Graduate AI software engineer", "Inizio flessibile anche part-time.", "Graduate"),
        # takes students who are still enrolled
        ("AI Engineer - Graduate Development Program", "Accetta magistrale in corso.", "Graduate"),
        ("Security Engineer, New Grad", "Currently enrolled in a Master's program.", None),
        # graduates of a range that includes 2027
        ("Graduate Programme", "Laureati 2025-2027, inizio febbraio 2027.", "Graduate"),
        # two intakes: "after graduation" is about the August one, February is open
        (
            "Graduate Quantitative Researcher",
            "Ingresso febbraio o agosto 2027: agosto e dopo la laurea",
            "Graduate",
        ),
        # full-time role with no post-graduation signal
        ("Data Scientist", "Python, SQL. Start: February 2027.", "Lavoro"),
        ("Data Scientist", "Python and SQL.", "Lavoro"),
        ("Data Scientist", "Full-time. Students graduating in 2027 are welcome.", None),
        ("Research Engineer", "Gained through post-graduate study.", "Lavoro"),
    ],
)
def test_not_post_graduation(rules: Rules, title: str, text: str, type_: str | None) -> None:
    assert post_grad(hits_for(rules, title, text, type_)) is None


def test_supersedes_start_window_hits(rules: Rules) -> None:
    # Sep 2027 would be "summer-only" (a reject) for an internship; for a graduate
    # programme it is the post-graduation start: one flag, no start_* hit.
    hits = hits_for(rules, "Programma per neolaureati", "Inizio settembre 2027.", None)
    assert {h.rule_id for h in hits} == {POST_GRADUATION_ID}
    assert state_for(hits) == "flagged"

    late = hits_for(rules, "Data Scientist", "Start date: October 2027.", "Lavoro")
    assert POST_GRADUATION_ID in {h.rule_id for h in late}
    assert "start_after_window" not in {h.rule_id for h in late}

    undated = hits_for(rules, "Graduate Trader", "Trade options.", None)
    assert "start_not_stated" not in {h.rule_id for h in undated}


def test_internship_keeps_start_after_window(rules: Rules) -> None:
    hits = hits_for(rules, "Data Science Intern", "Start date: October 2027.", "Stage")
    assert "start_after_window" in {h.rule_id for h in hits}
    assert post_grad(hits) is None


def test_other_rejects_still_apply(rules: Rules) -> None:
    hits = hits_for(rules, "Graduate Trader", "At least 5 years of professional experience.")
    assert post_grad(hits) is not None
    assert state_for(hits) == "rejected"


def test_short_month_words_need_a_year(rules: Rules) -> None:
    # "you may start" is not a May start: the programme stays undated
    hit = post_grad(hits_for(rules, "Graduate Programme", "You may start soon.", "Graduate"))
    assert hit is not None and "no start date" in hit.reason


def test_rule_can_be_disabled() -> None:
    cfg = yaml.safe_load((REPO / "config" / "rules.example.yaml").read_text("utf-8"))
    cfg["post_graduation"]["enabled"] = False
    hits = evaluate(
        PostingText(title="Graduate Trader", location="Milan"), compile_rules(cfg), TODAY
    )
    assert post_grad(hits) is None
    assert "start_not_stated" in {h.rule_id for h in hits}


def test_example_and_local_rules_identical() -> None:
    local = REPO / "config" / "rules.yaml"
    if not local.exists():
        pytest.skip("no local config/rules.yaml")
    example = (REPO / "config" / "rules.example.yaml").read_text("utf-8")
    assert yaml.safe_load(local.read_text("utf-8")) == yaml.safe_load(example)


def test_apply_filters_persists_flag_and_respects_override(session: Session, rules: Rules) -> None:
    run = Run()
    session.add(run)
    session.flush()
    posting = upsert_raw(
        session,
        raw("pg1", title="Graduate Programme", raw_text="September 2027 intake.", location="Milan"),
        run_id=run.id,
        now=NOW,
    ).posting
    assert apply_filters(session, posting, rules, TODAY) == "flagged"
    rows = session.exec(select(FilterResult).where(FilterResult.posting_id == posting.id)).all()
    assert [(r.rule_id, r.overridden) for r in rows] == [(POST_GRADUATION_ID, False)]

    # the user moves it back to the internship track; re-filtering keeps that decision
    rows[0].overridden = True
    posting.filter_state = "overridden"
    session.add_all([rows[0], posting])
    session.flush()
    assert apply_filters(session, posting, rules, TODAY) == "overridden"
    rows = session.exec(select(FilterResult).where(FilterResult.posting_id == posting.id)).all()
    assert [(r.rule_id, r.overridden) for r in rows] == [(POST_GRADUATION_ID, True)]
