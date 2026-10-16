"""Deterministic role relevance from the title, used to spend the free LLM budget well.

Titles that are clearly outside the target roles (tax, payroll, sales, legal, ...) get a
"Bassa" score without an LLM call; relevant titles are scored first. Keywords can be
overridden in profile.yaml under ``scoring: {target_keywords: [...], offtarget_keywords: [...]}``.
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


def _compile(words: Iterable[str]) -> re.Pattern[str]:
    alts = "|".join(re.escape(w.strip().lower()) for w in words if w.strip())
    return re.compile(rf"(?<![a-z0-9])(?:{alts})", re.I)


class Relevance:
    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        cfg = (profile or {}).get("scoring") or {}
        self._target = _compile(cfg.get("target_keywords") or TARGET_KEYWORDS)
        self._off = _compile(cfg.get("offtarget_keywords") or OFFTARGET_KEYWORDS)

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
