"""score_pending spends its LLM budget on the highest local similarity first."""

from __future__ import annotations

from llm_testkit import make_posting
from sqlmodel import Session, select

from radar.llm import scoring
from radar.models import Score


def test_top_n_by_similarity(session: Session, fake_env: None) -> None:
    sims = [0.2, 0.9, None, 0.5, 0.7]
    postings = [
        make_posting(session, title=f"Data Science Intern {i}", similarity=s)
        for i, s in enumerate(sims)
    ]
    offtarget = make_posting(session, title="Tax Intern", similarity=0.99)

    stats = scoring.score_pending(session, run_id=None, limit=2)
    assert stats["candidates"] == 2 and stats["scored"] == 2
    assert stats["rules_bassa"] == 1  # off-target title still scored by rule, not by the LLM
    llm = {s.posting_id for s in session.exec(select(Score)).all() if s.provider == "fake"}
    assert llm == {postings[1].id, postings[4].id}
    rules = {s.posting_id for s in session.exec(select(Score)).all() if s.provider == "rules"}
    assert rules == {offtarget.id}

    # Next run continues with the next best; unranked postings come last.
    scoring.score_pending(session, run_id=None, limit=2)
    llm = {s.posting_id for s in session.exec(select(Score)).all() if s.provider == "fake"}
    assert llm == {postings[i].id for i in (1, 4, 3, 0)}


def test_without_similarity_keeps_title_relevance_order(session: Session, fake_env: None) -> None:
    generic = make_posting(session, title="Operations Intern")
    target = make_posting(session, title="Machine Learning Intern")
    scoring.score_pending(session, run_id=None, limit=1)
    llm = {s.posting_id for s in session.exec(select(Score)).all() if s.provider == "fake"}
    assert llm == {target.id}
    assert generic.id not in llm


def test_pack_candidates_prefer_similarity_at_equal_priority(
    session: Session, fake_env: None
) -> None:
    from radar.llm.packs import pack_candidates

    low = make_posting(session, title="Data Scientist A", priority="Alta", similarity=0.3)
    high = make_posting(session, title="Data Scientist B", priority="Alta", similarity=0.8)
    none = make_posting(session, title="Data Scientist C", priority="Alta")
    assert [p.id for p in pack_candidates(session, 5)] == [high.id, low.id, none.id]
