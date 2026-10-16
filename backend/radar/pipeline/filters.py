"""Hard filters: deterministic reject/flag rules loaded from ``config/rules.yaml``.

``evaluate(posting, rules)`` is a pure function returning a list of :class:`Hit`.
``apply_filters`` / ``apply_filters_all`` persist hits as ``FilterResult`` rows and set
``Posting.filter_state`` (rejected > flagged > pass; a manual override is never undone).

The ``post_graduation`` flag marks graduate programmes / full-time roles that start after
graduation. It is not an exclusion: the API shows those postings in their own track.

Pattern lists live in YAML (see ``config/rules.example.yaml``); structural logic (start-date
parsing, experience years, country codes, language detection) is code parameterised by it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from sqlmodel import Session, col, select

from radar.models import FilterResult, Posting
from radar.pipeline import langdetect
from radar.pipeline.normalize import country_code
from radar.timeutil import today as utc_today

_FLAGS = re.IGNORECASE | re.UNICODE
_BOUNDARY = re.compile(r"\n|\||[.;!?](?=\s|$)")

YearMonth = tuple[int, int]

POST_GRADUATION_ID = "post_graduation"
"""Rule id of the post-graduation flag (the API's ``track`` is derived from it)."""


class PostingLike(Protocol):
    @property
    def title(self) -> str: ...
    @property
    def raw_text(self) -> str | None: ...
    @property
    def location(self) -> str | None: ...
    @property
    def country_code(self) -> str | None: ...
    @property
    def type(self) -> str | None: ...
    @property
    def start_info(self) -> str | None: ...


@dataclass(frozen=True)
class PostingText:
    """Plain posting stand-in (tests, previews)."""

    title: str
    raw_text: str | None = None
    location: str | None = None
    country_code: str | None = None
    type: str | None = None
    start_info: str | None = None


@dataclass(frozen=True)
class Hit:
    rule_id: str
    action: str  # reject | flag
    reason: str
    evidence: str | None


# --------------------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------------------


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, _FLAGS)


def _rx_list(values: Any) -> list[re.Pattern[str]]:
    return [_rx(str(v)) for v in (values or [])]


def _ym(value: Any) -> YearMonth:
    m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})\s*", str(value))
    if not m:
        raise ValueError(f"expected 'YYYY-MM', got {value!r}")
    return int(m.group(1)), int(m.group(2))


def _alternation(words: Iterable[str]) -> str:
    uniq = sorted({w.strip().lower() for w in words if str(w).strip()}, key=len, reverse=True)
    return "|".join(re.escape(w).replace("\\ ", " ").replace(" ", r"\s+") for w in uniq)


@dataclass
class RegexRule:
    id: str
    action: str
    reason: str
    fields: list[str]
    patterns: list[re.Pattern[str]]
    unless: list[re.Pattern[str]]
    unless_scope: str = "window"  # window | field
    unless_window: int = 80


@dataclass
class _Period:
    name: str
    months: tuple[int, int]
    needs_year: bool
    regex: re.Pattern[str]
    words: tuple[str, ...] = ()


@dataclass
class StartRule:
    fields: list[str]
    ok_from: YearMonth
    ok_to: YearMonth
    summer_from: YearMonth
    summer_to: YearMonth
    ids: dict[str, str]
    actions: dict[str, str]
    reasons: dict[str, str]
    keywords: list[re.Pattern[str]]
    weak_keywords: list[re.Pattern[str]]
    context_skip: list[re.Pattern[str]]
    context_skip_chars: int
    flexible: list[re.Pattern[str]]
    summer_title: list[re.Pattern[str]]
    summer_text: list[re.Pattern[str]]
    summer_unless: list[re.Pattern[str]]
    month_of: dict[str, int]
    month_rx: re.Pattern[str]
    periods: list[_Period]


@dataclass
class ExperienceRule:
    fields: list[str]
    reject_min: int
    flag_min: int
    ids: dict[str, str]
    reasons: dict[str, str]
    patterns: list[re.Pattern[str]]
    negations: list[re.Pattern[str]]
    soft: list[re.Pattern[str]]


@dataclass
class LocationRule:
    allowed: set[str]
    uk: set[str]
    ids: dict[str, str]
    actions: dict[str, str]
    reasons: dict[str, str]
    remote: list[re.Pattern[str]]
    sponsorship: list[re.Pattern[str]]
    sponsorship_neg: list[re.Pattern[str]]


@dataclass
class LanguageRule:
    fields: list[str]
    ids: dict[str, str]
    reasons: dict[str, str]
    names: dict[str, re.Pattern[str]]
    patterns: list[re.Pattern[str]]
    soft: list[re.Pattern[str]]
    alternatives_before: list[re.Pattern[str]]


@dataclass
class PostingLanguageRule:
    id: str
    action: str
    reason: str
    evidence: str
    fields: list[str]
    min_words: int
    min_share: float
    flag_languages: set[str]  # ISO codes (fr, de, ...)
    labels: dict[str, str]  # code -> name used in reason / evidence
    skip_if_required: set[str]  # rule ids: a hit for the same language makes this redundant


@dataclass
class EnrollmentRule:
    id: str
    action: str
    reason: str
    countries: dict[str, str]
    types: set[str]
    patterns: list[re.Pattern[str]]


