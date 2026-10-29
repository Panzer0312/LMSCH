"""Regenerate tests/fixtures/resolve/{,segments/}*.json (fixed seed); `--check` exits 1 on diff.

Cue ids in probes are written by hand (the "AUTO" probes of the random fixture excepted) and
asserted against Timeline while generating; expect_state/expect_overlaps come from Timeline.
Run from backend/: uv run python tests/gen_resolve_fixtures.py [--check]
"""

import json
import random
import sys
from pathlib import Path

from spookhub.models import LightState
from spookhub.show.resolve import CueSpec, Timeline

OUT = Path(__file__).parent / "fixtures" / "resolve"
A, B = "aa", "bb"
BASE = {"on": True, "bri": 50, "fx": 0}


def cue(i, start, dur=None, targets=None, state=None, tr=None):
    state = {"fx": i} if state is None else state
    return dict(id=i, start=start, duration=dur, targets=targets, state=state, transition_ms=tr)


F: dict[str, dict] = {}


def fx(name, cues, probes, base=BASE, macs=(A, B), overlaps=False):
    F[name] = dict(
        name=name,
        macs=list(macs),
        base=base,
        cues=cues,
        probes=[dict(mac=m, t=t, expect_cue=e) for m, t, e in probes],
    )
    if overlaps:
        F[name]["expect_overlaps"] = True


fx(
    "overlap_latest_start_wins",
    [cue(1, 0, 10), cue(2, 2, 10)],
    [(A, 1, 1), (A, 2, 2), (A, 11, 2), (A, 12, None)],
    overlaps=True,
)
fx(
    "duration_end_resumes_longer",
    [cue(1, 0, 10), cue(2, 2, 2)],
    [(A, 3, 2), (A, 4, 1), (A, 9.99, 1), (A, 10, None)],
)
fx(
    "open_ended_superseded",
    [cue(1, 0), cue(2, 2, 1)],
    [(A, 1, 1), (A, 2.5, 2), (A, 3, None), (A, 50, None)],
)
fx(
    "all_vs_board_cue",
    [cue(1, 0, targets=None), cue(2, 1, targets=[B]), cue(3, 3, 1)],
    [(A, 2, 1), (B, 2, 2), (B, 3.5, 3), (A, 3.5, 3), (B, 5, None)],
    overlaps=True,
)
fx(
    "base_when_nothing_active",
    [cue(1, 5, 1)],
    [(A, 0, None), (A, 4.999, None), (B, 6, None), (A, 5.5, 1)],
)
fx(
    "transition_override",
    [cue(1, 1, state={"fx": 5, "transition_ms": 100}, tr=900), cue(2, 3, state={"bri": 9}, tr=0)],
    [(A, 1, 1), (A, 3, 2), (A, 0, None)],
)
fx(
    "equal_starts_id_tiebreak",
    [cue(7, 2, 5), cue(3, 2, 5), cue(5, 2, 1)],
    [(A, 2, 7), (A, 3.5, 7), (A, 5, 7), (A, 7, None)],
)
fx(
    "cue_starts_exactly_at_t",
    [cue(1, 0), cue(2, 2.5, 1)],
    [(A, 2.4999, 1), (A, 2.5, 2), (B, 2.5, 2)],
)
fx(
    "end_boundary_exclusive",
    [cue(1, 0, 4), cue(2, 1, 1)],
    [(A, 1.999, 2), (A, 2, 1), (A, 4, None), (A, 3.999, 1)],
)
fx(
    "merge_semantics",
    [
        cue(1, 0, 20, state={"colors": [[255, 0, 0]], "bri": 200}),
        cue(2, 2, 1, state={"preset": 4}),
        cue(3, 5, 1, state={"on": False}),
        cue(4, 8, state={"colors": [[0, 0, 255], [0, 255, 0]], "sx": 10}),
    ],
    [(A, 1, 1), (A, 2.5, 2), (A, 3.5, 1), (A, 5.5, 3), (A, 6.5, 1), (A, 9, 4)],
    base={"preset": 2, "on": True, "bri": 50, "colors": [[1, 2, 3]]},
)
fx(
    "targeted_multi_board",
    [cue(1, 0, 5, targets=[A, B]), cue(2, 1, 1, targets=[A]), cue(3, 4, targets=[B])],
    [(A, 1.5, 2), (B, 1.5, 1), (A, 3, 1), (B, 4.5, 3), (A, 6, None), (B, 100, 3)],
)
fx(
    "state_field_edges",
    [
        cue(1, 0, 10, state={"fx": 3, "transition_ms": 300}),
        cue(2, 2, 2, state={"bri": 0, "fx": None, "on": None}),
        cue(3, 5, 2, state={"on": False, "transition_ms": 50}, tr=0),
    ],
    [(A, 1, 1), (A, 3, 2), (A, 4.5, 1), (A, 6, 3), (A, 8, 1)],
    base={"on": True, "bri": 90, "fx": 7, "transition_ms": 700},
)
fx(
    "preset_with_cue_transition",
    [
        cue(1, 0, 6, state={"preset": 3}, tr=1200),
        cue(2, 1, 2, state={"preset": 5, "transition_ms": 40}),
    ],
    [(A, 0.5, 1), (A, 2, 2), (A, 4, 1), (A, 6, None)],
    base={"on": True, "bri": 10, "transition_ms": 200},
)
fx(
    "equal_start_highest_id_ended",
    [cue(9, 2, 1), cue(4, 2, 5), cue(6, 2, 3)],
    [(A, 2.5, 9), (A, 3, 6), (A, 4.99, 6), (A, 5, 4), (A, 6.99, 4), (A, 7, None)],
)
fx("unknown_board_and_empty", [], [(A, 0, None), (A, 9, None)])

