"""Fact bank: the only source of candidate facts for every draft.

``config/facts.yaml`` (gitignored; schema in ``config/facts.example.yaml``) is flattened into a
list of facts with stable ids, e.g. ``experience.acme.did.0`` or
``experience.acme.products.widget.scale_citable``. Path segments are mapping keys; list items
use their ``id`` when they have one, else their index. Rules:

* an item with ``use_in_drafts: false`` is skipped with its whole subtree; its name becomes a
  prohibition (never mention it);
* a field whose value is ``not_stated`` and every ``do_not_claim`` entry become prohibitions;
* language levels produce deterministic prohibitions ("native" English when the level is not
  native, languages that are not listed);
* ``meta.decisions.availability`` / ``availability_it`` are the availability sentences. They must
  equal ``radar.llm.style.AVAILABILITY_SENTENCE`` (the deterministic style checks use those); a
  mismatch is reported in ``warnings`` and the style constants win. Missing facts fall back to the
  constants too.
* keys starting with ``source`` (provenance notes) and ``meta`` / ``drafting_rules`` are not facts;
  ``drafting_rules`` are passed to the drafting prompt as the user's own rules.

Resolution: ``RADAR_FACTS_PATH`` -> ``config/facts.yaml`` -> DB setting ``config:facts`` ->
``config/facts.example.yaml``. Never log fact texts.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from radar.config import get_settings
from radar.llm.style import AVAILABILITY_SENTENCE

log = logging.getLogger(__name__)

NOT_STATED = "not_stated"
PROHIBIT_KEYS = frozenset({"do_not_claim"})
SKIP_KEYS = frozenset({"id", "use_in_drafts"})
NON_FACT_TOP_KEYS = frozenset({"meta", "drafting_rules"})
LABEL_KEYS = ("name", "org", "school")
# Lists of short scalars rendered as ONE fact (e.g. a stack) instead of one fact per item.
JOINED_LIST_KEYS = frozenset({"stack", "tags", "courses", "common_stack", "quality", "sources"})
JOINED_LIST_PREFIXES = ("skills.",)
_ID_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")

_GRADE_KEYS = frozenset({"grades", "grade", "gpa", "average", "grade_average", "voto", "media"})
GRADE_PATTERNS = (
    r"\bGPA\b",
    r"\bgrade (?:point )?average\b",
    r"\baverage grade\b",
    r"\b\d{2,3}\s?/\s?110\b",
    r"\b110 (?:e|cum) lode\b",
    r"\bcum laude\b",
    r"\bmedia (?:dei voti|ponderata)\b",
)
NATIVE_ENGLISH_PATTERNS = (
    r"\bnative(?:[- ]level)?\s+(?:english|speaker of english)\b",
    # "fluent in English and native Italian" is fine: "native" must not qualify another language
    r"\benglish[^.\n]{0,25}\bnative\b(?!\s+(?:italian|italiano|speaker of italian))",
    r"\bmadrelingua inglese\b",
    r"\binglese[^.\n]{0,25}\bmadrelingua\b(?!\s+italian[oa])",
    r"\bbilingual\b",
    r"\bbilingue\b",
)
# Language name -> spellings (English, Italian, own) checked when the language is not listed.
KNOWN_LANGUAGES: dict[str, tuple[str, ...]] = {
    "french": ("french", "francese", "français", "francais"),
    "german": ("german", "tedesco", "deutsch"),
    "spanish": ("spanish", "spagnolo", "español", "espanol"),
    "dutch": ("dutch", "olandese", "nederlands"),
    "portuguese": ("portuguese", "portoghese", "português"),
    "chinese": ("chinese", "mandarin", "cinese"),
}


class FactsError(ValueError):
    """The fact bank file is not valid (not a mapping, duplicate ids, bad id segments)."""


@dataclass(frozen=True)
class Fact:
    id: str
    text: str


@dataclass(frozen=True)
class Prohibition:
    id: str
    text: str
    # Case-insensitive regexes for the deterministic pre-check (empty: LLM verifier only).
    patterns: tuple[str, ...] = ()


@dataclass
class FactBank:
    facts: list[Fact]
    prohibitions: list[Prohibition]
    excluded: list[str]  # ids of subtrees skipped because of use_in_drafts: false
    availability: dict[str, str]
    rules: list[str] = field(default_factory=list)  # drafting_rules from the file
    name: str = ""
    version: str | None = None
    source: str = "missing"  # facts.yaml | facts.example.yaml | db | <file name> | missing
    warnings: list[str] = field(default_factory=list)
    english_level: str | None = None

    def __post_init__(self) -> None:
        self._by_id = {f.id: f for f in self.facts}
        self._prohibited = {p.id: p for p in self.prohibitions}

    @property
    def ids(self) -> set[str]:
        return set(self._by_id)

    def get(self, fact_id: str) -> Fact | None:
        return self._by_id.get(fact_id)

    def prohibition(self, pid: str) -> Prohibition | None:
        return self._prohibited.get(pid)

    def first(self, prefix: str) -> Fact | None:
        return next((f for f in self.facts if f.id.startswith(prefix)), None)

    def availability_sentence(self, language: str) -> str:
        return self.availability["it" if language == "it" else "en"]

    def render_facts(self) -> str:
        return "\n".join(f"[{f.id}] {f.text}" for f in self.facts)

    def render_prohibitions(self) -> str:
        return "\n".join(f"[{p.id}] {p.text}" for p in self.prohibitions)

    @property
    def facts_text(self) -> str:
        return "\n".join(f.text for f in self.facts)


# --------------------------------------------------------------------------- flattening


def _humanize(key: str) -> str:
    return key.replace("_", " ")


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).strip()


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, dict | list)


def _label(node: dict[str, Any], inherited: str) -> str:
    for key in LABEL_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return inherited


def _patterns_for(key: str) -> tuple[str, ...]:
    return GRADE_PATTERNS if key.lower() in _GRADE_KEYS else ()


class _Flattener:
    def __init__(self) -> None:
        self.facts: list[Fact] = []
        self.prohibitions: list[Prohibition] = []
        self.excluded: list[str] = []
        self.seen: set[str] = set()
        self.warnings: list[str] = []

    def _add_fact(self, fid: str, text: str) -> None:
        if not text:
            return
        if fid in self.seen:
            raise FactsError(f"duplicate fact id {fid!r}")
        self.seen.add(fid)
        self.facts.append(Fact(fid, text))

    def _add_prohibition(self, p: Prohibition) -> None:
        if p.id in self.seen:
            raise FactsError(f"duplicate fact id {p.id!r}")
        self.seen.add(p.id)
        self.prohibitions.append(p)

    def walk(self, node: Any, path: str, key: str, label: str) -> None:
        if isinstance(node, dict):
            self._dict(node, path, label)
        elif isinstance(node, list):
            self._list(node, path, key, label)
        else:
            value = _scalar(node) if node is not None else ""
            if not value:
                return
            if value == NOT_STATED:
                self._add_prohibition(
                    Prohibition(
                        path,
                        f"{label + ', ' if label else ''}{_humanize(key)}: not stated in the "
                        "fact bank. Never claim any value.",
                        _patterns_for(key),
                    )
                )
                return
            if key in LABEL_KEYS or not label:
                self._add_fact(path, f"{_humanize(key)}: {value}")
            else:
                self._add_fact(path, f"{label}, {_humanize(key)}: {value}")

    def _dict(self, node: dict[str, Any], path: str, label: str) -> None:
        use = node.get("use_in_drafts", True)
        if not isinstance(use, bool):
            raise FactsError(f"{path}: use_in_drafts must be true or false")
        own_label = _label(node, label)
        if use is False:
            self.excluded.append(path)
            names = [
                str(node[k]).strip()
                for k in ("name", "id", "org")
                if isinstance(node.get(k), str) and str(node[k]).strip()
            ]
            patterns = tuple(rf"(?<!\w){re.escape(n)}(?!\w)" for n in dict.fromkeys(names))
            self._add_prohibition(
                Prohibition(
                    f"{path}.excluded",
                    f"Never mention {own_label or path} in any draft (excluded by the user).",
                    patterns,
                )
            )
            return
        for k, v in node.items():
            k = str(k)
            if k in SKIP_KEYS or k.startswith("source"):
                continue
            if not _ID_SEGMENT.match(k):
                raise FactsError(f"{path}: invalid key {k!r}")
            child = f"{path}.{k}" if path else k
            if k in PROHIBIT_KEYS:
                items = v if isinstance(v, list) else [v]
                for i, item in enumerate(items):
                    text = _scalar(item)
                    if text:
                        prefix = f"{own_label}: " if own_label else ""
                        self._add_prohibition(Prohibition(f"{child}.{i}", f"{prefix}{text}"))
                continue
            self.walk(v, child, k, own_label)

    def _unsplit(self, item: Any, path: str) -> Any:
        """An unquoted list item with ": " in it parses as a one-key mapping: rejoin it."""
        if isinstance(item, dict) and len(item) == 1:
            ((k, v),) = item.items()
            if not _ID_SEGMENT.match(str(k)) and _is_scalar(v):
                self.warnings.append(f"{path}: quote this item (it contains ': ')")
                return f"{k}: {_scalar(v)}" if v is not None else str(k)
        return item

    def _list(self, node: list[Any], path: str, key: str, label: str) -> None:
        if not node:
            return
        joined = key in JOINED_LIST_KEYS or any(
            (path + ".").startswith(p) for p in JOINED_LIST_PREFIXES
        )
        if joined and all(_is_scalar(x) for x in node):
            values = ", ".join(_scalar(x) for x in node if x is not None and _scalar(x))
            prefix = f"{label}, " if label else ""
            self._add_fact(path, f"{prefix}{_humanize(key)}: {values}")
            return
        for i, item in enumerate(node):
            seg = str(i)
            item = self._unsplit(item, f"{path}.{i}")
            if isinstance(item, dict) and item.get("id") is not None:
                seg = str(item["id"]).strip()
                if not _ID_SEGMENT.match(seg):
                    raise FactsError(f"{path}: invalid id {seg!r}")
            self.walk(item, f"{path}.{seg}", key, label)


# --------------------------------------------------------------------------- languages


def _language_levels(identity: dict[str, Any]) -> dict[str, str]:
    raw = identity.get("languages")
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            out[str(k).strip().lower()] = _scalar(v)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = _scalar(item.get("language") or item.get("name") or "").lower()
                if name:
                    out[name] = _scalar(item.get("level") or "")
            elif item is not None:
                parts = _scalar(item).split(maxsplit=1)
                if parts:
                    out[parts[0].lower()] = parts[1] if len(parts) > 1 else ""
    return out


def _language_prohibitions(levels: dict[str, str]) -> list[Prohibition]:
    out: list[Prohibition] = []
    english = levels.get("english")
    if english is not None and "native" not in english.lower():
        out.append(
            Prohibition(
                "identity.languages.english.level",
                f'English level is {english}: write "fluent English" or "{english}", '
                'never "native" or "bilingual".',
                NATIVE_ENGLISH_PATTERNS,
            )
        )
    listed = set(levels)
    missing = [lang for lang in KNOWN_LANGUAGES if lang not in listed]
    if levels and missing:
        names = ", ".join(lang.capitalize() for lang in missing)
        spellings = [s for lang in missing for s in KNOWN_LANGUAGES[lang]]
        out.append(
            Prohibition(
                "identity.languages.unlisted",
                f"The candidate speaks no other languages: never claim {names}.",
                tuple(rf"(?<!\w){re.escape(s)}(?!\w)" for s in spellings),
            )
        )
    return out


_IN_PROGRESS = re.compile(
    r"\b(?:expected|candidate|in corso|atteso|attesa|present|ongoing)\b", re.I
)
_YEAR = re.compile(r"\b(20\d{2})\b")
_MASTER_KW = (r"m\.?sc", r"master'?s?", r"laurea magistrale", r"magistrale")
_BACHELOR_KW = (r"b\.?sc", r"bachelor'?s?", r"laurea triennale", r"triennale")


def degree_in_progress(item: dict[str, Any], today: date | None = None) -> bool:
    """An education item not completed yet: "expected"/"candidate" in its text, or an end
    year after this year."""
    text = " ".join(_scalar(item.get(k) or "") for k in ("degree", "dates", "notes"))
    if _IN_PROGRESS.search(text):
        return True
    years = [int(y) for y in _YEAR.findall(_scalar(item.get("dates") or ""))]
    return bool(years) and max(years) > (today or date.today()).year


def degree_completed_patterns(degree: str, school: str) -> tuple[str, ...]:
    """Regexes for sentences that present an unfinished degree as completed ("I hold an MSc",
    "I graduated from Luiss", "MSc graduate", "ho conseguito la laurea magistrale")."""
    low = degree.lower()
    kws = list(_MASTER_KW if re.search(r"m\.?sc|master|magistrale", low) else ())
    if re.search(r"b\.?sc|bachelor|triennale", low):
        kws += list(_BACHELOR_KW)
    first = re.split(r"[\s,]+", school.strip())[0] if school.strip() else ""
    if len(first) >= 3:
        kws.append(re.escape(first.lower()))
    if not kws:
        kws = [r"degree", r"laurea"]
    kw = "|".join(kws)
    det = r"(?:an?|my|the|la|il|una|un)\s+"
    return (
        # "I hold an MSc", "holder of a master's", "holding a Luiss MSc"
        rf"\b(?:hold|holds|holding|holder of)\b[^.\n]{{0,40}}?\b(?:{kw})\b",
        # "I graduated from Luiss", "graduated with an MSc"
        rf"\bgraduated\b[^.\n]{{0,60}}?\b(?:{kw})\b",
        # "completed / earned / obtained / received / awarded an MSc"
        rf"\b(?:completed|earned|obtained|received|awarded)\s+{det}(?:[\w'-]+\s+){{0,2}}(?:{kw})\b",
        # "MSc graduate", "Luiss graduate" (not "graduate programme")
        rf"\b(?:{kw})\s+(?:graduate|holder)\b(?!\s+(?:programme|program|role|scheme|position|track|intern))",
        # Italian: "ho conseguito la laurea magistrale", "mi sono laureato alla Luiss"
        rf"\b(?:ho conseguito|conseguita|conseguito)\b[^.\n]{{0,40}}?\b(?:{kw})\b",
        rf"\b(?:mi sono laureat[oa]|sono laureat[oa]|laureat[oa] (?:in|presso|alla|a))"
        rf"\b[^.\n]{{0,60}}?\b(?:{kw})\b",
    )


def _degree_prohibitions(education: Any, today: date | None = None) -> list[Prohibition]:
    out: list[Prohibition] = []
    if not isinstance(education, list):
        return out
    for i, item in enumerate(education):
        if not isinstance(item, dict) or not degree_in_progress(item, today):
            continue
        degree = _scalar(item.get("degree") or "")
        school = _scalar(item.get("school") or "")
        ident = _scalar(item.get("id") or str(i))
        dates = _scalar(item.get("dates") or "")
        out.append(
            Prohibition(
                f"education.{ident}.degree_status",
                f'{degree} at {school} is not completed yet ({dates}): write "MSc candidate" or '
                '"finishing my MSc (expected ...)", never "I hold", "I graduated", "MSc graduate".',
                degree_completed_patterns(degree, school),
            )
        )
    return out


# --------------------------------------------------------------------------- build / load


def _availability(meta: dict[str, Any], warnings: list[str]) -> dict[str, str]:
    decisions = meta.get("decisions") if isinstance(meta.get("decisions"), dict) else {}
    assert isinstance(decisions, dict)
    out = dict(AVAILABILITY_SENTENCE)
    for lang, key in (("en", "availability"), ("it", "availability_it")):
        value = decisions.get(key)
        if value is None:
            warnings.append(f"meta.decisions.{key} missing: using the default sentence")
        elif _scalar(value) != AVAILABILITY_SENTENCE[lang]:
            warnings.append(
                f"meta.decisions.{key} differs from radar.llm.style.AVAILABILITY_SENTENCE"
                f"[{lang!r}]: update both (the style constant is used)"
            )
    return out


def build_bank(data: dict[str, Any], source: str = "inline") -> FactBank:
    """Flatten a parsed fact bank mapping. Raises FactsError when it is invalid."""
    if not isinstance(data, dict):
        raise FactsError("the fact bank must be a YAML mapping")
    warnings: list[str] = []
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    assert isinstance(meta, dict)
    availability = _availability(meta, warnings)

    flat = _Flattener()
    for lang, key in (("en", "availability"), ("it", "availability_it")):
        flat._add_fact(f"meta.decisions.{key}", availability[lang])
    for key, value in data.items():
        key = str(key)
        if key in NON_FACT_TOP_KEYS:
            continue
        if not _ID_SEGMENT.match(key):
            raise FactsError(f"invalid top-level key {key!r}")
        flat.walk(value, key, key, "")

    identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
    assert isinstance(identity, dict)
    name = _scalar(identity.get("name") or "")
    if not name:
        warnings.append("identity.name missing")
    levels = _language_levels(identity)
    for p in _language_prohibitions(levels):
        flat._add_prohibition(p)
    for p in _degree_prohibitions(data.get("education")):
        flat._add_prohibition(p)
    rules_raw = data.get("drafting_rules") or []
    rules = [_scalar(r) for r in rules_raw if r is not None] if isinstance(rules_raw, list) else []
    warnings.extend(flat.warnings)
    if not flat.facts:
        warnings.append("no facts found")
    return FactBank(
        facts=flat.facts,
        prohibitions=flat.prohibitions,
        excluded=flat.excluded,
        availability=availability,
        rules=rules,
        name=name,
        version=_scalar(meta.get("version")) if meta.get("version") is not None else None,
        source=source,
        warnings=warnings,
        english_level=levels.get("english"),
    )


def example_path() -> Path:
    return get_settings().config_dir / "facts.example.yaml"


def _read(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise FactsError(f"{path.name}: invalid YAML ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise FactsError(f"{path.name} must contain a YAML mapping")
    return data


def empty_bank() -> FactBank:
    return FactBank(
        facts=[
            Fact(f"meta.decisions.{k}", AVAILABILITY_SENTENCE[lang])
            for lang, k in (("en", "availability"), ("it", "availability_it"))
        ],
        prohibitions=[],
        excluded=[],
        availability=dict(AVAILABILITY_SENTENCE),
        source="missing",
        warnings=["no fact bank found: every claim must be a [CHECK: ...] marker"],
    )


def load_facts(path: Path | None = None) -> FactBank:
    """Load and flatten the fact bank (see module docstring for the resolution order)."""
    settings = get_settings()
    if path is not None:
        return build_bank(_read(path), path.name)
    if settings.facts_path is not None:
        return build_bank(_read(settings.facts_path), settings.facts_path.name)
    real = settings.config_dir / "facts.yaml"
    if real.exists():
        return build_bank(_read(real), real.name)
    try:
        from radar.db import get_setting  # local import: avoid cycle at module load

        stored = get_setting("config:facts")
    except Exception:  # no database (CLI checks, tests): fall through to the example
        stored = None
    if isinstance(stored, dict):
        return build_bank(stored, "db")
    example = example_path()
    if example.exists():
        return build_bank(_read(example), example.name)
    log.warning("fact bank missing: drafts will only contain [CHECK] markers")
    return empty_bank()


# --------------------------------------------------------------------------- selection

# Always rendered: who the candidate is and the skeleton of every entry.
_CORE_ID = re.compile(
    r"^(meta\.decisions\.|identity\.(name|headline|based_in|citizenship|availability|languages)"
    r"|(experience|education|projects)\.[^.]+\.(org|role|dates|name|school|degree|context)$"
    r"|experience\.[^.]+\.products\.[^.]+\.(name|what)$)"
)
_WORD = re.compile(r"[a-zà-ÿ0-9][a-zà-ÿ0-9+#.-]{2,}")
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "you",
        "our",
        "are",
        "will",
        "this",
        "that",
        "from",
        "have",
        "your",
        "team",
        "work",
        "role",
        "able",
        "per",
        "con",
        "che",
        "della",
        "delle",
        "degli",
        "nel",
        "nella",
        "sono",
        "una",
        "del",
        "dei",
    ]
)


def _words(text: str) -> set[str]:
    return {w.strip(".-") for w in _WORD.findall(text.lower())} - _STOP


def select_facts(bank: FactBank, query: str, budget_chars: int) -> list[Fact]:
    """Facts that fit ``budget_chars`` when rendered: core facts first, then the ones sharing
    most words with ``query`` (the posting, or the texts to verify). Keeps the original order.
    Deterministic; every fact is kept when the whole bank fits."""

    def size(f: Fact) -> int:
        return len(f.id) + len(f.text) + 4

    if sum(size(f) for f in bank.facts) <= budget_chars:
        return list(bank.facts)
    q = _words(query)
    chosen: set[str] = set()
    used = 0
    for f in bank.facts:
        if _CORE_ID.match(f.id):
            chosen.add(f.id)
            used += size(f)
    ranked = sorted(
        (f for f in bank.facts if f.id not in chosen),
        key=lambda f: (-len(_words(f.text) & q), bank.facts.index(f)),
    )
    for f in ranked:
        if used + size(f) > budget_chars:
            continue
        chosen.add(f.id)
        used += size(f)
    return [f for f in bank.facts if f.id in chosen]


def render(facts: Iterable[Fact]) -> str:
    return "\n".join(f"[{f.id}] {f.text}" for f in facts)
