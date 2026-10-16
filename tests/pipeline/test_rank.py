"""Local embedding pre-ranking with a deterministic fake embedder (no model, no network)."""

from __future__ import annotations

import builtins
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pipeline_fakes import TODAY, FakeConnector, raw
from sqlalchemy import Engine
from sqlmodel import Session

import radar.pipeline.rank as rank_mod
from radar.models import Posting
from radar.pipeline.filters import Rules
from radar.pipeline.normalize import content_hash
from radar.pipeline.rank import (
    EmbeddingCache,
    HashingEmbedder,
    profile_chunks,
    rank_postings,
    top_postings,
)
from radar.pipeline.run import PipelineOptions, run_pipeline

REPO = Path(__file__).resolve().parents[2]
ORIGINAL_LOADER = rank_mod.load_default_embedder  # before the autouse fixture patches it
PROFILE: dict[str, Any] = yaml.safe_load(
    (REPO / "config" / "profile.example.yaml").read_text("utf-8")
)
FACTS: dict[str, Any] = {
    "experience": [
        {
            "id": "exp-a",
            "org": "Example Analytics",
            "role": "Data Intern",
            "dates": "2026",
            "did": ["Built a demand forecasting pipeline in Python and SQL."],
            "do_not_claim": ["payroll tax accounting"],
        }
    ],
    "projects": [
        {"id": "p1", "name": "tinybench", "solution": "Benchmark of LLM agents with RAG."},
        {"id": "p2", "name": "secret", "solution": "Tax payroll legal", "use_in_drafts": False},
    ],
    "skills": {"ml": ["machine learning", "forecasting"], "llm": ["LLM", "RAG", "embeddings"]},
}


def _posting(session: Session, title: str, text: str, **kw: Any) -> Posting:
    n = len(session.exec(rank_mod.select(Posting)).all())
    p = Posting(
        uid=f"test:{n}",
        source="greenhouse",
        external_id=str(n),
        url=f"https://example.com/{n}",
        canonical_url=f"https://example.com/{n}",
        title=title,
        company_name="Acme",
        raw_text=text,
        content_hash=content_hash(title, "Acme", None, text),
        **kw,
    )
    session.add(p)
    session.commit()
    return p


def _cache(tmp_path: Path) -> EmbeddingCache:
    return EmbeddingCache(tmp_path / "cache" / "embeddings.sqlite")


def test_profile_chunks_skip_excluded_items_and_keys() -> None:
    roles, facts = profile_chunks(PROFILE, FACTS)
    assert roles == PROFILE["target_roles"]
    joined = " ".join(facts)
    assert "forecasting pipeline" in joined
    assert "tinybench" in joined
    assert "payroll" not in joined.lower()  # do_not_claim and use_in_drafts: false
    assert "exp-a" not in joined and "2026" not in joined
    # Without a fact bank, profile.yaml's own sections are used.
    _, fallback = profile_chunks(PROFILE, {})
    assert any("forecasting" in c.lower() for c in fallback)


def test_ranking_order(session: Session, tmp_path: Path) -> None:
    ml = _posting(session, "Machine Learning Intern", "LLM agents, RAG and forecasting in Python")
    data = _posting(session, "Data Scientist", "Forecasting models with SQL")
    tax = _posting(session, "Tax Intern", "Payroll and VAT accounting for clients")
    rejected = _posting(session, "Data Intern", "Python", filter_state="rejected")
    stats = rank_postings(
        session, HashingEmbedder(), _cache(tmp_path), profile=PROFILE, facts=FACTS
    )
    assert stats["ranked"] == 3 and stats["embedded"] == 3 and stats["updated"] == 3
    for p in (ml, data, tax):
        session.refresh(p)
        assert p.similarity is not None and 0.0 <= p.similarity <= 1.0
        assert p.similarity_version == stats["profile_version"]
    assert ml.similarity > tax.similarity  # type: ignore[operator]
    assert data.similarity > tax.similarity  # type: ignore[operator]
    assert rejected.similarity is None
    assert [p.id for p in top_postings(session, 2)] == [
        p.id for p in sorted((ml, data), key=lambda p: -(p.similarity or 0))
    ]


