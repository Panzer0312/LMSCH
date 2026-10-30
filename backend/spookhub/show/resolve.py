"""Pure cue-resolution logic: what should board X show at time t?

Rules (spec §5, plus our resolution of the open cases):
- A cue with a duration is active during [start, start+duration).
- A cue without a duration holds until any later cue *starts* on the same board.
- The active cue with the latest start wins ("most recently started wins"); "all selected
  boards" cues and board-specific cues compete under the same rule.
- If a short cue ends inside a longer duration cue's window, the longer cue resumes. If no cue
  is active, the board shows the profile's base state.

Segments: a cue whose target lists segments of a board drives only those segments there. The
whole-board state ignores segment cues. A segment (mac, seg) resolves with the same rules over
the whole-board cues of mac plus the cues for that segment, so a later whole-board cue covers an
earlier segment cue and vice versa. A segment only gets its own state when a segment cue wins.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from dataclasses import dataclass

from spookhub.models import LightState

SegKey = tuple[str, int]
# Change-detection key for one board: (whole-board cue id, ((seg, cue id), ...)).
BoardKey = tuple[int | None, tuple[tuple[int, int], ...]]


@dataclass(frozen=True)
class CueSpec:
    id: int
    start: float
    duration: float | None
    targets: frozenset[str] | None  # None = all selected boards
    state: LightState
    transition_ms: int | None = None
    # mac -> segment ids. A mac listed here is hit on those segments only, never as a whole.
    segs: dict[str, frozenset[int]] | None = None
    # 'sound' cues live on their own lane (engine fx player) and never drive lights.
    kind: str = "light"

    @property
    def end(self) -> float | None:
        return None if self.duration is None else self.start + self.duration

    def hits(self, mac: str) -> bool:
        """Whole-board hit."""
        if self.segs is not None and mac in self.segs:
            return False
        return self.targets is None or mac in self.targets

    def hits_seg(self, mac: str, seg: int) -> bool:
        """Segment hit: the cue's own segment target, or a whole-board hit on mac."""
        if self.segs is not None and mac in self.segs:
            return seg in self.segs[mac]
        return self.hits(mac)


def _apply(cue: CueSpec | None, base: LightState) -> tuple[int | None, LightState]:
    if cue is None:
        return None, base
    st = base.merged(cue.state)
    if cue.transition_ms is not None:
        st = st.model_copy(update={"transition_ms": cue.transition_ms})
    return cue.id, st


def _active(cues: list[CueSpec] | None, starts: list[float] | None, t: float) -> CueSpec | None:
    if not cues or starts is None:
        return None
    i = bisect.bisect_right(starts, t) - 1
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


def _overlap_pairs(cues: list[CueSpec]) -> list[tuple[int, int]]:
    pairs = []
    for a_i, a in enumerate(cues):
        for b in cues[a_i + 1 :]:
            a_end = a.end
            if a_end is not None and b.start >= a_end:
                break
            if a_end is not None and b.start < a_end:
                pairs.append((a.id, b.id))
    return pairs


class Timeline:
    """Per-board index of cues, sorted by (start, id) for O(log n) lookups at 100 Hz."""

    def __init__(self, cues: Iterable[CueSpec], macs: Iterable[str]) -> None:
        ordered = sorted((c for c in cues if c.kind != "sound"), key=lambda c: (c.start, c.id))
        macs = list(macs)
        self.by_board: dict[str, list[CueSpec]] = {
            m: [c for c in ordered if c.hits(m)] for m in macs
        }
        self._starts = {m: [c.start for c in cs] for m, cs in self.by_board.items()}
        # Only segments some cue targets get their own index; the rest follow the board.
        keys = sorted(
            {(m, sg) for c in ordered if c.segs for m in macs for sg in c.segs.get(m, ())}
        )
        self.by_seg: dict[SegKey, list[CueSpec]] = {
            k: [c for c in ordered if c.hits_seg(*k)] for k in keys
        }
        self._seg_starts = {k: [c.start for c in cs] for k, cs in self.by_seg.items()}
        self.segs_of: dict[str, list[int]] = {}
        for m, sg in keys:
            self.segs_of.setdefault(m, []).append(sg)

    def active(self, mac: str, t: float) -> CueSpec | None:
        return _active(self.by_board.get(mac), self._starts.get(mac), t)

    def active_seg(self, mac: str, seg: int, t: float) -> CueSpec | None:
        """Winning cue for one segment: a whole-board cue or a cue for this segment."""
        key = (mac, seg)
        if key not in self.by_seg:
            return self.active(mac, t)
        return _active(self.by_seg[key], self._seg_starts[key], t)

    def state_at(self, mac: str, t: float, base: LightState) -> tuple[int | None, LightState]:
        """Return (cue id or None for base, effective whole-board state)."""
        return _apply(self.active(mac, t), base)

    def seg_state_at(
        self, mac: str, seg: int, t: float, base: LightState
    ) -> tuple[int | None, LightState]:
        """state_at for one segment; equals state_at when no segment cue wins."""
        return _apply(self.active_seg(mac, seg, t), base)

    def board_at(
        self, mac: str, t: float, base: LightState
    ) -> tuple[BoardKey, LightState, dict[int, LightState]]:
        """Whole-board state plus the states of segments whose own segment cue wins."""
        cue_id, state = self.state_at(mac, t, base)
        segs: dict[int, LightState] = {}
        seg_ids: list[tuple[int, int]] = []
        for sg in self.segs_of.get(mac, ()):
            cue = self.active_seg(mac, sg, t)
            if cue is None or cue.hits(mac):
                continue
            _, segs[sg] = _apply(cue, base)
            seg_ids.append((sg, cue.id))
        return (cue_id, tuple(seg_ids)), state, segs

    def overlaps(self) -> dict[str, list[tuple[int, int]]]:
        """Pairs of cue ids overlapping on each board (for UI warnings)."""
        out: dict[str, list[tuple[int, int]]] = {}
        for mac, cues in self.by_board.items():
            pairs = _overlap_pairs(cues)
            if pairs:
                out[mac] = pairs
        return out

    def seg_overlaps(self) -> dict[str, list[tuple[int, int]]]:
        """Overlapping pairs per targeted segment, keyed "mac:seg"; whole-board pairs omitted."""
        out: dict[str, list[tuple[int, int]]] = {}
        for (mac, sg), cues in self.by_seg.items():
            whole = {(a, b) for a, b in _overlap_pairs(self.by_board.get(mac, []))}
            pairs = [p for p in _overlap_pairs(cues) if p not in whole]
            if pairs:
                out[f"{mac}:{sg}"] = pairs
        return out
