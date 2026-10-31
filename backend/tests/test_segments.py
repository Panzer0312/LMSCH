"""Phase 2 task 11: WLED segment targeting."""

import asyncio

from spookhub.boards.client import build_payload
from spookhub.boards.registry import BoardRuntime
from spookhub.models import LightState
from spookhub.show.engine import load_active
from spookhub.show.resolve import Timeline
from tests.conftest import wait_for
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_timeline_api import _video_profile

RED, BLUE = [255, 0, 0], [0, 0, 255]


async def _adopt(client, farm):  # noqa: F811
    sim = await farm.add(0)
    r = await client.post("/api/boards/add", json={"ip": f"127.0.0.1:{sim.port}"})
    mac = r.json()["mac"]
    await client.post(f"/api/boards/{mac}/adopt", json={"name": "Porch"})
    b = client.hub.boards.boards[mac]
    await wait_for(lambda: len(b.segments) == 2)
    return sim, mac, b


async def test_segments_in_snapshot_and_persisted(client, farm):  # noqa: F811
    _, mac, b = await _adopt(client, farm)
    r = await client.get("/api/boards")
    snap = next(x for x in r.json() if x["mac"] == mac)
    assert snap["segments"] == [
        {"id": 0, "name": "Left", "start": 0, "stop": 30},
        {"id": 1, "name": "Right", "start": 30, "stop": 60},
    ]
    assert b.seg_ids == [0, 1]


async def test_cue_segs_roundtrip_and_load(client, farm):  # noqa: F811
    _, mac, _ = await _adopt(client, farm)
    cues = [
        {"start_s": 0, "state": {"fx": 1}},
        {
            "start_s": 1,
            "duration_s": 2,
            "target": {"all": False, "macs": [], "segs": {mac: [1]}},
            "state": {"colors": [BLUE]},
        },
    ]
    pid = await _video_profile(client, cues)
    got = (await client.get(f"/api/profiles/{pid}")).json()["cues"]
    assert got[0]["target"] == {"all": True, "macs": [], "groups": [], "segs": None}
    assert got[1]["target"]["segs"] == {mac: [1]}
    prof = load_active(pid)
    assert prof is not None
    by_start = {c.start: c for c in prof.cues}
    assert by_start[0].segs is None
    assert by_start[1].segs == {mac: frozenset({1})}


async def test_two_segment_cues_one_request(client, farm):  # noqa: F811
    """Two segments with their own cues starting together reach the board in one request."""
    sim, mac, _ = await _adopt(client, farm)
    cues = [
        {"start_s": 0, "state": {"fx": 3}},
        {
            "start_s": 2,
            "duration_s": 2,
            "target": {"all": False, "macs": [], "segs": {mac: [0]}},
            "state": {"colors": [RED]},
        },
        {
            "start_s": 2,
            "duration_s": 2,
            "target": {"all": False, "macs": [], "segs": {mac: [1]}},
            "state": {"fx": 9, "colors": [BLUE]},
        },
    ]
    pid = await _video_profile(client, cues)
    eng = client.hub.engine
    prof = load_active(pid)
    assert prof is not None
    await wait_for(lambda: client.hub.boards.boards[mac].queue.idle)
    eng._sched_prof, eng._sched_tl = prof, Timeline(prof.cues, [mac])
    eng._last_cue = {mac: None}
    eng.settings.update(sync_offset_ms=0)
    client.hub.boards.boards[mac].offset_ms = 0
    client.hub.boards.boards[mac].latency_ms = 0

    sim.log.clear()
    eng._tick(2.5)
    await wait_for(lambda: len(sim.log) >= 1)
    eng._tick(2.6)  # same cues: nothing new to send
    await asyncio.sleep(0.2)
    assert len(sim.log) == 1
    seg = {s["id"]: s for s in sim.log[0]["body"]["seg"]}
    assert set(seg) == {0, 1}
    assert seg[0]["col"] == [RED] and seg[0]["fx"] == 3  # unset fields follow the board
    assert seg[1]["col"] == [BLUE] and seg[1]["fx"] == 9
    assert [s["fx"] for s in sim.state["seg"]] == [3, 9]

    # Segment cues end: one request puts both segments back on the board cue.
    sim.log.clear()
    eng._tick(4.5)
    await wait_for(lambda: len(sim.log) >= 1)
    await asyncio.sleep(0.2)
    assert len(sim.log) == 1
    assert [s["fx"] for s in sim.state["seg"]] == [3, 3]


def test_build_payload_segment_states():
    board = LightState(on=True, bri=100, fx=2, colors=[(1, 2, 3)])
    p = build_payload(
        board,
        presets={},
        seg_ids=[0, 1, 2],
        seg_states={1: LightState(on=False, bri=5, colors=[(9, 9, 9)]), 7: LightState(fx=4)},
    )
    assert p["on"] is True and p["bri"] == 100  # board-wide fields stay the board's
    assert p["seg"] == [
        {"id": 0, "fx": 2, "col": [[1, 2, 3]]},
        {"id": 1, "fx": 2, "col": [[9, 9, 9]]},
        {"id": 2, "fx": 2, "col": [[1, 2, 3]]},
        {"id": 7, "fx": 4},
    ]
    # No segment states: identical to the pre-segment payload.
    assert build_payload(board, presets={}, seg_ids=[0, 1]) == build_payload(
        board, presets={}, seg_ids=[0, 1], seg_states={}
    )


def test_build_payload_segment_preset_and_idless_preset_seg():
    presets = {
        "3": {"on": True, "bri": 90, "seg": [{"fx": 5, "col": [[7, 7, 7]]}]},
        "4": {"seg": [{"id": 0, "start": 0, "stop": 10, "n": "x", "fx": 8, "pal": 2}]},
    }
    p = build_payload(
        LightState(preset=3),
        presets=presets,
        seg_ids=[0, 1],
        seg_states={1: LightState(preset=4, sx=40)},
    )
    assert p["bri"] == 90
    assert p["seg"] == [
        {"id": 0, "fx": 5, "col": [[7, 7, 7]]},
        {"id": 1, "fx": 8, "pal": 2, "sx": 40},
    ]


async def test_manual_overrides_segments_and_blackout_drops_them(client):  # noqa: F811
    bm = client.hub.boards

    b = BoardRuntime(mac="m", ip="x", adopted=True, seg_ids=[0, 1])
    b.show_state = LightState(on=True, fx=1)
    b.show_segs = {1: LightState(fx=5), 4: LightState(fx=6)}
    assert bm.desired_segs(b) == {1: LightState(fx=5)}  # unknown segment 4 dropped
    b.manual = LightState(fx=2)
    assert bm.desired_segs(b) == {1: LightState(fx=2)}
    bm.blackout = True
    try:
        assert bm.desired_segs(b) == {}
    finally:
        bm.blackout = False
