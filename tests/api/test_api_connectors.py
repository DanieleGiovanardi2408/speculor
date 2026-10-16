from sqlmodel import Session

from radar.connectors.status import pause


def test_connectors_api_status_and_resume(client, api_engine):
    with Session(api_engine) as s:
        pause(s, "jobteaser", "session expired", "Esegui make jobteaser-login.")
        s.commit()
    assert client.get("/api/connectors/status").json()["jobteaser"]["paused"] is True
    assert client.post("/api/connectors/jobteaser/resume").json() == {}
    assert client.post("/api/connectors/jobteaser/resume").status_code == 404