@dataclass
class PostGraduationRule:
    action: str
    graduation: YearMonth  # the user's graduation month: starts after it are post-graduation
    reasons: dict[str, str]
    kinds: dict[str, str]
    internship_patterns: list[re.Pattern[str]]
    internship_types: set[str]
    graduate_types: set[str]
    graduate_title_patterns: list[re.Pattern[str]]
    graduate_text_patterns: list[re.Pattern[str]]
    open_to_students: list[re.Pattern[str]]
    fulltime_types: set[str]
    fulltime_patterns: list[re.Pattern[str]]
    after_graduation: list[re.Pattern[str]]
    after_graduation_unless: list[re.Pattern[str]]
    text_fields: list[str]
    trailing_start: re.Pattern[str] | None  # "<month|season|year> intake/start"
    supersedes: set[str]


@dataclass
class Rules:
    evidence_chars: int = 200
    regex_rules: list[RegexRule] = field(default_factory=list)
    start: StartRule | None = None
    experience: ExperienceRule | None = None
    location: LocationRule | None = None
    languages: LanguageRule | None = None
    posting_language: PostingLanguageRule | None = None
    enrollment: EnrollmentRule | None = None
    post_graduation: PostGraduationRule | None = None
    fingerprint: str = ""


def _enabled(section: Any) -> bool:
    return isinstance(section, Mapping) and bool(section.get("enabled", True))


def _compile_start(s: Mapping[str, Any]) -> StartRule:
    month_of: dict[str, int] = {}
    for num, words in (s.get("months") or {}).items():
        for w in words or []:
            month_of[str(w).lower()] = int(num)
    summer_year = str(_ym(s["summer_from"])[0])
    periods: list[_Period] = []
    for name, p in (s.get("periods") or {}).items():
        lo, hi = (int(x) for x in p["months"])
        alt = _alternation(str(w) for w in p.get("words") or [])
        rx = _rx(
            rf"(?<!real\s)\b(?:{alt})\b(?:\s+(?:of\s+|del\s+|de\s+|di\s+|dell'))?\s*(?P<y>20\d{{2}})?"
        )
        words = tuple(str(w) for w in p.get("words") or [])
        periods.append(_Period(str(name), (lo, hi), bool(p.get("needs_year", False)), rx, words))
    month_alt = _alternation(month_of)
    sep = r"(?:-|–|/|to|until|till|through|a|al|e|o|or|and|bis|à|au|et|ou|y|hasta|tot)"
    month_rx = _rx(
        rf"\b(?P<m1>{month_alt})\b\.?(?:\s*,?\s*(?P<y1>20\d{{2}}))?"
        rf"(?:\s*{sep}\s*(?:\d{{1,2}}(?:st|nd|rd|th|°)?\s+)?(?P<m2>{month_alt})\b\.?)?"
        rf"(?:\s*,?\s*(?:of\s+|de\s+|del\s+|di\s+)?(?P<y2>20\d{{2}}))?"
    )

    def year_sub(values: Any) -> list[re.Pattern[str]]:
        return [_rx(str(v).replace("{year}", summer_year)) for v in (values or [])]

    return StartRule(
        fields=list(s.get("fields") or ["title", "start_info", "raw_text"]),
        ok_from=_ym(s["ok_from"]),
        ok_to=_ym(s["ok_to"]),
        summer_from=_ym(s["summer_from"]),
        summer_to=_ym(s["summer_to"]),
        ids=dict(s.get("ids") or {}),
        actions=dict(s.get("actions") or {}),
        reasons=dict(s.get("reasons") or {}),
        keywords=_rx_list(s.get("keywords")),
        weak_keywords=_rx_list(s.get("weak_keywords")),
        context_skip=_rx_list(s.get("context_skip")),
        context_skip_chars=int(s.get("context_skip_chars", 35)),
        flexible=_rx_list(s.get("flexible")),
        summer_title=year_sub(s.get("summer_title_patterns")),
        summer_text=year_sub(s.get("summer_text_patterns")),
        summer_unless=_rx_list(s.get("summer_unless")),
        month_of=month_of,
        month_rx=month_rx,
        periods=periods,
    )


def _compile_languages(s: Mapping[str, Any]) -> LanguageRule:
    names = {
        str(lang): _rx(rf"\b(?:{_alternation(str(w) for w in words)})\b")
        for lang, words in (s.get("names") or {}).items()
    }
    all_alt = _alternation(str(w) for words in (s.get("names") or {}).values() for w in words)
    lang_group = f"(?:{all_alt})"
    patterns = [_rx(str(p).replace("{lang}", lang_group)) for p in s.get("required_patterns") or []]
    return LanguageRule(
        fields=list(s.get("fields") or ["title", "raw_text"]),
        ids=dict(s.get("ids") or {}),
        reasons=dict(s.get("reasons") or {}),
        names=names,
        patterns=patterns,
        soft=_rx_list(s.get("soft")),
        alternatives_before=_rx_list(s.get("alternatives_before")),
    )


# Older configs name languages ("French"); the detector uses ISO codes.
_LANG_CODES = {
    "english": "en",
    "italian": "it",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "dutch": "nl",
    "portuguese": "pt",
}
_DEFAULT_LABELS = {
    "en": "inglese",
    "it": "italiano",
    "fr": "francese",
    "de": "tedesco",
    "es": "spagnolo",
    "nl": "olandese",
    "pt": "portoghese",
}


def _lang_code(value: Any) -> str:
    v = str(value).strip().lower()
    return _LANG_CODES.get(v, v)


