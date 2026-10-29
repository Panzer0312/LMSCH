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


def test_segments_migration_keeps_cues_whole_board(tmp_path):
    """e8a4c2f61b37: boards get empty segments; old cue targets load as whole-board."""
    from spookhub.models import CueTarget

    db = tmp_path / "s.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "d5e2b83c1a96")
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO board (mac, ip, name, adopted, firmware, led_count, created_at, offset_ms) "
        "VALUES ('aa','1.2.3.4','b',1,'',60,'2026-01-01 00:00:00',0)"
    )
    con.execute(
        "INSERT INTO profile (name, type, sort, selection, base_state, overrides, "
        "playback_mode, cooldown_s) VALUES ('old','video',0,'{}','{}','{}','once',0)"
    )
    con.execute(
        "INSERT INTO cue (profile_id, start_s, target, state) "
        """VALUES (1, 0, '{"all": false, "macs": ["aa"]}', '{}')"""
    )
    con.commit()
    con.close()
    command.upgrade(cfg, "e8a4c2f61b37")
    con = sqlite3.connect(db)
    assert con.execute("SELECT segments FROM board").fetchone()[0] == "[]"
    target = con.execute("SELECT target FROM cue").fetchone()[0]
    assert CueTarget.model_validate_json(target).segs is None
    con.close()
    command.downgrade(cfg, "d5e2b83c1a96")
    con = sqlite3.connect(db)
    assert "segments" not in {r[1] for r in con.execute("PRAGMA table_info(board)")}
    assert con.execute("SELECT count(*) FROM board").fetchone()[0] == 1
    con.close()
