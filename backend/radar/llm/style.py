"""Deterministic writing rules shared by every generated text.

* never output an em dash (U+2014); en dashes used as dashes become commas/hyphens
* banned cliches (English + Italian)
* length limits: cover letter <= 250 words, LinkedIn connection note <= 280 chars,
  LinkedIn message / follow-up / email <= 600 chars (trimmed at a sentence boundary)
* cover letter: the first line states availability (AVAILABILITY_SENTENCE below):
  "Available full-time from February 2027, start flexible until April 2027."

``check`` lists violations; ``fix`` repairs what can be repaired deterministically and returns the
new text plus notes about edits (trim, inserted line) and any violations still present.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

Kind = Literal[
    "cover_letter",
    "form_answer",
    "linkedin_connection_note",
    "linkedin_message",
    "followup",
    "email",
    "text",
]
Language = Literal["it", "en"]

WORD_LIMITS: dict[str, int] = {"cover_letter": 250, "form_answer": 120}
CHAR_LIMITS: dict[str, int] = {
    "linkedin_connection_note": 280,
    "linkedin_message": 600,
    "followup": 600,
    "email": 600,
}

AVAILABILITY_PHRASE: dict[str, str] = {
    "en": "full-time from February 2027, start flexible until April 2027",
    "it": "full-time da febbraio 2027, con inizio flessibile fino ad aprile 2027",
}
AVAILABILITY_SENTENCE: dict[str, str] = {
    "en": "Available full-time from February 2027, start flexible until April 2027.",
    "it": "Disponibile full-time da febbraio 2027, con inizio flessibile fino ad aprile 2027.",
}

EM_DASH = "—"
EN_DASH = "–"
HORIZONTAL_BAR = "―"
_ANY_DASH = f"[{EM_DASH}{EN_DASH}{HORIZONTAL_BAR}]"

CLICHES_EN: tuple[str, ...] = (
    "I am writing to express my interest",
    "I'm writing to express my interest",
    "I am writing to apply",
    "I am excited to apply",
    "I'm excited to apply",
    "I am thrilled",
    "passionate",
    "passion for",
    "leverage",
    "leveraging",
    "dynamic environment",
    "fast-paced environment",
    "fast paced environment",
    "team player",
    "synergy",
    "synergies",
    "results-driven",
    "self-starter",
    "go-getter",
    "think outside the box",
    "hit the ground running",
    "perfect fit",
    "dream job",
    "to whom it may concern",
    "delve",
    "cutting-edge",
)
CLICHES_IT: tuple[str, ...] = (
    "con la presente",
    "appassionato",
    "appassionata",
    "appassionati",
    "passione per",
    "ambiente dinamico",
    "ambiente stimolante",
    "ambiente frenetico",
    "giovane e dinamico",
    "giovane e dinamica",
    "sinergia",
    "sinergie",
    "mettermi in gioco",
    "sono entusiasta di candidarmi",
    "scrivo per esprimere il mio interesse",
    "a chi di competenza",
    "fortemente motivato",
    "fortemente motivata",
    "fare la differenza",
)
_CLICHE_RES: list[tuple[str, re.Pattern[str]]] = [
    (c, re.compile(rf"(?<!\w){re.escape(c.lower())}(?!\w)")) for c in CLICHES_EN + CLICHES_IT
]

_SALUTATION = re.compile(
    r"^(dear|hello|hi|good morning|gentile|gentili|spettabile|egregio|egregia|buongiorno|ciao)\b",
    re.I,
)


# --------------------------------------------------------------------------- dashes


def fix_dashes(text: str) -> str:
    """Remove em dashes and en dashes used as dashes. Never returns U+2014."""
    t = text
    # Line-leading dash used as a bullet.
    t = re.sub(rf"(?m)^([ \t]*){_ANY_DASH}[ \t]*", r"\1- ", t)
    # Numeric ranges: 2026-2027, 10-12.
    t = re.sub(rf"(\d)[ \t]*{_ANY_DASH}[ \t]*(\d)", r"\1-\2", t)
    # Unspaced en dash between words is a range/compound: February-April.
    t = re.sub(rf"(\w){EN_DASH}(\w)", r"\1-\2", t)
    # Everything else is a clause dash: becomes a comma.
    t = re.sub(rf"[ \t]*{_ANY_DASH}+[ \t]*", ", ", t)
    t = re.sub(r"(?<=\S)[ \t]+--[ \t]+(?=\S)", ", ", t)
    # Tidy punctuation produced by the replacements.
    t = re.sub(r",[ \t]*,", ",", t)
    t = re.sub(r",[ \t]*([.!?;:])", r"\1", t)
    t = re.sub(r"([.!?;:])[ \t]*,", r"\1", t)
    t = re.sub(r"(?m)^,[ \t]*", "", t)
    t = re.sub(r"(?m)[ \t]+$", "", t)
    return t.replace(EM_DASH, ",")  # belt and braces


# --------------------------------------------------------------------------- detectors


def find_cliches(text: str) -> list[str]:
    folded = text.lower().replace("’", "'")
    return [phrase for phrase, rx in _CLICHE_RES if rx.search(folded)]


def word_count(text: str) -> int:
    return len(text.split())


def _norm_line(line: str) -> str:
    t = line.lower().replace(EN_DASH, "-").replace(EM_DASH, "-").replace(HORIZONTAL_BAR, "-")
    t = re.sub(r"\s*-\s*", "-", t)
    t = re.sub(r"full\s*time", "full-time", t)
    return re.sub(r"\s+", " ", t)


def _first_content_line_index(lines: list[str]) -> int | None:
    """Index of the first non-empty line that is not a short salutation ("Dear X,")."""
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if _SALUTATION.match(s) and len(s.split()) <= 8 and s.endswith((",", ":", "!")):
            continue
        return i
    return None


def has_availability_first_line(text: str, language: str) -> bool:
    lines = text.splitlines()
    idx = _first_content_line_index(lines)
    if idx is None:
        return False
    phrase = AVAILABILITY_PHRASE["it" if language == "it" else "en"].lower()
    return phrase in _norm_line(lines[idx])


def _measure(kind: str) -> tuple[Callable[[str], int], int] | None:
    if kind in WORD_LIMITS:
        return word_count, WORD_LIMITS[kind]
    if kind in CHAR_LIMITS:
        return len, CHAR_LIMITS[kind]
    return None


def check(text: str, kind: str, language: str) -> list[str]:
    """Violations of the writing rules (empty list = compliant)."""
    issues: list[str] = []
    if not text.strip():
        return ["empty text"]
    if EM_DASH in text or HORIZONTAL_BAR in text:
        issues.append("contains an em dash")
    if EN_DASH in text:
        issues.append("contains an en dash")
    issues.extend(f'cliche: "{c}"' for c in find_cliches(text))
    measure = _measure(kind)
    if measure is not None:
        fn, limit = measure
        size = fn(text)
        if size > limit:
            unit = "words" if fn is word_count else "chars"
            issues.append(f"too long: {size} {unit} (max {limit})")
    if kind == "cover_letter" and not has_availability_first_line(text, language):
        phrase = AVAILABILITY_PHRASE["it" if language == "it" else "en"]
        issues.append(f'first line must state availability "{phrase}"')
    return issues


# --------------------------------------------------------------------------- fixers

_SENTENCE_END = re.compile(r"[.!?](?:[\"')\]]*)(?=\s|$)|\n")


def trim_to_limit(
    text: str, measure: Callable[[str], int], limit: int, *, keep_signoff: bool
) -> str:
    """Cut ``text`` at the last sentence boundary that fits ``limit``.

    With ``keep_signoff`` a short final paragraph (e.g. "Best regards,\\nName") is preserved.
    """
    if measure(text) <= limit:
        return text
    body, signoff = text, ""
    if keep_signoff:
        paras = re.split(r"\n\s*\n", text.strip())
        if len(paras) > 1 and word_count(paras[-1]) <= 8:
            body = "\n\n".join(paras[:-1])
            signoff = paras[-1]
    sep = "\n\n" if signoff else ""
    best = ""
    for m in _SENTENCE_END.finditer(body):
        candidate = body[: m.end()].rstrip()
        if candidate and measure(candidate + sep + signoff) <= limit:
            best = candidate
        else:
            break
    if not best:  # first sentence alone is too long: hard cut at a word boundary
        words = body.split()
        while words and measure(" ".join(words) + sep + signoff) > limit - 1:
            words.pop()
        best = " ".join(words).rstrip(",;:") + ("." if words else "")
    return f"{best}{sep}{signoff}".strip()


def _insert_availability(text: str, language: str) -> str:
    sentence = AVAILABILITY_SENTENCE["it" if language == "it" else "en"]
    lines = text.strip().splitlines()
    idx = _first_content_line_index(lines)
    if idx is None:
        return f"{sentence}\n\n{text.strip()}".strip()
    head, tail = lines[:idx], lines[idx:]
    return "\n".join([*head, sentence, "", *tail]).strip()


def fix(text: str, kind: str, language: str) -> tuple[str, list[str]]:
    """Apply deterministic fixes. Returns (text, notes + remaining violations)."""
    notes: list[str] = []
    t = fix_dashes(text).strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    if kind == "cover_letter" and t and not has_availability_first_line(t, language):
        t = _insert_availability(t, language)
        notes.append("availability line inserted at the top")
    measure = _measure(kind)
    if measure is not None:
        fn, limit = measure
        before = fn(t)
        if before > limit:
            t = trim_to_limit(t, fn, limit, keep_signoff=kind == "cover_letter")
            unit = "words" if fn is word_count else "chars"
            notes.append(
                f"trimmed from {before} to {fn(t)} {unit} (max {limit}): review the ending"
            )
    return t, notes + check(t, kind, language)
