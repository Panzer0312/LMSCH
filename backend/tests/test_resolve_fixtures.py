"""Shared resolver fixtures; frontend/src/timeline/resolve.test.ts runs the same files."""

import json
from pathlib import Path

import pytest

from spookhub.models import LightState
from spookhub.show.resolve import CueSpec, Timeline

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "resolve").glob("*.json"))


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
        assert state.model_dump(mode="json", exclude_none=True) == p["expect_state"], p


def test_fixture_count():
    assert len(FIXTURES) >= 10
