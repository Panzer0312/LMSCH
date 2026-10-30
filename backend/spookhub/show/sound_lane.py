"""Sound cues: which sound should the fx player play at main time t?

Rules:
- One sound at a time. The latest-started sound cue wins while its window covers t.
- Window: [start, start+duration) with a duration; without one the sound plays to its own end
  (media length), or forever when it loops.
- Seek / loop wrap: a cue whose window covers t restarts at offset t - start.
- A cue with a duration fades out (fade_out_ms) when its window ends.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session

from spookhub.models import Cue, Media, SoundSpec
from spookhub.tasks import spawn

log = logging.getLogger(__name__)

OWNER = "cue"


class FxPlayer(Protocol):
    async def play(
        self,
        path: str,
        start: float = 0.0,
        volume: float = 1.0,
        fade_in_ms: int = 0,
        loop: bool = False,
        owner: str | None = None,
    ) -> None: ...

    async def stop(self, fade_out_ms: int = 0, owner: str | None = None) -> None: ...

    async def set_pause(self, p: bool) -> None: ...


@dataclass(frozen=True)
class SoundCue:
    id: int
    start: float
    duration: float | None
    path: str
    media_duration: float
    volume: float = 1.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    loop: bool = False

    @property
    def end(self) -> float | None:
        if self.duration is not None:
            return self.start + self.duration
        if self.loop or self.media_duration <= 0:
            return None
        return self.start + self.media_duration


def load_sound_cues(s: Session, cues: list[Cue]) -> list[SoundCue]:
    """Sound cues of a profile, sorted by (start, id). Missing media files are skipped."""
    out = []
    for c in cues:
        if c.kind != "sound" or not c.sound:
            continue
        try:
            spec = SoundSpec.model_validate(c.sound)
        except ValueError:
            log.warning("cue %s has an invalid sound spec", c.id)
            continue
        m = s.get(Media, spec.media_id)
        if m is None or not Path(m.path).exists():
            continue
        out.append(
            SoundCue(
                id=c.id or 0,
                start=c.start_s,
                duration=c.duration_s,
                path=m.path,
                media_duration=m.duration_s,
                volume=spec.volume,
                fade_in_ms=spec.fade_in_ms,
                fade_out_ms=spec.fade_out_ms,
                loop=spec.loop,
            )
        )
    return sorted(out, key=lambda c: (c.start, c.id))


def active(cues: list[SoundCue], t: float) -> SoundCue | None:
    latest = None
    for c in cues:  # sorted; a handful per show, linear is fine at 100 Hz
        if c.start > t:
            break
        latest = c
    if latest is None:
        return None
    end = latest.end
    return latest if end is None or t < end else None


class SoundLane:
    """Change detection for the fx player, driven from the scheduler tick."""

    def __init__(self) -> None:
        self.playing: SoundCue | None = None
        self._resync = False

    def resync(self) -> None:
        """Seek or loop wrap: re-start the covering sound at the new offset."""
        self._resync = True

    def tick(self, fx: FxPlayer | None, cues: list[SoundCue], t: float) -> None:
        if fx is None:
            return
        cue = active(cues, t)
        resync, self._resync = self._resync, False
        prev = self.playing
        if not resync and (cue.id if cue else None) == (prev.id if prev else None):
            return
        self.playing = cue
        if cue is None:
            if prev is not None:
                # Natural end of a timed window fades; a jump away cuts.
                fade = prev.fade_out_ms if prev.duration is not None and not resync else 0
                spawn(_quiet(fx.stop(fade, owner=OWNER)))
            return
        offset = max(0.0, t - cue.start)
        if cue.loop and cue.media_duration > 0:
            offset %= cue.media_duration
        fade_in = max(0, round(cue.fade_in_ms - offset * 1000))
        spawn(
            _quiet(
                fx.play(
                    cue.path,
                    start=offset,
                    volume=cue.volume,
                    fade_in_ms=fade_in,
                    loop=cue.loop,
                    owner=OWNER,
                )
            )
        )

    def stop(self, fx: FxPlayer | None) -> None:
        """Show ended/stopped: silence a cue sound (soundboard sounds stay)."""
        if self.playing is not None and fx is not None:
            spawn(_quiet(fx.stop(0, owner=OWNER)))
        self.playing = None
        self._resync = False


async def _quiet(coro: Any) -> None:
    try:
        await coro
    except Exception as e:  # noqa: BLE001
        log.warning("fx player command failed: %s", e)
