"""Shared helpers for connector tests (fixtures are recorded/trimmed; no live network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
UA = "speculor-radar-test/0.1 (+https://example.com)"


def load_json(rel: str) -> Any:
    return json.loads((FIXTURES / rel).read_text(encoding="utf-8"))


def load_text(rel: str) -> str:
    return (FIXTURES / rel).read_text(encoding="utf-8")


class SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
