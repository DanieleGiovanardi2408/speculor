"""Test helpers for tests/llm (fictional data only)."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from radar.llm.provider import LLMResult
from radar.models import Posting, Score
from radar.pipeline.normalize import content_hash

FICTIONAL_PROFILE: dict[str, Any] = {
    "name": "Mara Fittizia",
    "headline": "Data scientist, MSc Data Science, Example University",
    "citizenship": "EU citizen",
    "based_in": "Bologna (IT)",
    "availability": "Full-time from February-April 2027",
    "graduation": "MSc Data Science, Example University, expected July 2027",
    "languages": [
        {"language": "Italian", "level": "native"},
        {"language": "English", "level": "C1"},
    ],
    "target_roles": ["Data scientist", "AI/LLM engineer"],
    "education": ["Example University. MSc Data Science, 2025 to 2027."],
    "experience": [
        {
            "org": "Example Analytics",
            "role": "Data Intern",
            "period": "Jun 2026 to Aug 2026",
            "facts": ["Built a weekly demand forecasting pipeline in Python."],
        }
    ],
    "projects": [{"name": "tinybench", "what": "Benchmark of LLM agents on spreadsheet tasks"}],
    "skills": {"programming": "Python, SQL", "ml": "Forecasting, evaluation design"},
    "cv_versions": [{"name": "CV v2", "description": "1 page", "default": True}],
}


def make_posting(
    session: Session,
    *,
    title: str = "Data Science Intern",
    company: str = "Acme Analytics",
    raw_text: str | None = None,
    priority: str | None = None,
    **kw: Any,
) -> Posting:
    text = (
        raw_text
        if raw_text is not None
        else (
            "Join our forecasting team in Milan. You will build demand models in Python and SQL "
            "and present results to the planning team. We work with the planning and supply chain "
            "teams on weekly forecasts for our stores. Requirements: enrolled in or recently "
            "graduated from a master's degree, fluent English, Italian is a plus. Start in "
            "February 2027 for six months."
        )
    )
    n = len(session.exec(select(Posting)).all())
    p = Posting(
        uid=f"test:{n}",
        source="greenhouse",
        external_id=str(n),
        url=f"https://example.com/jobs/{n}",
        canonical_url=f"https://example.com/jobs/{n}",
        title=title,
        company_name=company,
        location="Milan",
        country_code="IT",
        raw_text=text,
        content_hash=content_hash(title, company, "Milan", text + str(n)),
        **kw,
    )
    session.add(p)
    session.flush()
    if priority is not None:
        assert p.id is not None
        session.add(
            Score(
                posting_id=p.id,
                content_hash=p.content_hash,
                priority=priority,
                why_fits="x",
                what_doesnt_fit="",
                provider="manual",
                model="manual",
            )
        )
    session.commit()
    return p


class ScriptedProvider:
    name = "scripted"
    model = "scripted-model"

    def __init__(self, outputs: list[str | Exception]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[tuple[str, str]] = []

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LLMResult:
        self.prompts.append((system, user))
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return LLMResult(out, 100, 50, model, self.name)
