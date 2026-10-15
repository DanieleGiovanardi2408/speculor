from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from radar.pipeline.filters import Rules, compile_rules

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def rules() -> Rules:
    cfg = yaml.safe_load((REPO / "config" / "rules.example.yaml").read_text("utf-8"))
    return compile_rules(cfg)
