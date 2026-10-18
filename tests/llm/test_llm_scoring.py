from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from llm_testkit import ScriptedProvider, make_posting
from sqlmodel import Session, select

from radar.llm import scoring
from radar.llm.provider import MissingKeyError, RateLimitExhausted
from radar.models import LLMUsage, Posting, Run, Score
from radar.timeutil import utcnow

VALID = json.dumps(
    {
        "priority": "high",
        "why_fits": "Usa Python e forecasting come nel progetto. Il team è a Milano. Terza frase.",
        "what_doesnt_fit": "Richiede tedesco — non presente nel profilo.",
        "start_window": "February 2027",
        "language_requirements": "English",
        "confidence": 80,
        "warnings": [],
    }
)


def scores(session: Session) -> list[Score]:
    return list(session.exec(select(Score)).all())


def test_fake_provider_end_to_end_and_cache(session: Session, fake_env: None) -> None:
    run = Run()
    session.add(run)
    session.commit()
    eligible = make_posting(session, title="Data Science Intern")
    flagged = make_posting(session, title="Data Analyst Intern", filter_state="flagged")
    make_posting(session, title="Rejected", filter_state="rejected")
    make_posting(session, title="Closed", status="closed")
    make_posting(session, title="Dup", duplicate_of=eligible.id)
    make_posting(session, title="Seeded", priority="Media")  # manual score counts as scored

    stats = scoring.score_pending(session, run_id=run.id, limit=10)
    assert stats["candidates"] == 2
    assert stats["scored"] == 2
    assert stats["provider"] == "fake"
    by_posting = {s.posting_id: s for s in scores(session) if s.provider == "fake"}
    assert set(by_posting) == {eligible.id, flagged.id}
    s = by_posting[eligible.id]
    assert s.priority == "Alta"
    assert s.content_hash == eligible.content_hash
    assert "ruolo" in s.why_fits  # Italian
    assert s.input_tokens > 0 and s.output_tokens > 0
    assert by_posting[flagged.id].priority in {"Alta", "Media", "Bassa"}

    usage = session.exec(select(LLMUsage)).all()
    assert len(usage) == 1
    assert (usage[0].run_id, usage[0].purpose, usage[0].requests) == (run.id, "scoring", 2)

    # second call: nothing left (cache by content hash)
    again = scoring.score_pending(session, run_id=None, limit=10)
    assert again["scored"] == 0 and again["candidates"] == 0

    # content change -> new hash -> scored again
    eligible.content_hash = "changed"
    session.add(eligible)
    session.commit()
    assert scoring.score_pending(session, run_id=None, limit=10)["scored"] == 1


