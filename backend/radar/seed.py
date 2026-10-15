"""Import radar_seed.json (the manual tracker export) into the database. Idempotent."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from radar.models import Application, Company, Posting, Score
from radar.pipeline.normalize import (
    canonical_url,
    content_hash,
    country_code,
    dedupe_key,
    zone_for,
)
from radar.timeutil import utcnow

_STATUS_MAP = {"todo": ("open", "todo"), "sent": ("open", "sent"), "closed": ("closed", "todo")}
_TYPE_MAP = {"Stage": "Stage", "Graduate": "Graduate", "Lavoro": "Lavoro"}


def _parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _join(*parts: str | None) -> str | None:
    return "\n".join(x for x in parts if x) or None


def import_seed(session: Session, path: Path) -> dict[str, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    counts = {"companies": 0, "postings": 0, "applications": 0, "scores": 0}

    for c in data.get("companies", []):
        if session.exec(select(Company).where(Company.name == c["co"])).first():
            continue
        page = c.get("page") or ""
        is_url = page.startswith("http")
        session.add(
            Company(
                name=c["co"],
                sector=c.get("sector"),
                city=c.get("city"),
                careers_url=page if page.startswith("http") else None,
                program=c.get("program"),
                why=c.get("why"),
                notes=_join(c.get("welfare"), c.get("note"), None if is_url else page),
                priority=c.get("p"),
            )
        )
        counts["companies"] += 1
    session.flush()

    companies = {c.name: c for c in session.exec(select(Company)).all()}
    now = utcnow()
    for p in data.get("postings", []):
        uid = f"seed:{p['id']}"
        if session.exec(select(Posting).where(Posting.uid == uid)).first():
            continue
        code = country_code(p.get("country"), p.get("city"))
        status, user_status = _STATUS_MAP.get(p.get("status", "todo"), ("open", "todo"))
        warn = p.get("warn") or ""
        raw = "\n".join(x for x in (p.get("why"), warn, p.get("date")) if x)
        posting = Posting(
            uid=uid,
            source="seed",
            external_id=p["id"],
            url=p["url"],
            canonical_url=canonical_url(p["url"]),
            title=p["role"],
            company_name=p["co"],
            company_id=companies[p["co"]].id if p["co"] in companies else None,
            location=p.get("city"),
            country=p.get("country"),
            country_code=code,
            zone=p.get("zone") or zone_for(code),
            type=_TYPE_MAP.get(p.get("kind", ""), p.get("kind")),
            area=p.get("area"),
            start_info=p.get("date") or None,
            deadline=_parse_date(p.get("deadline")),
            via=p.get("via"),
            raw_text=raw,
            content_hash=content_hash(p["role"], p["co"], p.get("city"), raw),
            dedupe_key=dedupe_key(p["co"], p["role"], code),
            first_seen=now,
            last_seen=now,
            status=status,
            closed_at=now if status == "closed" else None,
            user_status=user_status,
        )
        session.add(posting)
        session.flush()
        assert posting.id is not None
        session.add(
            Score(
                posting_id=posting.id,
                content_hash=posting.content_hash,
                priority=p.get("p", "Media"),
                why_fits=p.get("why") or "",
                what_doesnt_fit=warn,
                warnings=[warn] if warn else [],
                provider="manual",
                model="manual",
            )
        )
        counts["postings"] += 1
        counts["scores"] += 1
    session.flush()

    for a in data.get("applications", []):
        exists = session.exec(
            select(Application).where(
                Application.company == a["company"], Application.role == a["role"]
            )
        ).first()
        if exists:
            continue
        linked = session.exec(
            select(Posting).where(Posting.title == a["role"], Posting.source == "seed")
        ).first()
        session.add(
            Application(
                posting_id=linked.id if linked else None,
                company=a["company"],
                role=a["role"],
                city=a.get("city"),
                date_sent=_parse_date(a.get("date")) or utcnow().date(),
                channel=a.get("channel") or "Sito aziendale",
                cv_version=a.get("cv") or "CV v1",
                stage=a.get("stage") or "Inviata",
                notes=a.get("note") or None,
            )
        )
        if linked is not None and linked.user_status != "sent":
            linked.user_status = "sent"
            session.add(linked)
        counts["applications"] += 1
    return counts
