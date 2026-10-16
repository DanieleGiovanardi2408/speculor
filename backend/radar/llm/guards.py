"""Deterministic caps applied to the LLM priority after scoring.

The prompt asks for the same rubric, but a small model sometimes rates a French-language or a
generic software role "Alta". These caps make the rubric hold whatever the model answers:

* Language (the user speaks Italian and English only):
  - clearly required: a ``language_required`` filter hit (kept even when the posting was
    overridden by hand: the override unhides the posting, it does not dispute the language),
    or the LLM lists a language other than Italian/English in ``language_requirements``
    without a nice-to-have qualifier -> **Bassa** at most;
  - probably required: the ``posting_language`` flag (description written mostly in French,
    German, Spanish, Dutch or Portuguese, no explicit requirement found) -> **Media** at most.
    "Probably" stays Media rather than Bassa because many such postings are bilingual or the
    team works in English; the warning tells the user to check.
* Role: :meth:`Relevance.cap` -> adjacent roles (generic software engineering, QA, fullstack,
  sales, BI/reporting only) are **Media** at most.

Every cap that lowers the priority adds an Italian warning.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from radar.llm.relevance import Relevance

GUARD_VERSION = 1  # stored on Score; bump when the caps change to find stale scores
RANK = {"Bassa": 0, "Media": 1, "Alta": 2}
LANGUAGE_REQUIRED_IDS = frozenset({"language_required"})
LANGUAGE_PROBABLE_IDS = frozenset({"posting_language"})

# Italian label -> words that name the language (English, Italian, native, other spellings).
FOREIGN_LANGUAGES: dict[str, tuple[str, ...]] = {
    "francese": ("french", "francese", "français", "francais", "französisch", "francés"),
    "tedesco": ("german", "tedesco", "deutsch", "allemand", "alemán"),
    "spagnolo": ("spanish", "spagnolo", "español", "espanol", "castellano", "espagnol"),
    "olandese": ("dutch", "olandese", "nederlands", "néerlandais", "flemish", "fiammingo"),
    "portoghese": ("portuguese", "portoghese", "português", "portugues", "portugais"),
    "polacco": ("polish", "polacco", "polski"),
    "svedese": ("swedish", "svedese", "svenska"),
    "danese": ("danish", "danese", "dansk"),
    "norvegese": ("norwegian", "norvegese", "norsk"),
    "finlandese": ("finnish", "finlandese", "suomi"),
    "ceco": ("czech", "ceco", "čeština"),
    "greco": ("greek", "greco"),
    "rumeno": ("romanian", "rumeno", "română"),
    "ungherese": ("hungarian", "ungherese", "magyar"),
    "russo": ("russian", "russo"),
    "turco": ("turkish", "turco"),
    "arabo": ("arabic", "arabo"),
    "cinese": ("chinese", "mandarin", "cinese", "cantonese"),
    "giapponese": ("japanese", "giapponese"),
}
_NAME_RX = {
    label: re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b", re.I)
    for label, words in FOREIGN_LANGUAGES.items()
}
_ALLOWED_RX = re.compile(r"\b(?:english|inglese|italian|italiano)\b", re.I)
_SOFT_RX = re.compile(
    r"\bplus\b|nice[- ]to[- ]have|\bpreferred\b|\bpreferabl\w*|\boptional\b|\bbonus\b"
    r"|\badvantage\w*|\bdesirable\b|\bwelcome\b|\bnot\s+required\b|\bideally\b|\bbeneficial\b"
    r"|\bgradit[oa]\b|\bpreferibil\w*|\bfacoltativ\w*|\bvantaggio\b|\bapprezzat\w*"
    r"|\bnon\s+(?:richiest|obbligatori|necessari)\w*|\batout\b|\bvon\s+vorteil\b",
    re.I,
)
_OR_RX = re.compile(r"\b(?:or|o|oppure|ou|oder)\b|/", re.I)
NO_GAP = "Nessun ostacolo evidente."


@dataclass(frozen=True)
class FlagFact:
    """The parts of a FilterResult the guards need."""

    rule_id: str
    reason: str
    overridden: bool = False


def required_languages(items: Iterable[str]) -> list[str]:
    """Italian labels of the non-Italian/English languages the LLM reports as required.

    "German (nice to have)" and "English or French" do not count.
    """
    out: list[str] = []
    for item in items:
        if _SOFT_RX.search(item) or (_ALLOWED_RX.search(item) and _OR_RX.search(item)):
            continue
        for label, rx in _NAME_RX.items():
            if rx.search(item) and label not in out:
                out.append(label)
    return out


def _flag_language(reason: str) -> str:
    """Italian label of the language in a filter reason ("French required", "...: tedesco")."""
    for label, rx in _NAME_RX.items():
        if rx.search(reason) or label in reason.lower():
            return label
    return "una lingua straniera"


@dataclass
class GuardResult:
    priority: str
    warnings: list[str]
    what_doesnt_fit: str
    applied: list[str]  # "language_required" | "language_probable" | "role_cap"


def _lower(current: str, cap: str) -> bool:
    return RANK[current] > RANK[cap]


def apply_guards(
    priority: str,
    *,
    title: str,
    language_requirements: Iterable[str],
    flags: Iterable[FlagFact],
    relevance: Relevance,
    warnings: Iterable[str] = (),
    what_doesnt_fit: str = "",
) -> GuardResult:
    """Cap ``priority`` (Alta/Media/Bassa) by the language and role rules above."""
    flags = list(flags)
    out = GuardResult(priority, list(warnings), what_doesnt_fit, [])
    original = priority

    required = [_flag_language(f.reason) for f in flags if f.rule_id in LANGUAGE_REQUIRED_IDS]
    for label in required_languages(language_requirements):
        if label not in required:
            required.append(label)
    probable = [
        _flag_language(f.reason)
        for f in flags
        if f.rule_id in LANGUAGE_PROBABLE_IDS and not f.overridden
    ]
    probable = [p for p in probable if p not in required]

    def lower(cap: str, applied: str, warning: str, gap: str) -> None:
        if not _lower(out.priority, cap):
            return
        out.priority = cap
        out.applied.append(applied)
        out.warnings.append(f"{warning} (il modello aveva indicato {original}).")
        if not out.what_doesnt_fit.strip() or out.what_doesnt_fit.strip() == NO_GAP:
            out.what_doesnt_fit = gap

    if required:
        langs = ", ".join(required)
        lower(
            "Bassa",
            "language_required",
            f"Lingua richiesta: {langs}, priorità limitata a Bassa",
            f"Richiede {langs}, che non è tra le lingue del profilo.",
        )
    if probable:
        langs = ", ".join(probable)
        lower(
            "Media",
            "language_probable",
            f"Lingua probabilmente richiesta: {langs}, priorità limitata a Media",
            f"L'annuncio è scritto in {langs}: probabilmente la lingua serve nel lavoro.",
        )
    if (cap := relevance.cap(title)) is not None:
        area = relevance.cap_keyword(title) or "ruolo adiacente"
        lower(
            cap,
            "role_cap",
            f"Ruolo adiacente ai target ({area}), priorità limitata a {cap}",
            "Ruolo adiacente, non nelle aree principali (AI, dati, consulenza, strategia, VC).",
        )
    return out
