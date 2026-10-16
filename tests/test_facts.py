"""Fact bank loader: stable ids, use_in_drafts, not_stated, prohibitions, resolution order."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from radar import config as radar_config
from radar.facts import FactsError, build_bank, load_facts, select_facts
from radar.llm.style import AVAILABILITY_SENTENCE

EXAMPLE = radar_config.REPO_ROOT / "config" / "facts.example.yaml"

BANK = {
    "meta": {
        "version": "2026-01-01",
        "decisions": {
            "availability": AVAILABILITY_SENTENCE["en"],
            "availability_it": AVAILABILITY_SENTENCE["it"],
        },
    },
    "identity": {
        "name": "Mara Fittizia",
        "citizenship": "EU citizen",
        "languages": {"italian": "native", "english": "C1", "other": "none"},
    },
    "experience": [
        {
            "id": "acme",
            "org": "Acme",
            "role": "Data Intern",
            "did": ["Built scrapers in Python.", "Helped run the crawlers."],
            "stack": ["Python", "Playwright"],
            "do_not_claim": ["leading the team"],
            "products": [
                {"id": "widget", "name": "Widget", "scale_citable": "about 1,200 users"},
            ],
        },
        {
            "id": "secret",
            "org": "Secret Co",
            "did": ["Something private."],
            "use_in_drafts": False,
        },
    ],
    "education": [{"id": "uni", "school": "Example University", "grades": "not_stated"}],
    "projects": [
        {"id": "p1", "name": "tinybench", "results": ["10/10 recall", "23% lower cost"]},
        {"id": "hidden", "name": "hiddenproj", "use_in_drafts": False, "results": ["9 users"]},
    ],
    "skills": {"programming": ["Python", "SQL"]},
    "drafting_rules": ["Cite only facts above."],
}


def test_ids_and_texts() -> None:
    bank = build_bank(BANK)
    ids = bank.ids
    assert "experience.acme.did.0" in ids and "experience.acme.did.1" in ids
    assert "experience.acme.products.widget.scale_citable" in ids
    assert "projects.p1.results.0" in ids and "projects.p1.results.1" in ids
    assert "meta.decisions.availability" in ids and "identity.name" in ids
    # short scalar lists are one fact
    assert "experience.acme.stack" in ids and "experience.acme.stack.0" not in ids
    assert bank.get("skills.programming").text == "programming: Python, SQL"  # type: ignore[union-attr]
    fact = bank.get("experience.acme.did.0")
    assert fact is not None and fact.text == "Acme, did: Built scrapers in Python."
    assert bank.name == "Mara Fittizia" and bank.version == "2026-01-01"
    assert bank.rules == ["Cite only facts above."]
    assert bank.render_facts().splitlines()[0].startswith("[meta.decisions.availability] ")


def test_use_in_drafts_false_skips_subtree() -> None:
    bank = build_bank(BANK)
    assert not any(i.startswith("experience.secret") for i in bank.ids)
    assert not any(i.startswith("projects.hidden") for i in bank.ids)
    assert bank.excluded == ["experience.secret", "projects.hidden"]
    assert "Secret Co" not in bank.render_facts() and "9 users" not in bank.render_facts()
    p = bank.prohibition("projects.hidden.excluded")
    assert p is not None and "hiddenproj" in p.text and p.patterns


def test_not_stated_and_do_not_claim_become_prohibitions() -> None:
    bank = build_bank(BANK)
    grades = bank.prohibition("education.uni.grades")
    assert grades is not None and "not stated" in grades.text and grades.patterns
    assert "education.uni.grades" not in bank.ids
    dnc = bank.prohibition("experience.acme.do_not_claim.0")
    assert dnc is not None and dnc.text == "Acme: leading the team"
    english = bank.prohibition("identity.languages.english.level")
    assert english is not None and "never" in english.text and bank.english_level == "C1"
    unlisted = bank.prohibition("identity.languages.unlisted")
    assert unlisted is not None and "French" in unlisted.text and "Italian" not in unlisted.text


def test_unquoted_colon_item_is_rejoined_with_warning() -> None:
    data = yaml.safe_load(
        "experience:\n  - id: x\n    org: X\n    did:\n      - Built a tool: fast and small\n"
    )
    bank = build_bank(data)
    fact = bank.get("experience.x.did.0")
    assert fact is not None and fact.text.endswith("Built a tool: fast and small")
    assert any("quote this item" in w for w in bank.warnings)


def test_invalid_banks() -> None:
    with pytest.raises(FactsError):
        build_bank({"projects": [{"id": "a", "name": "n", "use_in_drafts": "no"}]})
    with pytest.raises(FactsError):
        build_bank({"projects": [{"id": "bad id", "name": "n"}]})
    with pytest.raises(FactsError):
        build_bank([1, 2])  # type: ignore[arg-type]


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(FactsError, match="duplicate"):
        build_bank({"projects": [{"id": "a", "name": "one"}, {"id": "a", "name": "two"}]})


def test_availability_mismatch_warns_and_keeps_style_constants() -> None:
    data = {"meta": {"decisions": {"availability": "Available from May.", "availability_it": "x"}}}
    bank = build_bank(data)
    assert bank.availability == AVAILABILITY_SENTENCE
    assert any("differs" in w for w in bank.warnings)
    missing = build_bank({"identity": {"name": "A"}})
    assert missing.availability == AVAILABILITY_SENTENCE
    assert any("missing" in w for w in missing.warnings)


def test_example_bank_matches_style_constants() -> None:
    raw = yaml.safe_load(EXAMPLE.read_text("utf-8"))
    decisions = raw["meta"]["decisions"]
    assert decisions["availability"] == AVAILABILITY_SENTENCE["en"]
    assert decisions["availability_it"] == AVAILABILITY_SENTENCE["it"]
    bank = load_facts(EXAMPLE)
    assert bank.warnings == [] and bank.source == "facts.example.yaml"
    assert bank.excluded  # the example shows use_in_drafts: false
    assert any(p.id.endswith(".grades") for p in bank.prohibitions)


def test_resolution_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RADAR_FACTS_PATH", raising=False)
    monkeypatch.setenv("RADAR_CONFIG_DIR", str(tmp_path))
    radar_config.get_settings.cache_clear()
    monkeypatch.setattr("radar.db.get_setting", lambda key: None)
    (tmp_path / "facts.example.yaml").write_text("identity:\n  name: Example One\n", "utf-8")
    assert load_facts().name == "Example One"
    (tmp_path / "facts.yaml").write_text("identity:\n  name: Real One\n", "utf-8")
    bank = load_facts()
    assert bank.name == "Real One" and bank.source == "facts.yaml"
    other = tmp_path / "other.yaml"
    other.write_text("identity:\n  name: Explicit\n", "utf-8")
    monkeypatch.setenv("RADAR_FACTS_PATH", str(other))
    radar_config.get_settings.cache_clear()
    assert load_facts().name == "Explicit"


def test_missing_bank_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RADAR_FACTS_PATH", raising=False)
    monkeypatch.setenv("RADAR_CONFIG_DIR", str(tmp_path))
    radar_config.get_settings.cache_clear()
    monkeypatch.setattr("radar.db.get_setting", lambda key: None)
    bank = load_facts()
    assert bank.source == "missing" and bank.availability == AVAILABILITY_SENTENCE
    assert [f.id for f in bank.facts] == [
        "meta.decisions.availability",
        "meta.decisions.availability_it",
    ]


def test_select_facts_keeps_core_and_relevant() -> None:
    bank = load_facts(EXAMPLE)
    every = select_facts(bank, "", 10**9)
    assert len(every) == len(bank.facts)
    small = select_facts(bank, "gradient boosting late deliveries logistics", 2500)
    ids = [f.id for f in small]
    assert "identity.name" in ids and "meta.decisions.availability" in ids
    assert "projects.late_deliveries.what" in ids
    assert len(small) < len(bank.facts)
    assert ids == [f.id for f in bank.facts if f.id in set(ids)]  # original order