rng = random.Random(1234)
macs = [A, B, "cc"]
cs = []
for i in range(1, 41):
    cs.append(
        cue(
            i,
            round(rng.uniform(0, 60), 1),
            rng.choice([None, round(rng.uniform(0.2, 15), 1)]),
            rng.choice([None, [A], [B], [A, "cc"]]),
            {"fx": i} if i % 3 else {"bri": i, "on": True},
        )
    )
fx(
    "random_seek_probes",
    cs,
    [(rng.choice(macs), round(rng.uniform(-1, 80), 2), "AUTO") for _ in range(300)],
    macs=macs,
    overlaps=True,
)


# ---------- segment fixtures (fixtures/resolve/segments/) ----------
# Cues carry "segs" ({mac: [seg ids]} or null). Probes with "seg" check that segment
# (Timeline.seg_state_at); probes without it check the whole board and carry "expect_segs"
# ({seg id as string: cue id}) = segments whose own segment cue wins (Timeline.board_at).

SEG_OUT = OUT / "segments"
SF: dict[str, dict] = {}


def scue(i, start, dur=None, targets=None, segs=None, state=None, tr=None):
    c = cue(i, start, dur, targets, state, tr)
    c["segs"] = segs
    return c


def sfx(name, cues, probes, base=BASE, macs=(A, B), overlaps=False):
    SF[name] = dict(
        name=name,
        macs=list(macs),
        base=base,
        cues=cues,
        probes=[
            dict(mac=m, t=t, expect_cue=e, **({} if sg is None else {"seg": sg}))
            for m, t, e, sg in probes
        ],
    )
    if overlaps:
        SF[name]["expect_overlaps"] = True
        SF[name]["expect_seg_overlaps"] = True


sfx(
    "seg_overlap_board_and_segment",
    [
        scue(1, 0, 10),
        scue(2, 2, 3, targets=[], segs={A: [1]}, state={"colors": [[0, 0, 255]]}),
        scue(3, 4, 2, targets=[A]),
        scue(4, 7, None, targets=[], segs={A: [0]}, state={"colors": [[255, 0, 0]]}),
        scue(5, 9),
    ],
    [
        (A, 3, 1, None),
        (A, 3, 2, 1),
        (A, 3, 1, 0),
        (A, 4.5, 3, 1),
        (A, 5.5, 3, 1),
        (A, 6.5, 1, 1),
        (A, 8, 1, None),
        (A, 8, 4, 0),
        (A, 9.5, 5, 0),
        (B, 3, 1, None),
        (B, 3, 1, 1),
    ],
    overlaps=True,
)
sfx(
    "seg_two_segments_different_cues",
    [
        scue(1, 0, 10, state={"fx": 1, "colors": [[255, 160, 0]]}),
        scue(2, 1, 5, targets=[], segs={A: [0]}, state={"colors": [[255, 0, 0]]}),
        scue(3, 2, 2, targets=[], segs={A: [1]}, state={"fx": 9, "colors": [[0, 0, 255]]}),
        scue(4, 3, None, targets=[], segs={A: [0, 1]}, state={"bri": 7}, tr=250),
    ],
    [
        (A, 1.5, 1, None),
        (A, 2.5, 1, None),
        (A, 2.5, 2, 0),
        (A, 2.5, 3, 1),
        (A, 3.5, 4, 0),
        (A, 3.5, 4, 1),
        (A, 7, 1, None),
        (A, 12, None, None),
        (A, 12, 4, 1),
    ],
    overlaps=True,
)
sfx(
    "seg_mixed_targets",
    [
        scue(1, 0, 5, targets=None, segs={A: [1]}, state={"fx": 11}),
        scue(2, 1, 5, targets=[A], segs={B: [0]}, state={"fx": 12}),
        scue(3, 2, 1, targets=[], segs={A: [], "zz": [0]}, state={"fx": 13}),
    ],
    [
        (A, 0.5, None, None),
        (A, 0.5, 1, 1),
        (A, 0.5, None, 0),
        (B, 0.5, 1, None),
        (A, 1.5, 2, None),
        (A, 1.5, 2, 1),
        (B, 1.5, 1, None),
        (B, 1.5, 2, 0),
        (B, 1.5, 1, 1),
        (A, 2.5, 2, 1),
        (B, 5.5, 2, 0),
        (B, 5.5, None, None),
    ],
)
sfx(
    "seg_duration_resume",
    [
        scue(1, 0, 10, targets=[], segs={A: [1]}, state={"fx": 21}),
        scue(2, 2, 1, state={"fx": 22}),
        scue(3, 4, None, targets=[], segs={A: [0]}, state={"fx": 23}),
        scue(4, 5, 1, targets=[A], state={"fx": 24}),
        scue(5, 6, 1, targets=[], segs={A: [1]}, state={"fx": 25}, tr=0),
    ],
    [
        (A, 1, 1, 1),
        (A, 2.5, 2, 1),
        (A, 3.5, 1, 1),
        (A, 4.5, 3, 0),
        (A, 4.5, None, None),
        (A, 5.5, 4, 0),
        (A, 5.5, 4, 1),
        (A, 6.2, 5, 1),
        (A, 6.2, None, 0),
        (A, 7.5, 1, 1),
        (A, 10, None, 1),
        (A, 10, None, None),
    ],
)

