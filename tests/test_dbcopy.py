from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlmodel import Session

from radar.dbcopy import copy_database
from radar.models import Posting, Score
from radar.seed import import_seed

SEED = Path(__file__).parent / "fixtures" / "seed" / "mini_seed.json"


def test_copy_keeps_ids_and_refuses_non_empty_target(tmp_path: Path) -> None:
    src = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    dst = create_engine(f"sqlite:///{tmp_path / 'b.db'}")
    from radar.db import init_db

    init_db(src)
    with Session(src) as s:
        import_seed(s, SEED)
        s.commit()
    counts = copy_database(src, dst)
    assert counts["postings"] == 2 and counts["scores"] == 2
    with Session(dst) as s:
        ids = sorted(p.id for p in s.exec(select(Posting)).scalars())  # type: ignore[call-overload]
        assert ids == [1, 2]
        assert s.execute(select(func.count()).select_from(Score)).scalar_one() == 2
    with pytest.raises(RuntimeError):
        copy_database(src, dst)
    assert copy_database(src, dst, replace=True)["postings"] == 2
