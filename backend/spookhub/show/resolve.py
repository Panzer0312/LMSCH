"""Pure cue-resolution logic: what should board X show at time t?

Rules (spec §5, plus our resolution of the open cases):
- A cue with a duration is active during [start, start+duration).
- A cue without a duration holds until any later cue *starts* on the same board.
- The active cue with the latest start wins ("most recently started wins"); "all selected
  boards" cues and board-specific cues compete under the same rule.
- If a short cue ends inside a longer duration cue's window, the longer cue resumes. If no cue
  is active, the board shows the profile's base state.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from dataclasses import dataclass

from spookhub.models import LightState


@dataclass(frozen=True)
class CueSpec:
    id: int
    start: float
    duration: float | None
    targets: frozenset[str] | None  # None = all selected boards
    state: LightState
    transition_ms: int | None = None

    @property
    def end(self) -> float | None:
        return None if self.duration is None else self.start + self.duration

    def hits(self, mac: str) -> bool:
        return self.targets is None or mac in self.targets


class Timeline:
    """Per-board index of cues, sorted by (start, id) for O(log n) lookups at 100 Hz."""

    def __init__(self, cues: Iterable[CueSpec], macs: Iterable[str]) -> None:
        ordered = sorted(cues, key=lambda c: (c.start, c.id))
        self.by_board: dict[str, list[CueSpec]] = {
            m: [c for c in ordered if c.hits(m)] for m in macs
        }
        self._starts = {m: [c.start for c in cs] for m, cs in self.by_board.items()}

    def active(self, mac: str, t: float) -> CueSpec | None:
        cues = self.by_board.get(mac)
        if not cues:
            return None
        i = bisect.bisect_right(self._starts[mac], t) - 1
        if i < 0:
            return None
        latest = cues[i]
        if latest.end is None or t < latest.end:
            return latest
        # Latest-started cue has ended. Open-ended cues before it are superseded by it, so only
        # a still-running duration cue can take over.
        for c in reversed(cues[:i]):
            if c.end is not None and t < c.end:
                return c
        return None

    def state_at(self, mac: str, t: float, base: LightState) -> tuple[int | None, LightState]:
        """Return (cue id or None for base, effective state)."""
        cue = self.active(mac, t)
        if cue is None:
            return None, base
        st = base.merged(cue.state)
        if cue.transition_ms is not None:
            st = st.model_copy(update={"transition_ms": cue.transition_ms})
        return cue.id, st

    def overlaps(self) -> dict[str, list[tuple[int, int]]]:
        """Pairs of cue ids overlapping on each board (for UI warnings)."""
        out: dict[str, list[tuple[int, int]]] = {}
        for mac, cues in self.by_board.items():
            pairs = []
            for a_i, a in enumerate(cues):
                for b in cues[a_i + 1 :]:
                    a_end = a.end
                    if a_end is not None and b.start >= a_end:
                        break
                    if a_end is not None and b.start < a_end:
                        pairs.append((a.id, b.id))
            if pairs:
                out[mac] = pairs
        return out
