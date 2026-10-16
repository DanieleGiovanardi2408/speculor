from sqlalchemy import inspect, text
from sqlmodel import create_engine

from radar.db import add_missing_columns, init_db


def test_add_missing_columns_adds_new_model_fields(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE settings (key VARCHAR PRIMARY KEY)"))
    init_db(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("settings")}
    assert "value" in cols and "updated_at" in cols
    assert add_missing_columns(engine) == []
