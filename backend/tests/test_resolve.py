from spookhub.models import LightState
from spookhub.show.resolve import CueSpec, Timeline

A, B = "aa", "bb"
BASE = LightState(on=True, bri=50, fx=0)


def cue(i: int, start: float, dur: float | None = None, macs=None, fx: int = 1) -> CueSpec:
    return CueSpec(
        id=i, start=start, duration=dur,
        targets=None if macs is None else frozenset(macs), state=LightState(fx=fx),
    )  # fmt: skip


def at(tl: Timeline, mac: str, t: float) -> int | None:
    return tl.state_at(mac, t, BASE)[0]


def test_base_before_first_cue_and_open_cue_holds():
    tl = Timeline([cue(1, 2.0)], [A])
    assert at(tl, A, 1.99) is None
    assert at(tl, A, 2.0) == 1
    assert at(tl, A, 999) == 1


def test_duration_returns_to_base():
    tl = Timeline([cue(1, 1.0, 2.0)], [A])
    assert at(tl, A, 2.99) == 1
    assert at(tl, A, 3.0) is None


def test_latest_start_wins_and_longer_duration_cue_resumes():
    tl = Timeline([cue(1, 0.0, 10.0), cue(2, 2.0, 2.0)], [A])
    assert at(tl, A, 1) == 1
    assert at(tl, A, 3) == 2
    assert at(tl, A, 5) == 1  # short cue ended inside long one's window
    assert at(tl, A, 10) is None


def test_open_cue_superseded_does_not_resume():
    tl = Timeline([cue(1, 0.0), cue(2, 2.0, 1.0)], [A])
    assert at(tl, A, 2.5) == 2
    assert at(tl, A, 3.5) is None


def test_all_and_specific_targets_compete_equally():
    tl = Timeline([cue(1, 0.0, macs=None), cue(2, 1.0, macs=[B])], [A, B])
    assert at(tl, A, 1.5) == 1
    assert at(tl, B, 1.5) == 2
    tl2 = Timeline([cue(2, 1.0, macs=[B]), cue(3, 2.0)], [A, B])
    assert at(tl2, B, 2.5) == 3


def test_seek_recomputation_is_position_only():
    cues = [cue(1, 1.0, 1.0), cue(2, 5.0), cue(3, 8.0, 0.5)]
    tl = Timeline(cues, [A])
    # Jumping straight to t=6 must yield the same as playing through to it.
    assert at(tl, A, 6.0) == 2
    assert at(tl, A, 8.2) == 3
    assert at(tl, A, 8.6) is None  # cue 2 superseded by cue 3, so base after it
    assert at(tl, A, 0.5) is None  # seeking backwards


def test_state_merges_base_and_transition():
    c = CueSpec(1, 0.0, None, None, LightState(fx=9, bri=200), transition_ms=500)
    _, st = Timeline([c], [A]).state_at(A, 0.1, BASE)
    assert (st.on, st.bri, st.fx, st.transition_ms) == (True, 200, 9, 500)


def test_overlap_detection():
    tl = Timeline([cue(1, 0.0, 3.0), cue(2, 2.0, macs=[A]), cue(3, 5.0)], [A, B])
    assert tl.overlaps() == {A: [(1, 2)]}
