from radar.pipeline.normalize import (
    canonical_url,
    country_code,
    dedupe_key,
    normalize_company,
    zone_for,
)


def test_country_code_italian_names_and_cities() -> None:
    assert country_code("Paesi Bassi") == "NL"
    assert country_code(None, "Monaco o Berlino") == "DE"
    assert country_code("Spagna / Portogallo / Polonia") == "ES"
    assert country_code("Milan, Lombardy, Italy") == "IT"
    assert country_code("London, England, United Kingdom") == "GB"
    assert country_code("Nowhere") is None


def test_zone() -> None:
    assert zone_for("IT") == "Italia"
    assert zone_for("CH") == "Europa"
    assert zone_for("US") == "Altro"


def test_canonical_url_strips_tracking_and_linkedin_subdomains() -> None:
    assert canonical_url("https://Jobs.Example.com/a/?utm_source=x&id=3#top") == (
        "https://jobs.example.com/a?id=3"
    )
    assert canonical_url("https://it.linkedin.com/jobs/view/ai-intern-at-x-4012345678?trk=a") == (
        "https://www.linkedin.com/jobs/view/4012345678"
    )


def test_dedupe_key_ignores_suffixes_and_gender_tags() -> None:
    assert normalize_company("Nebbia Labs S.p.A.") == "nebbia labs"
    assert dedupe_key("Nebbia Labs GmbH", "AI Intern (m/f/d)", "DE") == dedupe_key(
        "Nebbia Labs", "AI Intern", "DE"
    )


def test_country_code_non_european_cities() -> None:
    assert country_code("Hong Kong, Hong Kong") == "HK"
    assert country_code("Seoul") == "KR"
    assert country_code("Mountain View, California") == "US"
    assert country_code("Doha, Qatar ") == "QA"


def test_country_code_multi_location_prefers_europe() -> None:
    assert country_code("London, New York, Singapore, Boston, Paris") == "FR"
    assert country_code("New York, London, or Paris") == "FR"
    assert country_code("London, New York") == "GB"
    assert country_code("Montreal, Bangalore") == "CA"
    assert country_code("Nantes") == "FR"