def _compile_posting_language(s: Mapping[str, Any]) -> PostingLanguageRule:
    labels = dict(_DEFAULT_LABELS)
    labels.update({_lang_code(k): str(v) for k, v in (s.get("labels") or {}).items()})
    return PostingLanguageRule(
        id=str(s.get("id", "posting_language")),
        action=str(s.get("action", "flag")),
        reason=str(s.get("reason", "lingua probabilmente richiesta: {lang}")),
        evidence=str(s.get("evidence", "testo: {shares} ({words} parole riconosciute)")),
        fields=list(s.get("fields") or ["title", "raw_text"]),
        min_words=int(s.get("min_words", s.get("min_tokens", 30))),
        min_share=float(s.get("min_share", 0.6)),
        flag_languages={_lang_code(x) for x in s.get("flag_languages") or []},
        labels=labels,
        skip_if_required={str(x) for x in s.get("skip_if_required") or ["language_required"]},
    )


def _compile_post_graduation(s: Mapping[str, Any], start: StartRule | None) -> PostGraduationRule:
    trailing: re.Pattern[str] | None = None
    keywords = [str(k) for k in s.get("trailing_start_keywords") or []]
    if start is not None and keywords:
        words = list(start.month_of)
        words += [w for p in start.periods if not p.needs_year for w in p.words]
        # Short words ("may", "mar", "set") only count with a year: "you may start".
        long_alt = _alternation(w for w in words if len(w) >= 4)
        short_alt = _alternation(words)
        year = r"\s*,?\s*(?:of\s+)?20\d{2}"
        trailing = _rx(
            rf"(?P<when>\b(?:{long_alt})\b\.?(?:{year})?|\b(?:{short_alt})\b\.?{year}"
            rf"|\b20\d{{2}})\s*[-–]?\s*(?:{'|'.join(keywords)})"
        )
    return PostGraduationRule(
        action=str(s.get("action", "flag")),
        graduation=_ym(s.get("graduation", "2027-07")),
        reasons=dict(s.get("reasons") or {}),
        kinds=dict(s.get("kinds") or {}),
        internship_patterns=_rx_list(s.get("internship_patterns")),
        internship_types={str(t) for t in s.get("internship_types") or []},
        graduate_types={str(t) for t in s.get("graduate_types") or []},
        graduate_title_patterns=_rx_list(s.get("graduate_title_patterns")),
        graduate_text_patterns=_rx_list(s.get("graduate_text_patterns")),
        open_to_students=_rx_list(s.get("open_to_students_patterns")),
        fulltime_types={str(t) for t in s.get("fulltime_types") or []},
        fulltime_patterns=_rx_list(s.get("fulltime_patterns")),
        after_graduation=_rx_list(s.get("after_graduation_patterns")),
        after_graduation_unless=_rx_list(s.get("after_graduation_unless")),
        text_fields=list(s.get("fields") or ["title", "start_info", "raw_text"]),
        trailing_start=trailing,
        supersedes={str(x) for x in s.get("supersedes") or []},
    )


