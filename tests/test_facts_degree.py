import re
from datetime import date

import pytest

from radar.facts import build_bank, degree_completed_patterns, degree_in_progress
from radar.llm.verify import precheck

EDU = [
    {
        "id": "msc",
        "school": "Example University, Rome",
        "degree": "MSc candidate in Data Science (expected July 2027)",
        "dates": "Sep 2025 - Jul 2027 (expected)",
    },
    {"id": "bsc", "school": "Other College", "degree": "BSc in Economics", "dates": "2022 - 2025"},
]


def _hits(sentence: str) -> bool:
    pats = degree_completed_patterns(EDU[0]["degree"], EDU[0]["school"])
    return any(re.search(p, sentence, re.I) for p in pats)


@pytest.mark.parametrize(
    "sentence",
    [
        "I hold an MSc in Data Science & Management (expected July 2027).",
        "I graduated from Example University with top marks.",
        "As an MSc graduate, I bring strong statistics.",
        "I am a holder of a master's degree in data science.",
        "I have completed my MSc in Data Science.",
        "I earned an MSc in Data Science last year.",
        "Ho conseguito la laurea magistrale in Data Science.",
        "Mi sono laureato alla Example University.",
    ],
)
def test_completed_degree_claims_are_flagged(sentence: str) -> None:
    assert _hits(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "I am finishing my MSc in Data Science (expected July 2027).",
        "I am an MSc candidate in Data Science at Example University.",
        "The MSc graduate programme starts in April 2027.",
        "I graduated from Other College in 2025.",
        "Exams are completed by March 2027, then the thesis.",
        "Sto completando la laurea magistrale in Data Science.",
    ],
)
def test_correct_degree_statements_pass(sentence: str) -> None:
    assert not _hits(sentence)


def test_in_progress_detection() -> None:
    assert degree_in_progress(EDU[0])
    assert not degree_in_progress(EDU[1], today=date(2026, 10, 17))
    assert degree_in_progress({"degree": "MSc", "dates": "2025 - 2028"}, today=date(2026, 10, 17))


def test_precheck_flags_completed_degree() -> None:
    bank = build_bank({"identity": {"name": "Alex Example"}, "education": EDU})
    found = precheck(
        bank, [("cover_letter", "I hold an MSc in Data Science (expected July 2027).")]
    )
    assert found and found[0]["verdict"] == "unsupported"
    assert found[0]["fact_id"] == "education.msc.degree_status"
    assert not precheck(bank, [("cover_letter", "I am finishing my MSc in Data Science.")])
