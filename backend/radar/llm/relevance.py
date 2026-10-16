"""Deterministic role relevance from the title, used to spend the free LLM budget well.

Titles that are clearly outside the target roles (tax, payroll, sales, legal, ...) get a
"Bassa" score without an LLM call; relevant titles are scored first. Keywords can be
overridden in profile.yaml under ``scoring: {target_keywords: [...], offtarget_keywords: [...]}``.

``cap(title)`` is the highest priority a title can reach after the LLM: adjacent roles (generic
software engineering, QA, fullstack, sales, BI/reporting) are "Media" at most unless the title
also names a top area (AI/ML, data science/analytics, AI/data consulting, VC/investment,
strategy/business analysis, forward-deployed engineering). Override with regexes under
``scoring: {top_patterns: [...], media_cap_patterns: [...]}``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

TARGET_KEYWORDS: tuple[str, ...] = (
    "ai",
    "a.i.",
    "artificial intelligence",
    "machine learning",
    "ml",
    "llm",
    "genai",
    "generative",
    "deep learning",
    "nlp",
    "computer vision",
    "data scien",
    "data scientist",
    "applied scien",
    "research engineer",
    "research scientist",
    "data engineer",
    "data analyst",
    "analytics",
    "data",
    "forward deployed",
    "solutions engineer",
    "quant",
    "quantitative",
    "venture",
    "vc",
    "investment",
    "strategy",
    "strategic",
    "product",
    "consult",
    "innovation",
    "digital transformation",
    "business intelligence",
    "automation",
    "software engineer",
    "software engineering",
    "developer",
    "engineer",
    "statistic",
    "actuar",
    "decision science",
)
OFFTARGET_KEYWORDS: tuple[str, ...] = (
    "tax",
    "payroll",
    "accounting",
    "accountant",
    "comptable",
    "bookkeep",
    "legal",
    "counsel",
    "lawyer",
    "paralegal",
    "marketing",
    "brand",
    "social media",
    "content",
    "copywrit",
    "communications",
    "public relations",
    "sales",
    "account executive",
    "business development representative",
    "sdr",
    "bdr",
    "customer care",
    "customer support",
    "customer service",
    "customer success",
    "support specialist",
    "hr",
    "human resources",
    "talent acquisition",
    "recruit",
    "people partner",
    "people operations",
    "facility",
    "warehouse",
    "driver",
    "courier",
    "rider",
    "office manager",
    "receptionist",
    "administrative",
    "assistant",
    "executive assistant",
    "fp&a",
    "controller",
    "audit",
    "treasury",
    "procurement",
    "purchasing",
    "kyc",
    "aml",
    "fraud",
    "compliance",
    "nurse",
    "medical",
    "pharmac",
    "chef",
    "cook",
    "graphic design",
    "visual design",
    "illustrat",
    "event",
    "hospitality",
    "store",
    "retail associate",
    "cashier",
)


# Top areas: a title with one of these is never capped (regexes, case-insensitive).
TOP_PATTERNS: tuple[str, ...] = (
    r"\b(?:ai|a\.i\.|ml|llms?|genai|nlp|mlops)\b",
    r"\bartificial\s+intelligence\b|\bintelligenza\s+artificiale\b",
    r"\bmachine[- ]learning\b|\bdeep[- ]learning\b|\bcomputer\s+vision\b",
    r"\bgenerative\b|\bagent(?:s|ic)\b",
    r"\bdata\s*scien\w*|\bapplied\s+scien\w*|\bresearch\s+(?:engineer|scientist)\b",
    r"\bdata\s+(?:analy\w+|engineer\w*)\b|\banalytics?\b|\bdecision\s+scien\w*",
    r"\bforward[- ]deployed\b|\bfde\b",
    r"\bventure\b|\bvc\b|\binvestment\w*|\bprivate\s+equity\b",
    r"\bstrateg\w*|\bbusiness\s+analy\w+",
    r"\b(?:ai|data)\s+(?:&\s+\w+\s+)?consult\w*",
    r"\bquant\w*|\bstatistic\w*",
)
# Adjacent roles: "Media" at most when no top pattern matches.
MEDIA_CAP_PATTERNS: tuple[str, ...] = (
    r"\bsoftware\s+(?:engineer\w*|develop\w*)",
    r"\b(?:full|back|front)[- ]?(?:stack|end)\b",
    r"\b(?:web|mobile|ios|android|java|\.net|php)\s+develop\w*|\bdeveloper\b|\bsviluppatore\b",
    r"\bqa\b|\bquality\s+assurance\b|\btest(?:ing|er)?\s+(?:engineer|automation|analyst)\b",
    r"\btester\b|\bsdet\b",
    r"\bdevops\b|\bsite\s+reliability\b|\bsre\b",
    r"\bsales\b|\baccount\s+(?:executive|manager)\b|\bpre[- ]?sales\b",
    r"\bbusiness\s+intelligence\b|\bbi\b|\breporting\b|\bpower\s*bi\b|\btableau\b",
)


def _compile_rx(patterns: Iterable[str]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns if p.strip()), re.I)


def _compile(words: Iterable[str]) -> re.Pattern[str]:
    alts = "|".join(re.escape(w.strip().lower()) for w in words if w.strip())
    return re.compile(rf"(?<![a-z0-9])(?:{alts})", re.I)


class Relevance:
    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        cfg = (profile or {}).get("scoring") or {}
        self._target = _compile(cfg.get("target_keywords") or TARGET_KEYWORDS)
        self._off = _compile(cfg.get("offtarget_keywords") or OFFTARGET_KEYWORDS)
        self._top = _compile_rx(cfg.get("top_patterns") or TOP_PATTERNS)
        self._media_cap = _compile_rx(cfg.get("media_cap_patterns") or MEDIA_CAP_PATTERNS)

    def rank(self, title: str) -> int:
        """2 = target role, 1 = neutral, 0 = clearly off-target."""
        t = f" {title.lower()} "
        target = self._target.search(t)
        off = self._off.search(t)
        if target and not off:
            return 2
        if off and not target:
            return 0
        return 1

    def offtarget_keyword(self, title: str) -> str | None:
        m = self._off.search(f" {title.lower()} ")
        return m.group(0).strip() if m else None

    def cap(self, title: str) -> str | None:
        """Highest priority the title allows: "Media" for adjacent roles, None if uncapped."""
        if self._media_cap.search(title) and not self._top.search(title):
            return "Media"
        return None

    def cap_keyword(self, title: str) -> str | None:
        m = self._media_cap.search(title)
        return m.group(0).strip().lower() if m else None