def compile_rules(cfg: Mapping[str, Any]) -> Rules:
    """Compile a rules mapping (the parsed YAML) into :class:`Rules`."""
    rules = Rules(evidence_chars=int(cfg.get("evidence_chars", 200)))
    rules.fingerprint = hashlib.sha256(
        json.dumps(cfg, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    for r in cfg.get("regex_rules") or []:
        if not r.get("enabled", True):
            continue
        rules.regex_rules.append(
            RegexRule(
                id=str(r["id"]),
                action=str(r.get("action", "flag")),
                reason=str(r.get("reason", r["id"])),
                fields=list(r.get("fields") or ["title", "raw_text"]),
                patterns=_rx_list(r.get("patterns")),
                unless=_rx_list(r.get("unless")),
                unless_scope=str(r.get("unless_scope", "window")),
                unless_window=int(r.get("unless_window", 80)),
            )
        )
    if _enabled(cfg.get("start_date")):
        rules.start = _compile_start(cfg["start_date"])
    if _enabled(e := cfg.get("experience")):
        assert isinstance(e, Mapping)
        rules.experience = ExperienceRule(
            fields=list(e.get("fields") or ["title", "raw_text"]),
            reject_min=int(e.get("reject_min_years", 3)),
            flag_min=int(e.get("flag_min_years", 1)),
            ids=dict(e.get("ids") or {}),
            reasons=dict(e.get("reasons") or {}),
            patterns=_rx_list(e.get("patterns")),
            negations=_rx_list(e.get("negations")),
            soft=_rx_list(e.get("soft")),
        )
    if _enabled(loc := cfg.get("location")):
        assert isinstance(loc, Mapping)
        rules.location = LocationRule(
            allowed={str(c).upper() for c in loc.get("allowed_countries") or []},
            uk={str(c).upper() for c in loc.get("uk_countries") or ["GB"]},
            ids=dict(loc.get("ids") or {}),
            actions=dict(loc.get("actions") or {}),
            reasons=dict(loc.get("reasons") or {}),
            remote=_rx_list(loc.get("remote_patterns")),
            sponsorship=_rx_list(loc.get("sponsorship_patterns")),
            sponsorship_neg=_rx_list(loc.get("sponsorship_negations")),
        )
    if _enabled(cfg.get("languages")):
        rules.languages = _compile_languages(cfg["languages"])
    if _enabled(pl := cfg.get("posting_language")):
        assert isinstance(pl, Mapping)
        rules.posting_language = _compile_posting_language(pl)
    if _enabled(en := cfg.get("enrollment")):
        assert isinstance(en, Mapping)
        rules.enrollment = EnrollmentRule(
            id=str(en.get("id", "enrollment_agreement")),
            action=str(en.get("action", "flag")),
            reason=str(en.get("reason", "{country}: university agreement needed")),
            countries={str(k).upper(): str(v) for k, v in (en.get("countries") or {}).items()},
            types={str(t) for t in en.get("internship_types") or []},
            patterns=_rx_list(en.get("internship_patterns")),
        )
    if _enabled(pg := cfg.get("post_graduation")):
        assert isinstance(pg, Mapping)
        rules.post_graduation = _compile_post_graduation(pg, rules.start)
    return rules


def load_rules() -> Rules:
    from radar.config import load_yaml_config

    return compile_rules(load_yaml_config("rules"))


# --------------------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------------------


def _field(posting: PostingLike, name: str) -> str:
    value = getattr(posting, name, None)
    return value if isinstance(value, str) else ""


def _snippet(text: str, start: int, end: int, max_chars: int) -> str:
    pad = max(0, (max_chars - (end - start)) // 2)
    a, b = max(0, start - pad), min(len(text), end + pad)
    out = re.sub(r"\s+", " ", text[a:b]).strip()
    if a > 0:
        out = "…" + out
    if b < len(text):
        out += "…"
    if len(out) > max_chars:
        out = out[: max_chars - 1] + "…"
    return out


def _sentence(text: str, start: int, end: int, size: int) -> tuple[str, str]:
    """(before, after) context of a match, clipped at sentence/line boundaries."""
    before = text[max(0, start - size) : start]
    cuts = list(_BOUNDARY.finditer(before))
    if cuts:
        before = before[cuts[-1].end() :]
    after = text[end : end + size]
    cut = _BOUNDARY.search(after)
    if cut:
        after = after[: cut.start()]
    return before, after


def _any(patterns: Sequence[re.Pattern[str]], text: str) -> re.Match[str] | None:
    for p in patterns:
        m = p.search(text)
        if m:
            return m
    return None


def _fmt(template: str, **values: Any) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


# --------------------------------------------------------------------------------------
# Regex rules
# --------------------------------------------------------------------------------------


def _eval_regex_rule(rule: RegexRule, posting: PostingLike, max_chars: int) -> Hit | None:
    for name in rule.fields:
        text = _field(posting, name)
        if not text:
            continue
        for pattern in rule.patterns:
            for m in pattern.finditer(text):
                if rule.unless:
                    if rule.unless_scope == "field":
                        scope = text
                    else:
                        before, after = _sentence(text, m.start(), m.end(), rule.unless_window)
                        scope = before + m.group(0) + after
                    if _any(rule.unless, scope):
                        continue
                return Hit(
                    rule.id, rule.action, rule.reason, _snippet(text, m.start(), m.end(), max_chars)
                )
    return None


# --------------------------------------------------------------------------------------
# Start date
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Mention:
    months: tuple[YearMonth, ...]
    label: str
    evidence: str


def _infer_year(month: int, ref: date) -> int:
    return ref.year if month >= ref.month else ref.year + 1


def _expand(a: YearMonth, b: YearMonth) -> tuple[YearMonth, ...]:
    if b < a:
        return (a,)
    out: list[YearMonth] = []
    y, m = a
    while (y, m) <= b and len(out) < 24:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return tuple(out)


def _label(months: tuple[YearMonth, ...]) -> str:
    names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    first, last = months[0], months[-1]
    if first == (first[0], 1) and last == (first[0], 12):
        return str(first[0])
    if first == last:
        return f"{names[first[1] - 1]} {first[0]}"
    return f"{names[first[1] - 1]} {first[0]}-{names[last[1] - 1]} {last[0]}"


def _parse_period(
    rule: StartRule, text: str, ref: date, *, require_year: bool, allow_bare_year: bool
) -> tuple[int, tuple[YearMonth, ...]] | None:
    """Earliest date expression in ``text`` -> (position, months)."""
    found: list[tuple[int, tuple[YearMonth, ...]]] = []

    for m in re.finditer(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b", text):
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            found.append((m.start(), ((int(m.group(3)), mo),)))
    for m in re.finditer(r"\b(20\d{2})-(\d{2})(?:-\d{2})?\b", text):
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            found.append((m.start(), ((int(m.group(1)), mo),)))
    for m in re.finditer(r"(?<![\d./-])(\d{1,2})[./](20\d{2})\b", text):
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            found.append((m.start(), ((int(m.group(2)), mo),)))

    for m in rule.month_rx.finditer(text):
        m1 = rule.month_of.get(re.sub(r"\s+", " ", m.group("m1").lower()))
        m2 = (
            rule.month_of.get(re.sub(r"\s+", " ", m.group("m2").lower())) if m.group("m2") else None
        )
        y1 = int(m.group("y1")) if m.group("y1") else None
        y2 = int(m.group("y2")) if m.group("y2") else None
        if m1 is None or (require_year and y1 is None and y2 is None):
            continue
        if m2 is None:
            year = y1 or y2 or _infer_year(m1, ref)
            found.append((m.start(), ((year, m1),)))
            continue
        if y1 is None:
            y1 = (y2 if m1 <= m2 else y2 - 1) if y2 is not None else _infer_year(m1, ref)
        if y2 is None:
            y2 = y1 if m2 >= m1 else y1 + 1
        found.append((m.start(), _expand((y1, m1), (y2, m2))))

    for period in rule.periods:
        for m in period.regex.finditer(text):
            y = int(m.group("y")) if m.group("y") else None
            if y is None and (require_year or period.needs_year):
                continue
            lo, hi = period.months
            year = y if y is not None else _infer_year(lo, ref)
            found.append((m.start(), _expand((year, lo), (year, hi))))

    for m in re.finditer(r"\b([HSQ])([1-4])\s*[/'-]?\s*((?:20)?\d{2})\b", text):
        kind, n = m.group(1).upper(), int(m.group(2))
        year = int(m.group(3))
        year = year + 2000 if year < 100 else year
        if kind in "HS" and n <= 2:
            lo, hi = (1, 6) if n == 1 else (7, 12)
        elif kind == "Q":
            lo, hi = 3 * n - 2, 3 * n
        else:
            continue
        found.append((m.start(), _expand((year, lo), (year, hi))))

    if allow_bare_year:
        for m in re.finditer(r"\b(20\d{2})\b", text):
            year = int(m.group(1))
            found.append((m.start() + 10_000, _expand((year, 1), (year, 12))))  # lowest priority

    if not found:
        return None
    return min(found, key=lambda x: (x[0], -len(x[1])))


def _start_mentions(
    rule: StartRule, posting: PostingLike, ref: date, max_chars: int
) -> list[_Mention]:
    mentions: list[_Mention] = []
    title = _field(posting, "title")

    # Titles: an explicit month/season/half with a year is a start ("(H1 2027)", "Janvier 2027").
    if "title" in rule.fields and title:
        parsed = _parse_period(rule, title, ref, require_year=True, allow_bare_year=False)
        if parsed:
            mentions.append(_Mention(parsed[1], _label(parsed[1]), title[:max_chars]))
        m = re.search(r"\b(20\d{2})\s+(?:start|intake|cohort)\b", title, _FLAGS)
        if m:
            year = int(m.group(1))
            months = _expand((year, 1), (year, 12))
            mentions.append(_Mention(months, str(year), title[:max_chars]))
        for p in rule.summer_title:
            if p.search(title):
                months = _expand(rule.summer_from, rule.summer_to)
                mentions.append(
                    _Mention(months, f"summer {rule.summer_from[0]}", title[:max_chars])
                )
                break

    for name in rule.fields:
        if name == "title":
            continue
        text = _field(posting, name)
        if not text:
            continue
        for p in rule.summer_text:
            for m in p.finditer(text):
                before, after = _sentence(text, m.start(), m.end(), 60)
                if _any(rule.summer_unless, before + m.group(0) + after):
                    continue
                months = _expand(rule.summer_from, rule.summer_to)
                ev = _snippet(text, m.start(), m.end(), max_chars)
                mentions.append(_Mention(months, f"summer {rule.summer_from[0]}", ev))
        for strong, keywords in ((True, rule.keywords), (False, rule.weak_keywords)):
            for kw_rx in keywords:
                for kw in kw_rx.finditer(text):
                    pre = text[max(0, kw.start() - rule.context_skip_chars) : kw.start()]
                    cuts = list(_BOUNDARY.finditer(pre))
                    if cuts:
                        pre = pre[cuts[-1].end() :]
                    if _any(rule.context_skip, pre):
                        continue
                    tail = text[kw.end() : kw.end() + 50]
                    cut = _BOUNDARY.search(tail)
                    if cut:
                        tail = tail[: cut.start()]
                    parsed = _parse_period(
                        rule, tail, ref, require_year=not strong, allow_bare_year=strong
                    )
                    if parsed is None:
                        continue
                    end = kw.end() + len(tail)
                    ev = _snippet(text, kw.start(), end, max_chars)
                    mentions.append(_Mention(parsed[1], _label(parsed[1]), ev))
    return mentions


def _classify(rule: StartRule, months: tuple[YearMonth, ...]) -> str:
    if any(rule.ok_from <= ym <= rule.ok_to for ym in months):
        return "ok"
    if all(ym < rule.ok_from for ym in months):
        return "before"
    if all(ym < rule.ok_from or rule.summer_from <= ym <= rule.summer_to for ym in months):
        return "summer"
    return "late"


def _start_is_flexible(rule: StartRule, posting: PostingLike) -> bool:
    for name in rule.fields:
        text = _field(posting, name)
        if text and _any(rule.flexible, text):
            return True
    return False


def _eval_start(
    rule: StartRule,
    posting: PostingLike,
    ref: date,
    max_chars: int,
    mentions: list[_Mention] | None = None,
) -> Hit | None:
    if _start_is_flexible(rule, posting):
        return None
    if mentions is None:
        mentions = _start_mentions(rule, posting, ref, max_chars)
    if not mentions:
        return Hit(
            rule.ids.get("not_stated", "start_not_stated"),
            rule.actions.get("not_stated", "flag"),
            rule.reasons.get("not_stated", "start not stated"),
            None,
        )
    by_class: dict[str, _Mention] = {}
    for mention in mentions:
        by_class.setdefault(_classify(rule, mention.months), mention)
    if "ok" in by_class:
        return None
    for cls in ("late", "summer", "before"):
        if cls in by_class:
            mention = by_class[cls]
            return Hit(
                rule.ids.get(cls, f"start_{cls}"),
                rule.actions.get(cls, "reject"),
                _fmt(rule.reasons.get(cls, cls), when=mention.label),
                mention.evidence,
            )
    return None


# --------------------------------------------------------------------------------------
# Experience
# --------------------------------------------------------------------------------------


def _eval_experience(rule: ExperienceRule, posting: PostingLike, max_chars: int) -> Hit | None:
    best: tuple[int, int, str, str] | None = None  # (severity, years, action, evidence)
    for name in rule.fields:
        text = _field(posting, name)
        if not text:
            continue
        for pattern in rule.patterns:
            for m in pattern.finditer(text):
                years = int(m.group("min"))
                if years > 20 or years < rule.flag_min:
                    continue
                pre = text[max(0, m.start() - 25) : m.start()]
                if _any(rule.negations, pre):
                    continue
                before, after = _sentence(text, m.start(), m.end(), 80)
                soft = bool(_any(rule.soft, before + m.group(0) + after))
                action = "reject" if years >= rule.reject_min and not soft else "flag"
                severity = 2 if action == "reject" else 1
                cand = (severity, years, action, _snippet(text, m.start(), m.end(), max_chars))
                if best is None or cand[:2] > best[:2]:
                    best = cand
    if best is None:
        return None
    _, years, action, evidence = best
    key = "reject" if action == "reject" else "flag"
    return Hit(
        rule.ids.get(key, f"experience_{key}"),
        action,
        _fmt(rule.reasons.get(key, "{years}+ years of experience"), years=years),
        evidence,
    )


# --------------------------------------------------------------------------------------
# Location
# --------------------------------------------------------------------------------------

_LOC_SPLIT = re.compile(r"[,/;|()\n]|\s+(?:o|or|and|e|et|und|y|-|–)\s+", re.I)


def location_codes(
    location: str | None, remote_patterns: Sequence[re.Pattern[str]] = ()
) -> tuple[set[str], bool]:
    """All country codes mentioned in a location string, and whether it says remote."""
    codes: set[str] = set()
    remote = False
    if not location:
        return codes, remote
    if _any(remote_patterns, location):
        remote = True
    for part in _LOC_SPLIT.split(location):
        part = part.strip()
        if not part:
            continue
        code = country_code(part)
        if code == "REMOTE":
            remote = True
            # "remoto Germania": look for a country after the remote word
            rest = re.sub(r"\b(?:remote|remoto|remota|fully remote)\b", " ", part, flags=re.I)
            code = country_code(rest) if rest.strip() else None
        if code and code != "REMOTE":
            codes.add(code)
    return codes, remote


def _sponsorship(rule: LocationRule, text: str) -> bool:
    for p in rule.sponsorship:
        for m in p.finditer(text):
            before, after = _sentence(text, m.start(), m.end(), 80)
            if not _any(rule.sponsorship_neg, before + m.group(0) + after):
                return True
    return False


def _eval_location(rule: LocationRule, posting: PostingLike, max_chars: int) -> Hit | None:
    location = _field(posting, "location")
    codes, remote = location_codes(location, rule.remote)
    cc = (posting.country_code or "").upper() or None
    if cc == "REMOTE":
        remote = True
    elif cc:
        codes.add(cc)
    evidence = location[:max_chars] if location else (cc or None)
    if codes & rule.allowed:
        return None
    if codes:
        if codes & rule.uk:
            text = "\n".join((_field(posting, "title"), _field(posting, "raw_text")))
            if _sponsorship(rule, text):
                return None
            return Hit(
                rule.ids.get("uk", "location_uk_visa"),
                rule.actions.get("uk", "reject"),
                rule.reasons.get("uk", "UK visa"),
                evidence,
            )
        return Hit(
            rule.ids.get("outside", "location_outside_europe"),
            rule.actions.get("outside", "reject"),
            _fmt(rule.reasons.get("outside", "outside Europe"), country="/".join(sorted(codes))),
            evidence,
        )
    if remote:
        return None
    return Hit(
        rule.ids.get("unknown", "location_unknown"),
        rule.actions.get("unknown", "flag"),
        rule.reasons.get("unknown", "location unknown"),
        evidence,
    )


# --------------------------------------------------------------------------------------
# Languages
# --------------------------------------------------------------------------------------


def _eval_languages(rule: LanguageRule, posting: PostingLike, max_chars: int) -> list[Hit]:
    required: dict[str, str] = {}
    soft: dict[str, str] = {}
    for name in rule.fields:
        text = _field(posting, name)
        if not text:
            continue
        for pattern in rule.patterns:
            for m in pattern.finditer(text):
                matched = m.group(0)
                langs = [lang for lang, rx in rule.names.items() if rx.search(matched)]
                if not langs:
                    continue
                pre = text[max(0, m.start() - 40) : m.start()]
                if _any(rule.alternatives_before, pre):
                    continue
                before, after = _sentence(text, m.start(), m.end(), 80)
                is_soft = bool(_any(rule.soft, before + matched + after))
                ev = _snippet(text, m.start(), m.end(), max_chars)
                for lang in langs:
                    if is_soft:
                        soft.setdefault(lang, ev)
                    else:
                        required.setdefault(lang, ev)
    hits = [
        Hit(
            rule.ids.get("required", "language_required"),
            "reject",
            _fmt(rule.reasons.get("required", "{lang} required"), lang=lang),
            ev,
        )
        for lang, ev in required.items()
    ]
    hits += [
        Hit(
            rule.ids.get("soft", "language_nice_to_have"),
            "flag",
            _fmt(rule.reasons.get("soft", "{lang} is a plus"), lang=lang),
            ev,
        )
        for lang, ev in soft.items()
        if lang not in required
    ]
    return hits


def _eval_posting_language(
    rule: PostingLanguageRule, posting: PostingLike, required: Iterable[Hit]
) -> Hit | None:
    """Flag a description written mostly in a language the user does not speak."""
    det = langdetect.detect(_field(posting, f) for f in rule.fields)
    lang = det.lang
    if lang is None or lang not in rule.flag_languages:
        return None
    if det.words < rule.min_words or det.share < rule.min_share:
        return None
    label = rule.labels.get(lang, lang)
    # "French required" already says it (and is stronger): one finding per language.
    names = {label.lower(), *(n for n, c in _LANG_CODES.items() if c == lang)}
    if any(
        h.rule_id in rule.skip_if_required and any(n in h.reason.lower() for n in names)
        for h in required
    ):
        return None
    shares = ", ".join(
        f"{rule.labels.get(k, k)} {round(v * 100)}%"
        for k, v in sorted(det.shares.items(), key=lambda kv: -kv[1])
    )
    evidence = _fmt(rule.evidence, shares=shares, words=det.words)
    return Hit(rule.id, rule.action, _fmt(rule.reason, lang=label), evidence)


# --------------------------------------------------------------------------------------
# Enrollment agreement (FR/ES internships)
# --------------------------------------------------------------------------------------


def _eval_enrollment(
    rule: EnrollmentRule, posting: PostingLike, codes: set[str], max_chars: int
) -> list[Hit]:
    is_internship = (posting.type or "") in rule.types or bool(
        _any(rule.patterns, _field(posting, "title"))
    )
    if not is_internship:
        return []
    location = _field(posting, "location")
    return [
        Hit(rule.id, rule.action, _fmt(rule.reason, country=country), location[:max_chars] or code)
        for code, country in rule.countries.items()
        if code in codes
    ]


# --------------------------------------------------------------------------------------
# Post-graduation roles (graduate programmes / full-time roles starting after graduation)
# --------------------------------------------------------------------------------------


def _role_kind(rule: PostGraduationRule, posting: PostingLike, max_chars: int) -> tuple[str, str]:
    """("graduate" | "fulltime" | "", evidence). Internships are never post-graduation."""
    title = _field(posting, "title")
    ptype = posting.type or ""
    grad_title = _any(rule.graduate_title_patterns, title)
    if _any(rule.internship_patterns, title):
        return "", ""
    if ptype in rule.internship_types and not grad_title:
        return "", ""
    if grad_title or ptype in rule.graduate_types:
        return "graduate", title[:max_chars]
    for name in rule.text_fields:
        if name == "title":
            continue
        text = _field(posting, name)
        m = _any(rule.graduate_text_patterns, text) if text else None
        if m:
            return "graduate", _snippet(text, m.start(), m.end(), max_chars)
    if ptype in rule.fulltime_types:
        return "fulltime", title[:max_chars]
    for name in rule.text_fields:
        text = _field(posting, name)
        m = _any(rule.fulltime_patterns, text) if text else None
        if m:
            return "fulltime", _snippet(text, m.start(), m.end(), max_chars)
    return "", ""


def _trailing_mentions(
    rule: PostGraduationRule, start: StartRule, posting: PostingLike, ref: date, max_chars: int
) -> list[_Mention]:
    """Dates written before the keyword: "September 2027 intake", "Autumn start"."""
    if rule.trailing_start is None:
        return []
    out: list[_Mention] = []
    for name in rule.text_fields:
        text = _field(posting, name)
        for m in rule.trailing_start.finditer(text) if text else ():
            parsed = _parse_period(
                start, m.group("when"), ref, require_year=False, allow_bare_year=True
            )
            if parsed:
                ev = _snippet(text, m.start(), m.end(), max_chars)
                out.append(_Mention(parsed[1], _label(parsed[1]), ev))
    return out


def _whole_year(months: tuple[YearMonth, ...]) -> bool:
    return months[0][1] == 1 and months[-1] == (months[0][0], 12)


def _eval_post_graduation(
    rule: PostGraduationRule,
    start: StartRule | None,
    posting: PostingLike,
    ref: date,
    max_chars: int,
    mentions: list[_Mention],
) -> Hit | None:
    kind, kind_evidence = _role_kind(rule, posting, max_chars)
    if not kind:
        return None
    label = rule.kinds.get(kind, kind)
    flexible = start is not None and _start_is_flexible(start, posting)
    if start is not None and not flexible:
        mentions = mentions + _trailing_mentions(rule, start, posting, ref, max_chars)
    else:
        mentions = []
    y, m = rule.graduation
    after: YearMonth = (y + 1, 1) if m == 12 else (y, m + 1)

    # (a) start after graduation. For a graduate programme, a bare "2027 start" means the
    # autumn intake, so a whole-year mention from the graduation year on counts too.
    def specific(mention: _Mention) -> bool:
        return not (kind == "graduate" and _whole_year(mention.months))

    # A flexible start ("ASAP", "rolling") could be Feb-Apr 2027: same as an in-window start.
    ok = flexible or (
        start is not None
        and any(specific(x) and _classify(start, x.months) == "ok" for x in mentions)
    )
    dated = [
        x
        for x in mentions
        if x.months[0] >= after
        or (not specific(x) and x.months[0][0] >= y and not any(specific(o) for o in mentions))
    ]
    if dated and not ok:
        return Hit(
            POST_GRADUATION_ID,
            rule.action,
            _fmt(
                rule.reasons.get("dated", "post-graduation {kind} (start {when})"),
                kind=label,
                when=dated[0].label,
            ),
            dated[0].evidence,
        )

    # (b) explicitly for people who already graduated
    for name in rule.text_fields:
        text = _field(posting, name)
        if not text:
            continue
        for pattern in rule.after_graduation:
            for hit in pattern.finditer(text):
                before, after_ctx = _sentence(text, hit.start(), hit.end(), 100)
                if _any(rule.after_graduation_unless, before + hit.group(0) + after_ctx):
                    continue
                # "February or August 2027: August is after graduation" -> the phrase is
                # about the later intake; the in-window one stays open.
                if ok and start is not None:
                    near = before[-40:] + " " + after_ctx[:40]
                    if any(
                        (
                            x := _parse_period(
                                start, mm.group(0), ref, require_year=False, allow_bare_year=False
                            )
                        )
                        and x[1][0] >= after
                        for mm in start.month_rx.finditer(near)
                    ):
                        continue
                return Hit(
                    POST_GRADUATION_ID,
                    rule.action,
                    _fmt(
                        rule.reasons.get("after_graduation", "post-graduation {kind}"), kind=label
                    ),
                    _snippet(text, hit.start(), hit.end(), max_chars),
                )

    # (c) graduate programme with no start date
    open_to_students = any(
        _any(rule.open_to_students, _field(posting, name)) for name in rule.text_fields
    )
    if kind == "graduate" and not mentions and not flexible and not open_to_students:
        return Hit(
            POST_GRADUATION_ID,
            rule.action,
            _fmt(rule.reasons.get("undated", "post-graduation: {kind}"), kind=label),
            kind_evidence or None,
        )
    return None


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def evaluate(
    posting: PostingLike, rules: Rules | Mapping[str, Any], today: date | None = None
) -> list[Hit]:
    """All reject/flag hits for one posting. Pure: no database, no network."""
    compiled = rules if isinstance(rules, Rules) else compile_rules(rules)
    ref = today or utc_today()
    max_chars = compiled.evidence_chars
    hits: list[Hit] = []
    for rule in compiled.regex_rules:
        hit = _eval_regex_rule(rule, posting, max_chars)
        if hit:
            hits.append(hit)
    mentions: list[_Mention] = []
    if compiled.start:
        if not _start_is_flexible(compiled.start, posting):
            mentions = _start_mentions(compiled.start, posting, ref, max_chars)
        if hit := _eval_start(compiled.start, posting, ref, max_chars, mentions):
            hits.append(hit)
    pg = compiled.post_graduation
    if pg and (hit := _eval_post_graduation(pg, compiled.start, posting, ref, max_chars, mentions)):
        # A post-graduation role is one finding: drop the internship-window start hits.
        hits = [h for h in hits if h.rule_id not in pg.supersedes]
        hits.append(hit)
    if compiled.experience and (hit := _eval_experience(compiled.experience, posting, max_chars)):
        hits.append(hit)
    if compiled.location and (hit := _eval_location(compiled.location, posting, max_chars)):
        hits.append(hit)
    if compiled.languages:
        hits.extend(_eval_languages(compiled.languages, posting, max_chars))
    pl = compiled.posting_language
    if pl and (hit := _eval_posting_language(pl, posting, hits)):
        hits.append(hit)
    if compiled.enrollment:
        remote = compiled.location.remote if compiled.location else []
        codes, _ = location_codes(_field(posting, "location"), remote)
        if posting.country_code:
            codes.add(posting.country_code.upper())
        hits.extend(_eval_enrollment(compiled.enrollment, posting, codes, max_chars))
    return hits


def state_for(hits: Iterable[Hit]) -> str:
    actions = {h.action for h in hits}
    if "reject" in actions:
        return "rejected"
    if "flag" in actions:
        return "flagged"
    return "pass"


def apply_filters(
    session: Session, posting: Posting, rules: Rules | None = None, today: date | None = None
) -> str:
    """Re-evaluate one posting: replace its non-overridden FilterResults, set filter_state."""
    rules = rules or load_rules()
    assert posting.id is not None, "posting must be flushed first"
    hits = evaluate(posting, rules, today)
    existing = session.exec(select(FilterResult).where(FilterResult.posting_id == posting.id)).all()
    kept = {(r.rule_id, r.reason) for r in existing if r.overridden}
    for row in existing:
        if not row.overridden:
            session.delete(row)
    overridden = posting.filter_state == "overridden"
    for hit in hits:
        if (hit.rule_id, hit.reason) in kept:
            continue
        session.add(
            FilterResult(
                posting_id=posting.id,
                rule_id=hit.rule_id,
                action=hit.action,
                reason=hit.reason,
                evidence=hit.evidence,
                overridden=overridden
                and (hit.action == "reject" or hit.rule_id == POST_GRADUATION_ID),
            )
        )
    if not overridden:
        posting.filter_state = state_for(hits)
        session.add(posting)
    return posting.filter_state


def apply_filters_all(
    session: Session, rules: Rules | None = None, today: date | None = None
) -> dict[str, int]:
    """Re-filter every posting (keeps manual overrides). Returns counts per filter_state."""
    rules = rules or load_rules()
    counts = {"pass": 0, "flagged": 0, "rejected": 0, "overridden": 0}
    for posting in session.exec(select(Posting).order_by(col(Posting.id))).all():
        state = apply_filters(session, posting, rules, today)
        counts[state] = counts.get(state, 0) + 1
    session.flush()
    counts["total"] = sum(counts.values())
    return counts
