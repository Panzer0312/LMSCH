"""Display profile resolution: which geometry applies to what mpv plays."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import ValidationError
from sqlmodel import col, select

from spookhub import db
from spookhub.models import DisplayGeometry, DisplayProfile, Media
from spookhub.player.geometry import SourceFrame

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaInfo:
    id: int
    display_profile_id: int | None
    size: SourceFrame | None


def resolve(
    profile_dp: int | None, media_dp: int | None, global_dp: int | None
) -> tuple[int | None, DisplayGeometry]:
    """Precedence profile > media > global > identity. A dangling id falls through."""
    ids = [i for i in (profile_dp, media_dp, global_dp) if i is not None]
    if not ids:
        return None, DisplayGeometry()
    with db.session() as s:
        for i in ids:
            row = s.get(DisplayProfile, i)
            if row is None:
                continue
            try:
                return i, DisplayGeometry.model_validate(row.geometry or {})
            except ValidationError:
                log.warning("display profile %s has invalid geometry, skipped", i)
    return None, DisplayGeometry()


def _info(m: Media) -> MediaInfo:
    size = SourceFrame(m.width, m.height, m.rotation or 0) if m.width > 0 and m.height > 0 else None
    return MediaInfo(id=m.id or 0, display_profile_id=m.display_profile_id, size=size)


def media_by_path(path: str | None) -> MediaInfo | None:
    if not path:
        return None
    with db.session() as s:
        m = s.exec(select(Media).where(Media.path == path).order_by(col(Media.id).desc())).first()
        return _info(m) if m else None


def media_by_id(mid: int | None) -> tuple[str, MediaInfo] | None:
    if mid is None:
        return None
    with db.session() as s:
        m = s.get(Media, mid)
        return (m.path, _info(m)) if m else None
