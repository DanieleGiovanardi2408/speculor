from sqlmodel import Session

from radar.connectors.status import SourcePaused, all_status, paused_sources, resume
from radar.db import get_engine, init_db
from radar.pipeline.run import PipelineOptions, run_pipeline


class _Paused:
    source = "linkedin_jobs"
    label = "linkedin jobs"
    calls = 0

    def fetch(self):
        type(self).calls += 1
        raise SourcePaused("linkedin_jobs", "HTTP 999", "Apri LinkedIn nel browser.")


class _Ok:
    source = "greenhouse"
    label = "greenhouse board #1"

    def fetch(self):
        return []


def test_paused_source_is_recorded_skipped_and_does_not_stop_others(tmp_path):
    engine = init_db(get_engine(f"sqlite:///{tmp_path / 'r.db'}"))
    opts = PipelineOptions(llm=False, check_closed=False)
    run_pipeline(opts, connectors=[_Paused(), _Ok()], engine=engine, reports_dir=tmp_path)
    with Session(engine) as s:
        assert paused_sources(s) == {"linkedin_jobs"}
        assert all_status(s)["linkedin_jobs"]["action"].startswith("Apri")
    run_pipeline(opts, connectors=[_Paused(), _Ok()], engine=engine, reports_dir=tmp_path)
    assert _Paused.calls == 1  # never retried while paused
    with Session(engine) as s:
        assert resume(s, "linkedin_jobs")
        s.commit()
        assert paused_sources(s) == set()