def test_invalid_json_then_repair(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    make_posting(session)
    fake = ScriptedProvider(["Sure! here it is: {priority: Alta", VALID])
    monkeypatch.setattr(scoring, "get_provider", lambda purpose: fake)
    monkeypatch.setattr(scoring.radar_config, "load_yaml_config", lambda n: {})
    stats = scoring.score_pending(session, run_id=None, limit=5)
    assert stats["scored"] == 1 and stats["requests"] == 2
    assert "INVALID" in fake.prompts[1][1]
    (s,) = scores(session)
    assert s.priority == "Alta"
    assert s.confidence == pytest.approx(0.8)
    assert s.language_requirements == ["English"]
    assert s.why_fits.count(".") == 2  # max 2 sentences
    assert "—" not in s.what_doesnt_fit
    assert (s.input_tokens, s.output_tokens) == (200, 100)


def test_invalid_twice_is_skipped(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    make_posting(session)
    make_posting(session, title="Second")
    fake = ScriptedProvider(["nope", '{"priority": "Excellent"}', VALID])
    monkeypatch.setattr(scoring, "get_provider", lambda purpose: fake)
    monkeypatch.setattr(scoring.radar_config, "load_yaml_config", lambda n: {})
    stats = scoring.score_pending(session, run_id=None, limit=5)
    assert stats["invalid"] == 1 and stats["scored"] == 1


def test_rate_limit_stops_gracefully(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(3):
        make_posting(session, title=f"Data Intern {i}")
    fake = ScriptedProvider([VALID, RateLimitExhausted("daily")])
    monkeypatch.setattr(scoring, "get_provider", lambda purpose: fake)
    monkeypatch.setattr(scoring.radar_config, "load_yaml_config", lambda n: {})
    stats = scoring.score_pending(session, run_id=None, limit=5)
    assert stats["stopped"] == "rate_limit"
    assert stats["scored"] == 1
    # next run resumes with the remaining two
    assert len(scoring.pending_postings(session, 10)) == 2


def test_missing_key(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    make_posting(session)

    def boom(purpose: str) -> None:
        raise MissingKeyError("GROQ_API_KEY is not set")

    monkeypatch.setattr(scoring, "get_provider", boom)
    stats = scoring.score_pending(session, run_id=None, limit=5)
    assert stats["skipped"] == "missing_key"
    assert scores(session) == []


def test_priority_order_newest_then_deadline(session: Session) -> None:
    old = make_posting(session, title="Old")
    old.first_seen = utcnow() - timedelta(days=3)
    late = make_posting(session, title="Late deadline", deadline=date.today() + timedelta(days=60))
    soon = make_posting(session, title="Soon deadline", deadline=date.today() + timedelta(days=5))
    none = make_posting(session, title="No deadline")
    session.add(old)
    session.commit()
    order = [p.title for p in scoring.pending_postings(session, 10)]
    assert order == [soon.title, late.title, none.title, old.title]


def test_parse_score_validation() -> None:
    out = scoring.parse_score(
        '```json\n{"priority": "Bassa", "why_fits": "Poco.", '
        '"confidence": "0.3", "start_window": "null"}\n```'
    )
    assert out.priority == "Bassa" and out.start_window is None and out.confidence == 0.3
    with pytest.raises(ValueError):
        scoring.parse_score('{"priority": "Alta"}')
    with pytest.raises(ValueError):
        scoring.parse_score("[1, 2]")


def test_posting_model_untouched(session: Session, fake_env: None) -> None:
    p = make_posting(session)
    session.refresh(p)
    before = p.model_dump()
    scoring.score_pending(session, run_id=None, limit=1)
    after = session.get(Posting, p.id)
    assert after is not None and after.model_dump() == before


def test_offtarget_titles_get_rules_bassa_without_llm(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_posting(session, title="Tax Analyst")
    make_posting(session, title="AI Engineer Intern")
    fake = ScriptedProvider([VALID])
    monkeypatch.setattr(scoring, "get_provider", lambda purpose: fake)
    monkeypatch.setattr(scoring.radar_config, "load_yaml_config", lambda n: {})
    stats = scoring.score_pending(session, run_id=None, limit=5)
    assert stats["rules_bassa"] == 1 and stats["scored"] == 1
    rules = session.exec(select(Score).where(Score.provider == "rules")).one()
    assert rules.priority == "Bassa"


def test_seed_backlog_uses_the_backlog_model_and_only_described_seeds(
    session: Session, monkeypatch: pytest.MonkeyPatch, fake_env: None
) -> None:
    other = make_posting(session, title="Data Science Intern")
    described = make_posting(
        session, title="AI Intern", source="seed", description_status="fetched"
    )
    make_posting(session, title="ML Intern", source="seed")  # no description: never scored
    purposes: list[str] = []
    real = scoring.get_provider

    def spy(purpose: str):  # type: ignore[no-untyped-def]
        purposes.append(purpose)
        return real(purpose)  # type: ignore[arg-type]

    monkeypatch.setattr(scoring, "get_provider", spy)
    stats = scoring.score_seed_backlog(session, run_id=None)
    assert purposes == ["backlog"]
    assert stats["scored"] == 1
    assert {s.posting_id for s in scores(session)} == {described.id}
    assert other.id not in {s.posting_id for s in scores(session)}
    assert scoring.score_seed_backlog(session, run_id=None)["candidates"] == 0  # done


def test_seed_backlog_never_runs_on_a_paid_provider(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_LLM_PROVIDER", "anthropic")
    scoring.radar_config.get_settings.cache_clear()
    try:
        assert scoring.score_seed_backlog(session, run_id=None) == {"skipped": "disabled"}
    finally:
        scoring.radar_config.get_settings.cache_clear()
