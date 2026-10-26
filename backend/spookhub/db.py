"""SQLite engine + session helpers. Schema managed by Alembic; create_all as dev fallback."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from spookhub import models  # noqa: F401  (register tables)

_engine: Engine | None = None


def init_engine(db_path: Path) -> Engine:
    global _engine
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(_engine, "connect")
    def _pragmas(conn, _rec):  # type: ignore[no-untyped-def]
        cur = conn.cursor()
        # WAL survives power cuts on the mini PC far better than rollback journal.
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    _run_migrations(db_path)
    return _engine


def _run_migrations(db_path: Path) -> None:
    from alembic.config import Config as AlembicConfig

    from alembic import command

    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = AlembicConfig(str(ini))
    cfg.attributes["skip_logging"] = True
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


def engine() -> Engine:
    assert _engine is not None, "init_engine() not called"
    return _engine


@contextmanager
def session() -> Iterator[Session]:
    with Session(engine(), expire_on_commit=False) as s:
        yield s


def create_all_for_tests(eng: Engine) -> None:
    SQLModel.metadata.create_all(eng)
