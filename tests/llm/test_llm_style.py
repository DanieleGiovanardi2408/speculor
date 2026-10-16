from __future__ import annotations

import pytest

from radar.llm import style

EM = "—"
EN = "–"


def test_em_dash_clause_becomes_comma() -> None:
    out = style.fix_dashes(f"I built a pipeline {EM} in Python {EM} for forecasting.")
    assert EM not in out
    assert out == "I built a pipeline, in Python, for forecasting."


def test_unspaced_em_dash_and_sentence_end() -> None:
    out = style.fix_dashes(f"Small team{EM}three people{EM}.")
    assert EM not in out
    assert out == "Small team, three people."


def test_en_dash_ranges_become_hyphen() -> None:
    assert style.fix_dashes(f"February{EN}April 2027") == "February-April 2027"
    assert style.fix_dashes(f"2026 {EN} 2027") == "2026-2027"
    assert style.fix_dashes(f"10{EM}12 weeks") == "10-12 weeks"


def test_dash_bullets_and_spaced_en_dash() -> None:
    assert style.fix_dashes(f"{EM} first\n{EN} second") == "- first\n- second"
    assert style.fix_dashes(f"Milan {EN} hybrid") == "Milan, hybrid"
    assert style.fix_dashes("one -- two") == "one, two"


def test_check_reports_dashes() -> None:
    issues = style.check(f"Hello {EM} there {EN} you", "text", "en")
    assert "contains an em dash" in issues
    assert "contains an en dash" in issues


@pytest.mark.parametrize(
    "text",
    [
        "I am writing to express my interest in the role.",
        "I'm writing to express my interest in this role.",
        "I am passionate about data.",
        "I want to leverage my skills.",
        "I like a dynamic environment.",
        "I am excited to apply.",
        "I thrive in a fast-paced environment.",
        "I am a team player.",
        "There is real synergy here.",
        "Con la presente mi candido.",
        "Sono appassionato di dati.",
        "Cerco un ambiente dinamico.",
    ],
)
def test_cliches_detected(text: str) -> None:
    assert style.find_cliches(text)
    assert any(i.startswith("cliche") for i in style.check(text, "text", "en"))


def test_cliche_word_boundaries_and_clean_text() -> None:
    assert style.find_cliches("Leverages are physics.") == []
    assert style.find_cliches("I built a forecasting model in Python.") == []
    assert style.find_cliches("I’m writing to express my interest") != []


def test_cover_letter_word_limit_trims_at_sentence_and_keeps_signoff() -> None:
    first = "Available full-time from February 2027, start flexible until April 2027 for this role."
    body = " ".join(f"Sentence number {i} has exactly seven words." for i in range(60))
    text = f"{first}\n\n{body}\n\nBest regards,\nMara"
    fixed, issues = style.fix(text, "cover_letter", "en")
    assert style.word_count(fixed) <= 250
    assert fixed.endswith("Best regards,\nMara")
    assert fixed.splitlines()[0] == first
    assert any(i.startswith("trimmed from") for i in issues)
    # what remains after the trim ends on a full sentence
    assert fixed.split("\n\n")[-2].endswith(".")
    assert style.check(fixed, "cover_letter", "en") == []


@pytest.mark.parametrize(
    ("kind", "limit"),
    [("linkedin_connection_note", 280), ("linkedin_message", 600), ("followup", 600)],
)
def test_char_limits(kind: str, limit: int) -> None:
    text = " ".join("This is one short sentence." for _ in range(40))
    assert any(i.startswith("too long") for i in style.check(text, kind, "en"))
    fixed, issues = style.fix(text, kind, "en")
    assert len(fixed) <= limit
    assert fixed.endswith(".")
    assert style.check(fixed, kind, "en") == []
    assert any(i.startswith("trimmed") for i in issues)


def test_hard_cut_when_first_sentence_too_long() -> None:
    text = "word " * 100
    fixed, _ = style.fix(text, "linkedin_connection_note", "en")
    assert len(fixed) <= 280


AV_EN = "Available full-time from February 2027, start flexible until April 2027."
AV_IT = "Disponibile full-time da febbraio 2027, con inizio flessibile fino ad aprile 2027."


def test_cover_letter_availability_first_line() -> None:
    ok_en = f"{AV_EN}\n\nMore text."
    assert style.check(ok_en, "cover_letter", "en") == []
    ok_en_dash = f"Available full{EN}time from February 2027, start flexible until April 2027."
    assert style.has_availability_first_line(ok_en_dash, "en")
    ok_it = f"{AV_IT}\n\nAltro."
    assert style.check(ok_it, "cover_letter", "it") == []
    ok_salutation = f"Dear Hiring Team,\n{AV_EN}"
    assert style.has_availability_first_line(ok_salutation, "en")
    bad = f"I like your company.\n{AV_EN}"
    assert any("availability" in i for i in style.check(bad, "cover_letter", "en"))


def test_fix_inserts_availability_line() -> None:
    fixed, issues = style.fix("Dear Team,\nI like your forecasting work.", "cover_letter", "en")
    lines = fixed.splitlines()
    assert lines[0] == "Dear Team,"
    assert lines[1] == "Available full-time from February 2027, start flexible until April 2027."
    assert "availability line inserted at the top" in issues
    fixed_it, _ = style.fix("Mi interessa il vostro lavoro.", "cover_letter", "it")
    assert fixed_it.startswith(
        "Disponibile full-time da febbraio 2027, con inizio flessibile fino ad aprile 2027."
    )


def test_fix_never_outputs_em_dash_and_reports_remaining_cliches() -> None:
    fixed, issues = style.fix(f"I am passionate {EM} really.", "linkedin_message", "en")
    assert EM not in fixed
    assert issues == ['cliche: "passionate"']


def test_empty_text() -> None:
    assert style.check("   ", "followup", "en") == ["empty text"]


def test_fix_separates_availability_and_normalizes_hyphens() -> None:
    text = f"{AV_EN} I built a churn‑risk model.\nBest,\nAlex"
    fixed, _ = style.fix(text, "cover_letter", "en")
    assert fixed.startswith(f"{AV_EN}\n\nI built a churn-risk model.")
    assert "‑" not in fixed


def test_new_cliches_are_detected() -> None:
    assert style.find_cliches("I am drawn to your mission and eager to contribute.")
    assert style.find_cliches("Sono motivato a contribuire al team.")
