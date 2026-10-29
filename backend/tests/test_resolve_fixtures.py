"""Shared resolver fixtures; frontend/src/timeline/resolve.test.ts runs the same files."""

import json
from pathlib import Path

import pytest

from spookhub.models import LightState
from spookhub.show.resolve import CueSpec, Timeline

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "resolve").glob("*.json"))


def naive_active(cues: list[CueSpec], mac: str, t: float) -> int | None:
    """O(n) oracle written from the resolve.py docstring, independent of its bisect logic."""
    mine = [c for c in cues if c.hits(mac) and c.start <= t]
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


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture(path: Path):
    f = json.loads(path.read_text())
    cues = [
        CueSpec(
            id=c["id"],
            start=c["start"],
            duration=c["duration"],
            targets=None if c["targets"] is None else frozenset(c["targets"]),
            state=LightState(**c["state"]),
            transition_ms=c["transition_ms"],
        )
        for c in f["cues"]
    ]
    tl = Timeline(cues, f["macs"])
    base = LightState(**f["base"])
    for p in f["probes"]:
        cue_id, state = tl.state_at(p["mac"], p["t"], base)
        assert cue_id == p["expect_cue"], p
        assert cue_id == naive_active(cues, p["mac"], p["t"]), p
        assert state.model_dump(mode="json", exclude_none=True) == p["expect_state"], p

    if "expect_overlaps" in f:
        got = {m: [list(x) for x in ps] for m, ps in tl.overlaps().items()}
        assert got == f["expect_overlaps"]


def test_fixture_count():
    assert len(FIXTURES) >= 10
    for path in FIXTURES:
        assert json.loads(path.read_text())["name"] == path.stem
