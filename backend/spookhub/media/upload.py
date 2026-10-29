"""Video upload: stream to disk (never buffer GBs in RAM), validate, probe, register."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import UploadFile

from spookhub import db
from spookhub.media.probe import ProbeError, probe
from spookhub.models import Media

ALLOWED_EXT = {".mp4", ".m4v", ".mkv", ".mov", ".webm", ".avi"}
MAX_BYTES = 8 * 1024**3
CHUNK = 1024 * 1024


class UploadError(Exception):
    pass


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem)[:60] or "video"
    return stem


async def save_upload(file: UploadFile, media_dir: Path) -> Media:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise UploadError(f"unsupported file type '{ext}'")
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / f"{_safe_name(file.filename or '')}-{uuid.uuid4().hex[:8]}{ext}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    size = 0
    try:
        with tmp.open("wb") as f:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise UploadError("file too large")
                f.write(chunk)
        try:
            info = await probe(tmp)
        except ProbeError as e:
            raise UploadError(f"not a playable video: {e}") from e
        tmp.rename(dest)
    finally:
        tmp.unlink(missing_ok=True)
    m = Media(
        filename=file.filename or dest.name,
        path=str(dest),
        size=size,
        duration_s=info.duration_s,
        width=info.width,
        height=info.height,
        codec=info.codec,
        rotation=info.rotation,
    )
    with db.session() as s:
        s.add(m)
        s.commit()
        s.refresh(m)
    return m
