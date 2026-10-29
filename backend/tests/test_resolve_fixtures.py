"""Shared resolver fixtures; frontend/src/timeline/resolve.test.ts runs the same files.

fixtures/resolve/segments/ adds segment targeting: cues carry "segs", probes with "seg" check
one segment, other probes there carry "expect_segs" (segments whose own cue wins).
"""

import json
from pathlib import Path

import pytest

from spookhub.models import LightState
from spookhub.show.resolve import CueSpec, Timeline

ROOT = Path(__file__).parent / "fixtures" / "resolve"
FIXTURES = sorted(ROOT.glob("*.json"))
SEG_FIXTURES = sorted((ROOT / "segments").glob("*.json"))


def naive_active(cues: list[CueSpec], mac: str, t: float, seg: int | None = None) -> int | None:
    """O(n) oracle written from the resolve.py docstring, independent of its bisect logic."""
    hit = (lambda c: c.hits(mac)) if seg is None else (lambda c: c.hits_seg(mac, seg))
    mine = [c for c in cues if hit(c) and c.start <= t]
    best = None
    for c in mine:
        if c.end is not None:
            live = t < c.end
        else:
            # Open-ended holds until any later-started cue on this board starts.
            live = not any((o.start, o.id) > (c.start, c.id) for o in mine)
        if live and (best is None or (c.start, c.id) > (best.start, best.id)):
            best = c
    return None if best is None else best.id


def load_cues(f: dict) -> list[CueSpec]:
    return [
        CueSpec(
            id=c["id"],
            start=c["start"],
            duration=c["duration"],
            targets=None if c["targets"] is None else frozenset(c["targets"]),
            state=LightState(**c["state"]),
            transition_ms=c["transition_ms"],
            segs=(
                None
                if c.get("segs") is None
                else {m: frozenset(ids) for m, ids in c["segs"].items()}
            ),
        )
        for c in f["cues"]
    ]


@pytest.mark.parametrize("path", FIXTURES + SEG_FIXTURES, ids=lambda p: p.stem)
def test_fixture(path: Path):
    f = json.loads(path.read_text())
    cues = load_cues(f)
    tl = Timeline(cues, f["macs"])
    base = LightState(**f["base"])
    for p in f["probes"]:
        seg = p.get("seg")
        if seg is None:
            cue_id, state = tl.state_at(p["mac"], p["t"], base)
        else:
            cue_id, state = tl.seg_state_at(p["mac"], seg, p["t"], base)
        assert cue_id == p["expect_cue"], p
        assert cue_id == naive_active(cues, p["mac"], p["t"], seg), p
        assert state.model_dump(mode="json", exclude_none=True) == p["expect_state"], p
        if "expect_segs" in p:
            (board_id, seg_ids), board_state, seg_states = tl.board_at(p["mac"], p["t"], base)
            assert board_id == cue_id and board_state == state, p
            assert {str(s): c for s, c in seg_ids} == p["expect_segs"], p
            assert set(seg_states) == {int(s) for s in p["expect_segs"]}, p

    if "expect_overlaps" in f:
        got = {m: [list(x) for x in ps] for m, ps in tl.overlaps().items()}
        assert got == f["expect_overlaps"]
    if "expect_seg_overlaps" in f:
        got = {k: [list(x) for x in ps] for k, ps in tl.seg_overlaps().items()}
        assert got == f["expect_seg_overlaps"]


def test_fixture_count():
    assert len(FIXTURES) >= 10
    assert len(SEG_FIXTURES) >= 4
    for path in FIXTURES + SEG_FIXTURES:
        assert json.loads(path.read_text())["name"] == path.stem
    # Whole-board fixtures stay segment-free so pre-segment ports keep reading them unchanged.
    for path in FIXTURES:
        f = json.loads(path.read_text())
        assert all("segs" not in c for c in f["cues"]) and all("seg" not in p for p in f["probes"])
