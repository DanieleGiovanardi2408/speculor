"""JobTeaser links saved from a page in another interface language (e.g. Catalan, /ca/)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pipeline_fakes import raw
from sqlmodel import Session, select

from radar.connectors.email_parsers.jobteaser import normalize_location, offer_key
from radar.models import FilterResult, Posting
from radar.pipeline import langdetect
from radar.pipeline.filters import Rules
from radar.pipeline.jobteaser_ingest import IncomingOffer, ingest_offers, normalize_stored
from radar.pipeline.store import upsert_raw

UUID = "0a1b2c3d-1111-4a2a-9f1d-000000000001"
T0 = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize("locale", ["ca", "fr", "en", "es", "it", "en-gb"])
def test_offer_links_always_use_the_italian_interface(locale: str) -> None:
    url = f"https://luiss.jobteaser.com/{locale}/job-offers/{UUID}-nebula-data-intern?from=x"
    assert offer_key(url) == (
        UUID,
        f"https://luiss.jobteaser.com/it/job-offers/{UUID}-nebula-data-intern",
    )
    assert offer_key(f"https://www.jobteaser.com/job-offers/{UUID}") == (
        UUID,
        f"https://www.jobteaser.com/it/job-offers/{UUID}",
    )


@pytest.mark.parametrize(
    "shown",
    [
        "Diverses localitzacions proposades",
        "Varias ubicaciones propuestas",
        "Plusieurs lieux proposés",
        "Several locations proposed",
        "Diverse sedi proposte",
    ],
)
def test_several_locations_placeholder(shown: str) -> None:
    assert normalize_location(shown) == "Più sedi"
    assert normalize_location("Roma, Italia") == "Roma, Italia"


def test_catalan_interface_text_is_not_a_language() -> None:
    det = langdetect.detect(["Diverses localitzacions proposades", "Data Scientist Intern"])
    assert det.lang in (None, "en")


def test_ingest_saves_italian_links_and_no_placeholder_location(session: Session) -> None:
    offers = [
        IncomingOffer(
            url=f"https://luiss.jobteaser.com/ca/job-offers/{UUID}-nebula",
            title="Data Scientist Intern",
            company="Nebula Analytics",
            location="Diverses localitzacions proposades",
            date=None,
        )
    ]
    ingest_offers(session, offers, now=T0)
    p = session.exec(select(Posting)).one()
    assert p.url == f"https://luiss.jobteaser.com/it/job-offers/{UUID}-nebula"
    assert p.location == "Più sedi"


def test_normalize_stored_rows(session: Session, rules: Rules) -> None:
    p = upsert_raw(
        session,
        raw(
            UUID,
            source="jobteaser",
            url=f"https://luiss.jobteaser.com/ca/job-offers/{UUID}-nebula",
            location="Diverses localitzacions proposades",
            raw_text="",
        ),
        now=T0,
    ).posting
    session.commit()
    counts = normalize_stored(session, rules)
    assert counts == {"urls": 1, "locations": 1}
    session.refresh(p)
    assert p.url.startswith("https://luiss.jobteaser.com/it/") and p.location == "Più sedi"
    lang_flags = [
        r
        for r in session.exec(select(FilterResult).where(FilterResult.posting_id == p.id))
        if r.rule_id.startswith("lang")
    ]
    assert lang_flags == []
    assert normalize_stored(session) == {"urls": 0, "locations": 0}
