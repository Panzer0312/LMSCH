"""Regenerate tests/fixtures/resolve/*.json (fixed seed). `--check` exits 1 if files differ.

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


def build() -> dict[str, str]:
    out = {}
    for f in F.values():
        tl = Timeline(
            [
                CueSpec(
                    c["id"],
                    c["start"],
                    c["duration"],
                    None if c["targets"] is None else frozenset(c["targets"]),
                    LightState(**c["state"]),
                    c["transition_ms"],
                )
                for c in f["cues"]
            ],
            f["macs"],
        )
        for p in f["probes"]:
            cid, st = tl.state_at(p["mac"], p["t"], LightState(**f["base"]))
            if p["expect_cue"] == "AUTO":
                p["expect_cue"] = cid
            assert p["expect_cue"] == cid, (f["name"], p)
            p["expect_state"] = st.model_dump(mode="json", exclude_none=True)
        if f.get("expect_overlaps"):
            f["expect_overlaps"] = {m: [list(p) for p in ps] for m, ps in tl.overlaps().items()}
        out[f["name"]] = json.dumps(f, indent=1) + "\n"
    return out


if __name__ == "__main__":
    files = build()
    if "--check" in sys.argv:
        bad = [
            n
            for n, s in files.items()
            if not (OUT / f"{n}.json").exists() or (OUT / f"{n}.json").read_text() != s
        ]
        extra = [p.stem for p in OUT.glob("*.json") if p.stem not in files]
        if bad or extra:
            print("fixtures out of date:", bad, extra)
            sys.exit(1)
    else:
        OUT.mkdir(parents=True, exist_ok=True)
        for n, s in files.items():
            (OUT / f"{n}.json").write_text(s)
