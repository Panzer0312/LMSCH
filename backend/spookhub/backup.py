"""Backup/restore of the SQLite DB plus config (media files are excluded: too big).

Restore stages the uploaded DB next to the live one and restarts the process (systemd brings it
back); the swap happens at startup before the DB is opened, so nothing reads a half-written file.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import time
import zipfile
from pathlib import Path

from spookhub.config import DEFAULT_CONFIG_PATH

MANIFEST = "spookhub-backup.json"
DB_NAME = "spookhub.db"
CFG_NAME = "config.toml"


class BackupError(Exception):
    pass


def config_path() -> Path:
    return Path(os.environ.get("SPOOKHUB_CONFIG", DEFAULT_CONFIG_PATH))


def make_backup(db_path: Path) -> bytes:
    buf = io.BytesIO()
    snap = db_path.with_suffix(".snapshot")
    # sqlite's online backup API gives a consistent copy even while the app writes (WAL).
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(snap)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, DB_NAME)
            cfg = config_path()
            if cfg.is_file():
                z.write(cfg, CFG_NAME)
            z.writestr(MANIFEST, json.dumps({"version": 1, "created": time.time()}))
    finally:
        snap.unlink(missing_ok=True)
    return buf.getvalue()


def stage_restore(data: bytes, db_path: Path) -> list[str]:
    """Validate a backup zip and stage it. Returns human-readable notes."""
    notes: list[str] = []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise BackupError("not a zip file") from e
    names = set(z.namelist())
    if MANIFEST not in names or DB_NAME not in names:
        raise BackupError("not a Spookhub backup")
    staged = db_path.with_suffix(".restore")
    staged.write_bytes(z.read(DB_NAME))
    try:
        con = sqlite3.connect(staged)
        ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
    except sqlite3.DatabaseError as e:
        staged.unlink(missing_ok=True)
        raise BackupError(f"database in backup is corrupt: {e}") from e
    if not ok or "profile" not in tables:
        staged.unlink(missing_ok=True)
        raise BackupError("database in backup failed the integrity check")
    notes.append("Database restored; Spookhub restarts now.")
    if CFG_NAME in names:
        cfg = config_path()
        try:
            cfg.write_bytes(z.read(CFG_NAME))
            notes.append(f"Config written to {cfg}.")
        except OSError:
            # The service sees /etc read-only (ProtectSystem=full); leave a copy to apply by hand.
            alt = db_path.parent / "config.restored.toml"
            alt.write_bytes(z.read(CFG_NAME))
            notes.append(f"Config could not be written; copy {alt} to {cfg} manually.")
    return notes


def apply_staged_restore(db_path: Path) -> bool:
    staged = db_path.with_suffix(".restore")
    if not staged.exists():
        return False
    for ext in ("-wal", "-shm"):
        Path(str(db_path) + ext).unlink(missing_ok=True)
    if db_path.exists():
        db_path.replace(db_path.with_suffix(".before-restore"))
    staged.replace(db_path)
    return True