def test_cache_by_content_hash(session: Session, tmp_path: Path) -> None:
    a = _posting(session, "Data Scientist", "Forecasting in Python")
    _posting(session, "ML Engineer", "LLM agents")
    emb = HashingEmbedder()
    first = rank_postings(session, emb, _cache(tmp_path), profile=PROFILE, facts=FACTS)
    calls = emb.calls
    assert first["embedded"] == 2

    # Second run: nothing new to embed (cache on disk survives a new cache object).
    emb2 = HashingEmbedder()
    again = rank_postings(session, emb2, _cache(tmp_path), profile=PROFILE, facts=FACTS)
    assert again["embedded"] == 0 and again["cached"] == 2 and again["updated"] == 0
    assert emb2.calls == 0  # profile chunks cached too

    # A content change embeds only that posting.
    a.raw_text = "Payroll"
    a.content_hash = content_hash(a.title, "Acme", None, "Payroll")
    session.add(a)
    session.commit()
    third = rank_postings(session, emb, _cache(tmp_path), profile=PROFILE, facts=FACTS)
    assert third["embedded"] == 1 and third["cached"] == 1
    assert emb.calls == calls + 1


def test_profile_change_recomputes(session: Session, tmp_path: Path) -> None:
    p = _posting(session, "Data Scientist", "Forecasting in Python")
    first = rank_postings(
        session, HashingEmbedder(), _cache(tmp_path), profile=PROFILE, facts=FACTS
    )
    changed = {**FACTS, "skills": {"other": ["Payroll", "tax"]}}
    second = rank_postings(
        session, HashingEmbedder(), _cache(tmp_path), profile=PROFILE, facts=changed
    )
    assert second["profile_version"] != first["profile_version"]
    assert second["embedded"] == 0 and second["updated"] == 1
    session.refresh(p)
    assert p.similarity_version == second["profile_version"]


def test_graceful_fallback_without_extra(
    session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_import = builtins.__import__

    def no_st(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sentence_transformers":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_st)
    with caplog.at_level(logging.INFO, logger="radar.pipeline.rank"):
        assert ORIGINAL_LOADER() is None
    assert len(caplog.records) == 1 and "embed extra" in caplog.records[0].getMessage()

    p = _posting(session, "Data Scientist", "x")
    assert rank_postings(session)["skipped"]  # autouse fixture: no embedder available
    session.refresh(p)
    assert p.similarity is None


def test_pipeline_ranks_before_llm(
    engine: Engine, rules: Rules, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from radar import config as radar_config

    def fake_config(name: str) -> dict[str, Any]:
        if name == "profile":
            return PROFILE
        raise FileNotFoundError(name)

    monkeypatch.setattr(radar_config, "load_yaml_config", fake_config)
    monkeypatch.setenv("RADAR_DATA_DIR", str(tmp_path / "data"))
    radar_config.get_settings.cache_clear()
    conn = FakeConnector(
        "greenhouse",
        [
            raw("1", title="Data Science Intern", raw_text="Python forecasting"),
            raw("2", title="Machine Learning Intern", raw_text="LLM agents"),
        ],
    )
    opts = PipelineOptions(llm=False, check_closed=False)
    kwargs: dict[str, Any] = {
        "connectors": [conn],
        "engine": engine,
        "rules": rules,
        "today": TODAY,
        "reports_dir": tmp_path,
    }
    try:
        without = run_pipeline(opts, **kwargs)
        assert without.stats["rank"] == {"skipped": "embed extra not installed"}
        assert not without.stats["errors"]

        result = run_pipeline(opts, embedder=HashingEmbedder(), **kwargs)
        assert not result.stats["errors"]
        assert result.stats["rank"]["ranked"] == 2
        assert (tmp_path / "data" / "cache" / "embeddings.sqlite").exists()
        no_rank = run_pipeline(PipelineOptions(llm=False, check_closed=False, rank=False), **kwargs)
        assert no_rank.stats["rank"] == {}
    finally:
        radar_config.get_settings.cache_clear()