srng = random.Random(4321)
scs = []
for i in range(1, 41):
    segs = srng.choice([None, None, {A: [0]}, {A: [1]}, {A: [0, 1]}, {B: [1]}, {A: [1], B: [0]}])
    scs.append(
        scue(
            i,
            round(srng.uniform(0, 60), 1),
            srng.choice([None, round(srng.uniform(0.2, 15), 1)]),
            srng.choice([None, [A], [B], []]),
            segs,
            {"fx": i} if i % 3 else {"colors": [[i, 0, 0]], "sx": i},
        )
    )
sfx(
    "seg_random_seek_probes",
    scs,
    [
        (srng.choice([A, B]), round(srng.uniform(-1, 80), 2), "AUTO", srng.choice([None, 0, 1]))
        for _ in range(300)
    ],
    overlaps=True,
)


def spec(c: dict) -> CueSpec:
    segs = c.get("segs")
    return CueSpec(
        c["id"],
        c["start"],
        c["duration"],
        None if c["targets"] is None else frozenset(c["targets"]),
        LightState(**c["state"]),
        c["transition_ms"],
        None if segs is None else {m: frozenset(ids) for m, ids in segs.items()},
    )


def build(fixtures: dict[str, dict]) -> dict[str, str]:
    out = {}
    for f in fixtures.values():
        tl = Timeline([spec(c) for c in f["cues"]], f["macs"])
        base = LightState(**f["base"])
        for p in f["probes"]:
            if "seg" in p:
                cid, st = tl.seg_state_at(p["mac"], p["seg"], p["t"], base)
            else:
                cid, st = tl.state_at(p["mac"], p["t"], base)
            if p["expect_cue"] == "AUTO":
                p["expect_cue"] = cid
            assert p["expect_cue"] == cid, (f["name"], p)
            p["expect_state"] = st.model_dump(mode="json", exclude_none=True)
            if fixtures is SF and "seg" not in p:
                (_, seg_ids), _, _ = tl.board_at(p["mac"], p["t"], base)
                p["expect_segs"] = {str(sg): c for sg, c in seg_ids}
        if f.get("expect_overlaps"):
            f["expect_overlaps"] = {m: [list(p) for p in ps] for m, ps in tl.overlaps().items()}
        if f.get("expect_seg_overlaps"):
            f["expect_seg_overlaps"] = {
                k: [list(p) for p in ps] for k, ps in tl.seg_overlaps().items()
            }
        out[f["name"]] = json.dumps(f, indent=1) + "\n"
    return out


if __name__ == "__main__":
    bad: list[str] = []
    for fixtures, out_dir in ((F, OUT), (SF, SEG_OUT)):
        files = build(fixtures)
        if "--check" in sys.argv:
            bad += [
                n
                for n, s in files.items()
                if not (out_dir / f"{n}.json").exists() or (out_dir / f"{n}.json").read_text() != s
            ]
            bad += [p.stem for p in out_dir.glob("*.json") if p.stem not in files]
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            for n, s in files.items():
                (out_dir / f"{n}.json").write_text(s)
    if bad:
        print("fixtures out of date:", bad)
        sys.exit(1)
