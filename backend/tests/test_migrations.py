"""Migration c3d9a41f7e58: existing rows get empty markers; downgrade works."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

PREV, HEAD = "b7c1e5a90d42", "c3d9a41f7e58"


def _cfg(db: Path) -> Config:
    c = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    c.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return c


def test_markers_migration_up_and_down(tmp_path):
    db = tmp_path / "m.db"
    cfg = _cfg(db)
    command.upgrade(cfg, PREV)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO profile (name, type, sort, selection, base_state, overrides, "
        "playback_mode, cooldown_s) VALUES ('old','light',0,'{}','{}','{}','once',0)"
    )
    con.commit()
    con.close()
    command.upgrade(cfg, HEAD)
    con = sqlite3.connect(db)
    assert con.execute("SELECT markers FROM profile").fetchone()[0] == "[]"
    con.close()
    command.downgrade(cfg, PREV)
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(profile)")}
    assert "markers" not in cols
    assert con.execute("SELECT count(*) FROM profile").fetchone()[0] == 1
    con.close()
