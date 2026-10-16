from radar.llm.relevance import Relevance


def test_rank_target_neutral_offtarget() -> None:
    r = Relevance()
    assert r.rank("AI Engineer Intern") == 2
    assert r.rank("Product Manager Intern") == 2
    assert r.rank("Investment Analyst Intern") == 2
    assert r.rank("Graduate Trader") == 1
    assert r.rank("Tax Analyst") == 0
    assert r.rank("Payroll Associate (apprentice)") == 0
    assert r.rank("Intern Talent Acquisition Coordinator") == 0
    assert r.offtarget_keyword("Legal Counsel Intern") == "legal"


def test_profile_overrides_keywords() -> None:
    r = Relevance({"scoring": {"target_keywords": ["tax"], "offtarget_keywords": ["ai"]}})
    assert r.rank("Tax Analyst") == 2
    assert r.rank("AI Engineer") == 0
